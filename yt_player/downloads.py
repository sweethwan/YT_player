"""Nonblocking metadata probes and a bounded FIFO download queue."""

from __future__ import annotations

import json
from collections import deque
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

from .core import Task, TaskStore, format_selector
from .preview_data import preview_metadata
from .processes import OwnedProcess, Tools


def validate_metadata(info: dict, video_id: str) -> None:
    if info.get("id") != video_id or info.get("_type", "video") != "video":
        raise ValueError("단일 YouTube 영상만 지원합니다.")
    if info.get("is_live") or info.get("live_status") in {"is_live", "is_upcoming", "post_live"}:
        raise ValueError("라이브·방송 예정 영상은 지원하지 않습니다.")
    if info.get("availability") not in {None, "public", "unlisted"}:
        raise ValueError("로그인이 필요한 영상은 지원하지 않습니다.")
    if not info.get("formats"):
        raise ValueError("재생 가능한 영상 형식을 찾지 못했습니다.")


class MetadataProbe(QObject):
    ready = Signal(str, dict)
    failed = Signal(str, str)

    def __init__(self, tools: Tools, task: Task, parent=None):
        super().__init__(parent)
        self.task = task
        self.process = OwnedProcess(tools, self)
        self.output = bytearray()
        self.errors = bytearray()
        self.done = False
        self.process.readyReadStandardOutput.connect(self._read)
        self.process.readyReadStandardError.connect(self._read_error)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._error)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._timeout)
        self.args = tools.ytdlp_args() + ["--dump-single-json", "--skip-download", "--", task.url]
        self.program = tools.path("yt-dlp")

    def start(self):
        self.timer.start(90000)
        self.process.start(self.program, self.args)

    def _read(self):
        self.output.extend(bytes(self.process.readAllStandardOutput()))
        if len(self.output) > 32 * 1024 * 1024:
            self._fail("영상 정보 응답이 너무 큽니다.")
            self.process.stop()

    def _read_error(self):
        self.errors.extend(bytes(self.process.readAllStandardError()))
        self.errors = self.errors[-8000:]

    def _error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self._fail(f"yt-dlp 실행 실패: {self.process.errorString()}")

    def _timeout(self):
        self._fail("영상 정보 조회 시간이 초과되었습니다. 네트워크를 확인하고 재시도하세요.")
        self.process.stop()

    def _fail(self, message):
        if not self.done:
            self.done = True
            self.timer.stop()
            self.failed.emit(self.task.id, message)

    def _finished(self, code, _status):
        self._read()
        self._read_error()
        if self.done:
            return
        if code:
            self._fail(self.errors.decode("utf-8", "replace").strip() or "영상 정보 조회 실패")
            return
        try:
            info = json.loads(self.output)
            validate_metadata(info, self.task.video_id)
        except (ValueError, TypeError, AttributeError) as exc:
            self._fail(str(exc))
            return
        self.done = True
        self.timer.stop()
        self.ready.emit(
            self.task.id,
            {"title": info.get("title", self.task.video_id), "preview": preview_metadata(info)},
        )

    def stop(self):
        self.done = True
        self.timer.stop()
        self.process.shutdown()


class DownloadWorker(QObject):
    changed = Signal(str)
    finished = Signal(str)

    def __init__(self, tools: Tools, store: TaskStore, task: Task, parent=None):
        super().__init__(parent)
        self.tools, self.store, self.task = tools, store, task
        self.process = OwnedProcess(tools, self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self._read)
        self.process.finished.connect(self._download_finished)
        self.process.errorOccurred.connect(self._error)
        self.buffer = bytearray()
        self.tail: deque[str] = deque(maxlen=12)
        self.file = ""
        self.done = False
        self.cancelled = False
        self.verifier: OwnedProcess | None = None
        self.verify_output = bytearray()
        self.verify_timer = QTimer(self)
        self.verify_timer.setSingleShot(True)
        self.verify_timer.timeout.connect(self._verify_timeout)

    def start(self):
        try:
            folder = self.store.prepare(self.task)
        except OSError as exc:
            self._finish("failed", f"저장 폴더를 사용할 수 없습니다: {exc}")
            return
        self.task.state = "downloading"
        self.task.error = ""
        self.changed.emit(self.task.id)
        self.store.save()
        args = self.tools.ytdlp_args() + [
            "--format",
            format_selector(self.task.quality),
            "--newline",
            "--progress",
            "--progress-delta",
            "0.5",
            "--continue",
            "--windows-filenames",
            "--merge-output-format",
            "mkv",
            "--no-mtime",
            "--match-filters",
            "!is_live & live_status != is_upcoming & live_status != post_live",
            "--paths",
            str(folder),
            "--output",
            "%(title).100B [%(id)s].%(ext)s",
            "--progress-template",
            "download:YTP_PROGRESS:%(progress)j",
            "--progress-template",
            "postprocess:YTP_POST:%(progress)j",
            "--print",
            "after_move:YTP_FILE:%(filepath)j",
            "--no-simulate",
            "--",
            self.task.url,
        ]
        self.process.start(self.tools.path("yt-dlp"), args)

    def _read(self):
        self.buffer.extend(bytes(self.process.readAllStandardOutput()))
        while b"\n" in self.buffer:
            line, _, self.buffer = self.buffer.partition(b"\n")
            self._line(line.decode("utf-8", "replace").strip())

    def _line(self, line: str):
        if not line:
            return
        try:
            if line.startswith("YTP_PROGRESS:"):
                data = json.loads(line.removeprefix("YTP_PROGRESS:"))
                total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
                self.task.progress = (
                    min(100, 100 * (data.get("downloaded_bytes") or 0) / total) if total else 0
                )
                self.task.speed = data.get("speed") or 0
                self.task.eta = data.get("eta") or 0
                self.changed.emit(self.task.id)
            elif line.startswith("YTP_POST:"):
                self.task.state = "merging"
                self.changed.emit(self.task.id)
            elif line.startswith("YTP_FILE:"):
                self.file = json.loads(line.removeprefix("YTP_FILE:"))
            else:
                self.tail.append(line)
        except (ValueError, TypeError, ZeroDivisionError):
            self.tail.append(line[:1000])

    def _error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self._finish("failed", f"다운로드 실행 실패: {self.process.errorString()}")

    def _download_finished(self, code, _status):
        self._read()
        if self.buffer:
            self._line(self.buffer.decode("utf-8", "replace").strip())
            self.buffer.clear()
        if self.done:
            return
        if self.cancelled:
            self._finish("cancelled")
        elif code or not self.file:
            self._finish("failed", "\n".join(self.tail) or "다운로드 파일을 생성하지 못했습니다.")
        else:
            try:
                path = Path(self.file).resolve()
                if not path.is_relative_to(Path(self.task.folder).resolve()) or not path.is_file():
                    raise OSError("완성 파일이 작업 폴더 밖에 있거나 존재하지 않습니다.")
                self.task.file = str(path)
                self.task.file_size = path.stat().st_size
                if self.task.file_size <= 0:
                    raise OSError("완성 파일이 비어 있습니다.")
            except OSError as exc:
                self._finish("failed", str(exc))
                return
            self.task.state = "verifying"
            self.changed.emit(self.task.id)
            self.verifier = OwnedProcess(self.tools, self)
            self.verifier.readyReadStandardOutput.connect(self._read_verify)
            self.verifier.finished.connect(self._verified)
            self.verifier.errorOccurred.connect(self._verify_error)
            self.verify_timer.start(20000)
            self.verifier.start(
                self.tools.path("ffprobe"),
                [
                    "-v",
                    "error",
                    "-show_entries",
                    "stream=codec_type:format=duration",
                    "-of",
                    "json",
                    self.task.file,
                ],
            )

    def _read_verify(self):
        self.verify_output.extend(bytes(self.verifier.readAllStandardOutput()))

    def _verify_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self._finish("failed", "ffprobe 파일 검증 실행 실패")

    def _verify_timeout(self):
        self._finish("failed", "완성 파일 검증 시간 초과")
        if self.verifier:
            self.verifier.stop()

    def _verified(self, code, _status):
        self._read_verify()
        if self.done:
            return
        try:
            data = json.loads(self.verify_output)
            if code or not any(s.get("codec_type") == "video" for s in data.get("streams", [])):
                raise ValueError
        except (ValueError, TypeError):
            self._finish("failed", "다운로드 파일 검증 실패 · 재시도하세요.")
            return
        self.task.progress = 100
        self._finish("completed")

    def _finish(self, state, error=""):
        if self.done:
            return
        self.done = True
        self.verify_timer.stop()
        self.task.state, self.task.error = state, error
        self.store.save()
        self.changed.emit(self.task.id)
        self.finished.emit(self.task.id)

    def cancel(self):
        if self.done:
            return
        self.cancelled = True
        self.process.shutdown()
        if self.verifier:
            self.verifier.shutdown()
        self._finish("cancelled")


class DownloadQueue(QObject):
    changed = Signal(str)
    completed = Signal(str)
    idle_changed = Signal()

    def __init__(self, tools: Tools, store: TaskStore, parent=None):
        super().__init__(parent)
        self.tools, self.store = tools, store
        self.pending: deque[str] = deque()
        self.active: dict[str, DownloadWorker] = {}
        self.closing = False

    @property
    def busy(self):
        return bool(self.pending or self.active)

    def enqueue(self, task: Task):
        if task.id in self.active or task.id in self.pending or task.delete_pending:
            return
        task.state, task.error = "queued", ""
        self.pending.append(task.id)
        self.store.save()
        self.changed.emit(task.id)
        self._pump()

    def _pump(self):
        while not self.closing and self.pending and len(self.active) < 2:
            task_id = self.pending.popleft()
            worker = DownloadWorker(self.tools, self.store, self.store.tasks[task_id], self)
            self.active[task_id] = worker
            worker.changed.connect(self.changed)
            worker.finished.connect(self._finished)
            worker.start()
        self.idle_changed.emit()

    def _finished(self, task_id):
        worker = self.active.pop(task_id, None)
        if worker:
            worker.deleteLater()
        if self.store.tasks[task_id].state == "completed":
            self.completed.emit(task_id)
        # Defer pumping until cancellation/cleanup callers finish their transaction.
        QTimer.singleShot(0, self._pump)

    def cancel(self, task_id):
        if task_id in self.pending:
            self.pending.remove(task_id)
            self.store.tasks[task_id].state = "cancelled"
            self.store.save()
            self.changed.emit(task_id)
        if worker := self.active.get(task_id):
            worker.cancel()
        self.idle_changed.emit()

    def shutdown(self):
        self.closing = True
        for task_id in list(self.pending) + list(self.active):
            self.cancel(task_id)
