from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from yt_player.core import SettingsStore, Task, TaskStore
from yt_player.processes import Tools
from yt_player.ui import MainWindow


def test_window_settings_and_validation(qtbot, tmp_path):
    settings = SettingsStore(tmp_path)
    settings.value.check_updates = False
    window = MainWindow(Tools(tmp_path), settings, TaskStore(tmp_path))
    qtbot.addWidget(window)
    window.show()
    window.url.setText("not a youtube URL")
    window.submit.click()
    assert not window.store.tasks
    window.settings_button.click()
    assert window.dialog.isVisible()
    window.dialog.quality.setCurrentIndex(4)
    assert settings.value.quality == "720"
    window.dialog.close()
    window.close()
    assert SettingsStore(tmp_path).value.quality == "720"


def test_keyboard_fullscreen_and_live_settings(qtbot, tmp_path):
    settings = SettingsStore(tmp_path)
    settings.value.check_updates = False
    window = MainWindow(Tools(tmp_path), settings, TaskStore(tmp_path))
    qtbot.addWidget(window)
    window.show()
    calls = []
    window.player.seek_relative = calls.append
    window.activateWindow()
    window.pause.setFocus()
    qtbot.waitUntil(window.pause.hasFocus)
    qtbot.keyClick(window.pause, Qt.Key.Key_Right)
    qtbot.keyClick(window.pause, Qt.Key.Key_Left, Qt.KeyboardModifier.ShiftModifier)
    assert calls == [5, -30]
    window.url.setFocus()
    qtbot.keyClick(window.url, Qt.Key.Key_Right)
    assert calls == [5, -30]
    window._settings()
    window.dialog.seek_step.setValue(45)
    assert settings.value.seek_seconds == 45
    assert settings.value.seek_large_seconds == 46
    window.dialog.seek_large_step.setValue(90)
    window.dialog.preview_mode.setCurrentIndex(1)
    window.dialog.close()
    window._input_action("seek-forward-large")
    assert calls[-1] == 90
    window.resize(800, 600)
    qtbot.wait(20)
    geometry = window.geometry()
    qtbot.mouseDClick(window.video, Qt.MouseButton.LeftButton)
    assert window.fullscreen
    qtbot.mouseDClick(window.video, Qt.MouseButton.LeftButton)
    assert not window.fullscreen
    assert window.size() == geometry.size()
    window.close()


def test_video_click_toggles_once_and_double_click_preserves_playback(qtbot, tmp_path):
    settings = SettingsStore(tmp_path)
    settings.value.check_updates = False
    window = MainWindow(Tools(tmp_path), settings, TaskStore(tmp_path))
    qtbot.addWidget(window)
    window.show()
    window.activateWindow()
    qtbot.waitUntil(window.isActiveWindow)
    window.player.has_file = window.player.ready = True
    calls = []
    window.controller.toggle_playback = lambda: calls.append("toggle")
    interval = QApplication.doubleClickInterval() + 100
    qtbot.mouseClick(window.video, Qt.MouseButton.LeftButton)
    assert calls == []
    qtbot.waitUntil(lambda: calls == ["toggle"], timeout=interval + 1000)
    qtbot.mouseClick(window.video, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: len(calls) == 2, timeout=interval + 1000)
    # Real Qt double-click sequence: first click, double press, final release.
    qtbot.mouseClick(window.video, Qt.MouseButton.LeftButton)
    qtbot.mouseDClick(window.video, Qt.MouseButton.LeftButton)
    qtbot.mouseRelease(window.video, Qt.MouseButton.LeftButton)
    assert window.fullscreen
    qtbot.wait(interval)
    assert len(calls) == 2
    window._escape()
    qtbot.mouseClick(window.video, Qt.MouseButton.RightButton)
    qtbot.wait(interval)
    assert len(calls) == 2
    qtbot.mouseClick(window.video, Qt.MouseButton.LeftButton)
    window.player.generation += 1  # A new video or stop invalidates pending clicks.
    qtbot.wait(interval)
    assert len(calls) == 2
    window.player.has_file = False
    qtbot.mouseClick(window.video, Qt.MouseButton.LeftButton)
    qtbot.wait(interval)
    assert len(calls) == 2
    window.close()


def test_sidebar_order_selection_clear_and_small_window(qtbot, tmp_path):
    settings, store = SettingsStore(tmp_path), TaskStore(tmp_path)
    settings.value.check_updates = False
    tasks = []
    for index in range(23):
        task = Task.create("url", str(index), "1080", tmp_path)
        task.title = f"{index} 긴 한글 영상 제목 " + "긴 제목 " * 30
        task.state = "cancelled"
        task.list_order = index
        store.tasks[task.id] = task
        tasks.append(task)
    window = MainWindow(Tools(tmp_path), settings, store)
    qtbot.addWidget(window)
    window.show()
    # The offscreen backend's screen is 800px wide; geometry restoration clamps
    # larger windows to that screen. Use a width which fits for this assertion.
    window.resize(800, 600)
    qtbot.wait(20)
    window._set_sidebar_width(280)
    window._sidebar_resized(280, 1)
    assert settings.value.sidebar_width == 280
    window._fullscreen()
    window._escape()
    assert abs(window.splitter.sizes()[0] - 280) <= 2
    window.resize(800, 600)
    qtbot.wait(30)
    assert window.width() == 800
    assert window.task_list.count() == 20
    assert window.list_ids == [t.id for t in reversed(tasks[3:])]
    assert not window.cancel.isEnabled() and not window.retry.isEnabled()
    window.select_task(tasks[-2].id)
    assert window._selected() == tasks[-2].id
    assert window.retry.isEnabled() and not window.cancel.isEnabled()
    before = window.list_ids.copy()
    tasks[-1].progress = 65
    window.refresh_task(tasks[-1].id)
    assert window.list_ids == before and window._selected() == tasks[-2].id
    window.controller.current_id = tasks[-1].id
    window.refresh_task(tasks[-1].id)
    assert window.title.text() == tasks[-1].title
    assert not window.stop.text() and not window.stop.icon().isNull()
    assert window.stop.accessibleName() == "재생 중지"
    width = window.splitter.sizes()[0]
    window._fullscreen()
    assert not window.sidebar.isVisible() and window.stop.isVisible()
    window._escape()
    assert window.sidebar.isVisible()
    assert abs(window.splitter.sizes()[0] - width) <= 2
    window.clear_button.click()
    assert window.task_list.count() == 0 and window._selected() is None
    assert not window.cancel.isEnabled() and not window.retry.isEnabled()
    assert window.title.text() == tasks[-1].title
    window.refresh_task(tasks[-1].id)
    assert window.task_list.count() == 0
    assert not TaskStore(tmp_path).recent_tasks()
    # Reopening an existing task restores exactly one item, at the top.
    store.show_recent(tasks[0])
    window.select_task(tasks[0].id)
    assert window.list_ids == [tasks[0].id]
    window.grab().save(str(tmp_path / "sidebar-small.png"))
    window.close()
