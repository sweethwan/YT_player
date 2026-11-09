import json

import pytest
from PySide6.QtCore import QObject, Signal

import yt_player.downloads as downloads
from yt_player.core import Task, TaskStore
from yt_player.downloads import DownloadQueue, DownloadWorker, validate_metadata
from yt_player.processes import Tools


@pytest.mark.parametrize(
    "patch",
    [
        {"is_live": True},
        {"live_status": "is_upcoming"},
        {"live_status": "post_live"},
        {"availability": "needs_auth"},
        {"_type": "playlist"},
        {"formats": []},
        {"id": "wrong"},
    ],
)
def test_metadata_rejects_out_of_scope(patch):
    info = {"id": "video", "formats": [{"format_id": "1"}], **patch}
    with pytest.raises(ValueError):
        validate_metadata(info, "video")


def test_queue_limit_fifo_cancel_and_no_restart_on_shutdown(qapp, tmp_path, monkeypatch, qtbot):
    started = []

    class FakeWorker(QObject):
        changed = Signal(str)
        finished = Signal(str)

        def __init__(self, tools, store, task, parent):
            super().__init__(parent)
            self.task = task

        def start(self):
            self.task.state = "downloading"
            started.append(self.task.id)

        def cancel(self):
            self.task.state = "cancelled"
            self.finished.emit(self.task.id)

    monkeypatch.setattr(downloads, "DownloadWorker", FakeWorker)
    store = TaskStore(tmp_path)
    queue = DownloadQueue(Tools(tmp_path), store)
    tasks = [Task.create("url", str(i), "1080", tmp_path) for i in range(4)]
    for task in tasks:
        store.tasks[task.id] = task
        queue.enqueue(task)
    assert started == [tasks[0].id, tasks[1].id]
    queue.cancel(tasks[0].id)
    qtbot.waitUntil(lambda: len(started) == 3)
    assert started[-1] == tasks[2].id
    queue.shutdown()
    qtbot.wait(10)
    assert len(started) == 3
    assert not queue.busy


def test_structured_progress_and_final_filename(qapp, tmp_path):
    task = Task.create("url", "video", "1080", tmp_path)
    worker = DownloadWorker(Tools(tmp_path), TaskStore(tmp_path), task)
    worker._line('YTP_PROGRESS:{"downloaded_bytes":50,"total_bytes":100,"speed":2048,"eta":3}')
    assert task.progress == 50
    assert task.speed == 2048
    worker._line("YTP_POST:{}")
    assert task.state == "merging"
    worker._line("YTP_FILE:" + json.dumps(str(tmp_path / "한글 영상.mkv")))
    assert worker.file.endswith("한글 영상.mkv")
    worker.process.tree.close()
