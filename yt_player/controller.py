"""Coordinate independent playback/download states and safe transition cleanup."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Signal

from .core import SettingsStore, Task, TaskStore, normalize_url
from .downloads import DownloadQueue, MetadataProbe
from .player import Player
from .previews import PreviewService
from .processes import Tools


class Controller(QObject):
    changed = Signal(str)
    message = Signal(str)
    selected = Signal(str)
    activity = Signal()
    list_cleared = Signal()

    def __init__(
        self, tools: Tools, settings: SettingsStore, store: TaskStore, window_id: int, parent=None
    ):
        super().__init__(parent)
        self.tools, self.settings, self.store = tools, settings, store
        self.previews = PreviewService(tools, settings, store, self)
        self.previews.activity.connect(self.activity)
        self.downloads = DownloadQueue(tools, store, self)
        self.player = Player(tools, window_id, self)
        self.downloads.changed.connect(self.changed)
        self.downloads.completed.connect(self._downloaded)
        self.downloads.idle_changed.connect(self.activity)
        self.player.started.connect(self._played)
        self.player.failed.connect(self._play_failed)
        self.player.idle_changed.connect(self.activity)
        self.current_id = ""
        self.last_played_id = ""
        self.probes: dict[str, MetadataProbe] = {}
        self.transition_delete: dict[str, bool] = {}
        self.wait_local: set[str] = set()
        self.local_attempted: set[str] = set()
        self.play_requested = False
        self.closing = False

    def busy(self):
        return bool(self.probes or self.downloads.busy or self.player.busy or self.previews.busy)

    def open(self, raw: str):
        try:
            url, video_id = normalize_url(raw)
        except ValueError as exc:
            self.message.emit(str(exc))
            return
        missing = self.tools.missing()
        if missing:
            self.message.emit(
                "필수 도구가 없습니다: "
                + ", ".join(missing)
                + "\nscripts/bootstrap_tools.py를 먼저 실행하세요."
            )
            return
        if self.current_id and self.current_id in self.probes:
            previous_probe = self.current_id
            self._stop_probe(previous_probe)
            self.store.tasks[previous_probe].state = "cancelled"
            self.changed.emit(previous_probe)
        quality = self.settings.value.quality
        candidates = [
            t
            for t in reversed(list(self.store.tasks.values()))
            if t.video_id == video_id
            and t.quality == quality
            and not t.delete_pending
            and t.state != "deleted"
        ]
        task = next((t for t in candidates if self.store.local_file(t)), None)
        if task is None:
            task = next(
                (
                    t
                    for t in candidates
                    if t.id in self.downloads.active or t.id in self.downloads.pending
                ),
                None,
            )
        if task is None:
            task = Task.create(url, video_id, quality, Path(self.settings.value.download_dir))
            self.store.tasks[task.id] = task
        already_playing = self.current_id == task.id and self.player.process is not None
        self.current_id = task.id
        self.play_requested = True
        self.wait_local.discard(task.id)
        self.local_attempted.discard(task.id)
        self.transition_delete[task.id] = self.settings.value.auto_delete
        self.store.show_recent(task)
        self.selected.emit(task.id)
        if already_playing:
            return
        if local := self.store.local_file(task):
            self._play(task, str(local))
        elif task.id in self.downloads.active or task.id in self.downloads.pending:
            self._play(task)
        else:
            self._probe(task)
        self.store.save()
        self.changed.emit(task.id)

    def _play(self, task, local_file=""):
        self.previews.set_source(task, local_file)
        if local_file:
            self.local_attempted.add(task.id)
        self.player.play(task, local_file)

    def _probe(self, task: Task):
        if task.id in self.probes:
            return
        task.state, task.error = "analyzing", ""
        probe = MetadataProbe(self.tools, task, self)
        self.probes[task.id] = probe
        probe.ready.connect(self._metadata)
        probe.failed.connect(self._metadata_failed)
        probe.start()
        self.changed.emit(task.id)
        self.activity.emit()

    def _stop_probe(self, task_id):
        probe = self.probes.pop(task_id, None)
        if probe:
            probe.stop()
            probe.deleteLater()
        self.activity.emit()

    def _metadata(self, task_id: str, info: dict):
        if task_id not in self.probes:
            return
        self._stop_probe(task_id)
        task = self.store.tasks[task_id]
        task.title = info["title"]
        self.previews.remember(task_id, info)
        self.downloads.enqueue(task)
        if task_id == self.current_id and self.play_requested:
            self._play(task)
        self.changed.emit(task_id)

    def _metadata_failed(self, task_id: str, error: str):
        if task_id not in self.probes:
            return
        self._stop_probe(task_id)
        task = self.store.tasks[task_id]
        task.state, task.error = "failed", error
        self.store.save()
        self.changed.emit(task_id)
        if task_id == self.current_id:
            self.message.emit(f"영상 정보 조회 실패: {error}")

    def _played(self, task_id: str):
        if task_id != self.current_id:
            return
        previous = self.last_played_id
        self.last_played_id = task_id
        self.wait_local.discard(task_id)
        if previous and previous != task_id and self.transition_delete.pop(task_id, False):
            self.delete_task(previous)

    def delete_task(self, task_id):
        task = self.store.tasks.get(task_id)
        if not task or task.id == self.player.task_id and self.player.process is not None:
            return
        task.delete_pending = True
        self.previews.forget(task_id)
        self.store.save()  # Persist intent before cancelling an active process.
        self._stop_probe(task_id)
        self.downloads.cancel(task_id)
        self.store.cleanup(task)
        self.changed.emit(task_id)

    def _play_failed(self, task_id: str, error: str):
        if task_id != self.current_id:
            return
        task = self.store.tasks[task_id]
        if task_id in self.local_attempted:
            self.wait_local.discard(task_id)
            self.player.status.emit("저장 파일 재생 실패")
            self.message.emit(f"저장 파일 재생 오류: {error}")
            return
        if task_id not in self.wait_local:
            self.wait_local.add(task_id)
            if local := self.store.local_file(task):
                self._play(task, str(local))
                return
        if task.state in {"queued", "downloading", "merging", "verifying"}:
            self.player.status.emit("스트리밍 실패 · 다운로드 완료 후 재생 대기")
        else:
            self.player.status.emit("재생 실패")
        self.message.emit(f"재생 오류: {error}")

    def _downloaded(self, task_id):
        if task_id == self.current_id and task_id in self.wait_local and self.play_requested:
            task = self.store.tasks[task_id]
            if local := self.store.local_file(task):
                self._play(task, str(local))

    def stop_playback(self):
        self.previews.cancel()
        self.play_requested = False
        self.wait_local.discard(self.current_id)
        self.player.stop()
        self.player.status.emit("재생 중지 · 다운로드는 계속됩니다.")

    def toggle_playback(self):
        if self.player.process is not None:
            self.player.toggle_pause()
            return
        if not self.current_id:
            return
        self.play_requested = True
        task = self.store.tasks[self.current_id]
        self.local_attempted.discard(task.id)
        if local := self.store.local_file(task):
            self._play(task, str(local))
        elif task.id in self.downloads.active or task.id in self.downloads.pending:
            self._play(task)
        elif task.id not in self.probes:
            self._probe(task)

    def cancel(self, task_id):
        self._stop_probe(task_id)
        self.downloads.cancel(task_id)
        task = self.store.tasks[task_id]
        if task.state == "analyzing":
            task.state = "cancelled"
        self.store.save()
        self.changed.emit(task_id)

    def retry(self, task_id):
        task = self.store.tasks[task_id]
        if task.state not in {"failed", "cancelled"} or task.delete_pending:
            return
        self._probe(task)

    def clear_list(self):
        # Persist the hidden state first: cancellation and late progress events
        # must never repopulate the list. Keep ownership records and all files.
        for task in self.store.tasks.values():
            task.list_visible = False
        self.transition_delete.clear()
        self.store.save()
        task_ids = dict.fromkeys([*self.probes, *self.downloads.pending, *self.downloads.active])
        for task_id in task_ids:
            self.cancel(task_id)
        self.list_cleared.emit()

    def recover_cleanup(self):
        for task in self.store.tasks.values():
            if task.delete_pending:
                self.store.cleanup(task)
                self.changed.emit(task.id)

    def shutdown(self):
        self.closing = True
        self.previews.cancel()
        for task_id in list(self.probes):
            self.cancel(task_id)
        self.downloads.shutdown()
        self.player.stop()
        self.store.save()
