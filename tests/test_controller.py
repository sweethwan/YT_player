from pathlib import Path

from PySide6.QtCore import QObject, Signal

import yt_player.controller as module
from yt_player.controller import Controller
from yt_player.core import SettingsStore, Task, TaskStore
from yt_player.downloads import DownloadWorker
from yt_player.processes import Tools


class FakePlayer(QObject):
    started = Signal(str)
    failed = Signal(str, str)
    idle_changed = Signal()
    status = Signal(str)

    def __init__(self, tools, window_id, parent):
        super().__init__(parent)
        self.busy = False
        self.task_id = ""
        self.process = None
        self.calls = []

    def play(self, task, local_file=""):
        self.task_id = task.id
        self.process = object()
        self.calls.append((task.id, local_file))

    def stop(self):
        self.process = None


def setup_controller(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "Player", FakePlayer)
    settings, store = SettingsStore(tmp_path), TaskStore(tmp_path)
    controller = Controller(Tools(tmp_path), settings, store, 1)
    return controller, settings, store


def add_task(store, root, state="completed"):
    task = Task.create("url", "video", "1080", root)
    store.tasks[task.id] = task
    folder = store.prepare(task)
    video = folder / "video.mkv"
    video.write_bytes(b"video")
    task.state, task.file, task.file_size = state, str(video), 5
    return task


def test_delete_waits_for_new_playback_and_never_touches_archive(qapp, tmp_path, monkeypatch):
    controller, _settings, store = setup_controller(tmp_path, monkeypatch)
    archive = add_task(store, tmp_path)
    previous = add_task(store, tmp_path)
    current = add_task(store, tmp_path)
    controller.last_played_id = previous.id
    controller.current_id = current.id
    controller.transition_delete[current.id] = True
    controller.player.play(current)
    controller._play_failed(current.id, "network failure")
    assert Path(previous.folder).exists()
    controller._played(current.id)
    assert previous.state == "deleted"
    assert Path(archive.folder).exists()
    assert Path(current.folder).exists()


def test_off_retains_previous_and_ignores_stale_playback_event(qapp, tmp_path, monkeypatch):
    controller, _settings, store = setup_controller(tmp_path, monkeypatch)
    previous, current = add_task(store, tmp_path), add_task(store, tmp_path)
    controller.last_played_id = previous.id
    controller.current_id = current.id
    controller.transition_delete[current.id] = False
    controller._played(previous.id)
    assert controller.last_played_id == previous.id
    controller._played(current.id)
    assert Path(previous.folder).exists()


def test_stream_failure_falls_back_only_for_current_task(qapp, tmp_path, monkeypatch):
    controller, _settings, store = setup_controller(tmp_path, monkeypatch)
    old, current = add_task(store, tmp_path), add_task(store, tmp_path)
    controller.current_id = current.id
    controller._play_failed(old.id, "old error")
    assert not controller.player.calls
    controller._play_failed(current.id, "stream error")
    assert controller.player.calls == [(current.id, current.file)]


def test_deleted_task_cannot_be_retried(qapp, tmp_path, monkeypatch):
    controller, _settings, store = setup_controller(tmp_path, monkeypatch)
    task = add_task(store, tmp_path)
    controller.delete_task(task.id)
    controller.retry(task.id)
    assert not controller.probes


def test_stop_prevents_automatic_fallback(qapp, tmp_path, monkeypatch):
    controller, _settings, store = setup_controller(tmp_path, monkeypatch)
    task = add_task(store, tmp_path)
    controller.current_id = task.id
    controller.play_requested = True
    controller.wait_local.add(task.id)
    controller.stop_playback()
    controller._downloaded(task.id)
    assert not controller.player.calls


def test_corrupt_local_playback_does_not_loop_after_start(qapp, tmp_path, monkeypatch):
    controller, _settings, store = setup_controller(tmp_path, monkeypatch)
    task = add_task(store, tmp_path)
    controller.current_id = task.id
    controller._play(task, task.file)
    controller._played(task.id)
    controller._play_failed(task.id, "decode error")
    assert controller.player.calls == [(task.id, task.file)]
    assert task.id not in controller.wait_local


def test_clear_cancels_all_including_hidden_jobs_and_keeps_playback(qtbot, tmp_path, monkeypatch):
    controller, _settings, store = setup_controller(tmp_path, monkeypatch)
    tasks = [add_task(store, tmp_path, "queued") for _ in range(24)]
    current = tasks[0]
    controller.current_id = current.id
    controller.player.play(current, current.file)
    playing_process = controller.player.process
    for task in tasks[:2]:
        task.state = "downloading"
        worker = DownloadWorker(controller.tools, store, task, controller.downloads)
        worker.finished.connect(controller.downloads._finished)
        controller.downloads.active[task.id] = worker
    controller.downloads.pending.extend(t.id for t in tasks[2:-1])

    class Probe(QObject):
        stopped = False

        def stop(self):
            self.stopped = True

    probe = Probe(controller)
    tasks[-1].state = "analyzing"
    controller.probes[tasks[-1].id] = probe
    controller.clear_list()
    assert not controller.downloads.busy and not controller.probes
    assert probe.stopped
    assert controller.player.process is playing_process
    assert controller.current_id == current.id
    assert all(t.state == "cancelled" for t in tasks)
    assert all(Path(t.file).read_bytes() == b"video" for t in tasks)
    assert not store.recent_tasks()
    controller._metadata(tasks[-1].id, {"title": "late result"})
    controller._metadata_failed(tasks[-1].id, "late error")
    qtbot.wait(10)  # Deferred queue pumps cannot restart cancelled jobs.
    assert not controller.downloads.busy
    assert tasks[-1].state == "cancelled"
    assert not TaskStore(tmp_path).recent_tasks()


def test_reopen_same_playing_file_restores_and_saves_list(qapp, tmp_path, monkeypatch):
    controller, _settings, store = setup_controller(tmp_path, monkeypatch)
    task = add_task(store, tmp_path)
    task.url = "https://youtu.be/BaW_jenozKc"
    task.video_id = "BaW_jenozKc"
    controller.current_id = task.id
    controller.player.play(task, task.file)
    controller.clear_list()
    monkeypatch.setattr(controller.tools, "missing", lambda: [])
    controller.open(task.url)
    assert len(controller.player.calls) == 1
    assert len(store.tasks) == 1
    assert [t.id for t in TaskStore(tmp_path).recent_tasks()] == [task.id]
