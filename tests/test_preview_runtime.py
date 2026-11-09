"""Actual FFmpeg frames and mpv input bindings against a deterministic color clip."""

import os
import subprocess
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from yt_player.core import SettingsStore, Task, TaskStore
from yt_player.previews import PreviewService
from yt_player.processes import Tools
from yt_player.ui import MainWindow

pytestmark = [pytest.mark.runtime, pytest.mark.skipif(os.name != "nt", reason="Windows only")]
ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def color_clip(tmp_path):
    tools = Tools(tmp_path)
    tools.bin = ROOT / "tools"
    if tools.missing():
        pytest.skip("Prepare tools first")
    clip = tmp_path / "색상 테스트.mkv"
    args = [tools.path("ffmpeg"), "-v", "error"]
    for color in ["red", "blue", "green"]:
        args += ["-f", "lavfi", "-i", f"color=c={color}:s=320x180:r=10:d=20"]
    args += [
        "-filter_complex",
        "[0:v][1:v][2:v]concat=n=3:v=1:a=0",
        "-c:v",
        "mpeg4",
        "-g",
        "10",
        str(clip),
    ]
    subprocess.run(args, check=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
    return tools, clip


def test_real_frame_extraction_follows_hover_and_cancels(qtbot, tmp_path, color_clip):
    tools, clip = color_clip
    settings, store = SettingsStore(tmp_path), TaskStore(tmp_path)
    settings.value.thumbnail_mode = "precise"
    service = PreviewService(tools, settings, store)
    task = Task.create("local", "video", "1080", tmp_path)
    service.set_source(task, str(clip))
    results = []
    service.ready.connect(lambda *args: results.append(args))
    try:
        service.request(5.25, 60)
        qtbot.waitUntil(lambda: len(results) == 1, timeout=15000)
        image = results[-1][2]
        assert image is not None, results[-1][3]
        red = image.pixelColor(image.width() // 2, image.height() // 2)
        assert red.red() > 180 and red.blue() < 50
        service.request(22.125, 60)
        qtbot.waitUntil(lambda: len(results) == 2, timeout=15000)
        image = results[-1][2]
        assert image is not None, results[-1][3]
        blue = image.pixelColor(image.width() // 2, image.height() // 2)
        assert blue.blue() > 180 and blue.red() < 50
        service.request(60, 60, fps=10)
        qtbot.waitUntil(lambda: len(results) == 3, timeout=15000)
        image = results[-1][2]
        assert image is not None, results[-1][3]
        green = image.pixelColor(image.width() // 2, image.height() // 2)
        assert green.green() > 80 and green.red() < 50
        service.request(42.125, 60)
        service.cancel()
        qtbot.wait(200)
        assert len(results) == 3
        assert service.process is None and not service.busy
    finally:
        service.cancel()


def test_native_mpv_bindings_seek_once_and_toggle_parent_fullscreen(qtbot, tmp_path, color_clip):
    tools, clip = color_clip
    settings, store = SettingsStore(tmp_path), TaskStore(tmp_path)
    settings.value.check_updates = False
    settings.value.muted = True
    task = Task.create("local", "video", "1080", tmp_path)
    store.tasks[task.id] = task
    window = MainWindow(tools, settings, store)
    qtbot.addWidget(window)
    window.show()
    player = window.player
    errors, actions = [], []
    player.failed.connect(lambda *args: errors.append(args))
    player.input_action.connect(actions.append)
    try:
        window.controller.current_id = task.id
        window.controller._play(task, str(clip))
        qtbot.waitUntil(lambda: player.ready or bool(errors), timeout=20000)
        assert not errors
        player.command(["set_property", "pause", True])
        qtbot.waitUntil(lambda: player.paused)
        player.command(["keypress", "RIGHT"])
        qtbot.waitUntil(lambda: player.current_position >= 5, timeout=5000)
        assert 5 <= player.current_position < 6
        player.command(["keypress", "Shift+RIGHT"])
        qtbot.waitUntil(lambda: player.current_position >= 35, timeout=5000)
        assert 35 <= player.current_position < 36
        assert actions == ["seek-forward", "seek-forward-large"]
        assert player.paused
        player.command(["keypress", "MBTN_LEFT"])
        qtbot.waitUntil(lambda: not player.paused, timeout=5000)
        player.command(["keypress", "MBTN_LEFT"])
        qtbot.waitUntil(lambda: player.paused, timeout=5000)
        player.command(["keypress", "MBTN_LEFT"])
        player.command(["keypress", "MBTN_LEFT_DBL"])
        qtbot.waitUntil(lambda: window.fullscreen)
        # Include the trailing native release after a double-click.
        player.command(["keypress", "MBTN_LEFT"])
        qtbot.wait(QApplication.doubleClickInterval() + 150)
        assert player.paused
        player.command(["keypress", "ESC"])
        qtbot.waitUntil(lambda: not window.fullscreen)
        player.command(["keypress", "Shift+LEFT"])
        qtbot.waitUntil(lambda: player.current_position < 6, timeout=5000)
        player.command(["keypress", "Shift+LEFT"])
        qtbot.waitUntil(lambda: player.current_position == 0, timeout=5000)
        assert player.paused
        qtbot.waitUntil(lambda: window.updater.worker is None, timeout=25000)
    finally:
        window.controller.shutdown()
        qtbot.waitUntil(lambda: window.updater.worker is None, timeout=25000)
        window.close()


def test_native_hover_popup_changes_frame_without_seeking(qtbot, tmp_path, color_clip):
    tools, clip = color_clip
    settings, store = SettingsStore(tmp_path), TaskStore(tmp_path)
    settings.value.check_updates = False
    settings.value.muted = True
    task = Task.create("local", "video", "1080", tmp_path)
    store.tasks[task.id] = task
    window = MainWindow(tools, settings, store)
    qtbot.addWidget(window)
    window.show()
    window.activateWindow()
    try:
        window.controller.current_id = task.id
        window.controller._play(task, str(clip))
        qtbot.waitUntil(lambda: window.player.ready, timeout=20000)
        window.player.command(["set_property", "pause", True])
        qtbot.waitUntil(lambda: window.player.paused)
        position = window.player.current_position
        window.activateWindow()
        window.pause.setFocus()
        qtbot.waitUntil(window.isActiveWindow)

        def hover(x):
            point = QPoint(x, 5)
            QCoreApplication.sendEvent(
                window.seek,
                QMouseEvent(
                    QEvent.Type.MouseMove,
                    QPointF(point),
                    QPointF(window.seek.mapToGlobal(point)),
                    Qt.MouseButton.NoButton,
                    Qt.MouseButton.NoButton,
                    Qt.KeyboardModifier.NoModifier,
                ),
            )

        # Deliver directly to Qt: native QTest cursor movement is not delivered in
        # some unattended Windows desktop sessions. The real decoder/popup still run.
        hover(window.seek.width() // 10)
        qtbot.waitUntil(
            lambda: (
                window.preview_popup.picture.pixmap() is not None
                and not window.preview_popup.picture.pixmap().isNull()
            ),
            timeout=15000,
        )
        red = window.preview_popup.picture.pixmap().toImage().pixelColor(100, 50)
        assert red.red() > 180 and red.blue() < 50
        assert window.preview_popup.isVisible()
        window.preview_popup.grab().save(str(tmp_path / "red-preview.png"))
        hover(window.seek.width() // 2)
        qtbot.waitUntil(
            lambda: (
                window.preview_popup.picture.pixmap() is not None
                and not window.preview_popup.picture.pixmap().isNull()
            ),
            timeout=15000,
        )
        blue = window.preview_popup.picture.pixmap().toImage().pixelColor(100, 50)
        assert blue.blue() > 180 and blue.red() < 50
        assert abs(window.player.current_position - position) < 0.1
        window.controller.previews.cancel()
        assert not window.preview_popup.isVisible()
        qtbot.waitUntil(lambda: window.updater.worker is None, timeout=25000)
    finally:
        window.controller.shutdown()
        qtbot.waitUntil(lambda: window.updater.worker is None, timeout=25000)
        window.close()
