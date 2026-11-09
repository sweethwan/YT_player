"""Embedded mpv process with asynchronous Windows named-pipe JSON IPC."""

from __future__ import annotations

import json
import math
from uuid import uuid4

from PySide6.QtCore import QObject, QProcess, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtNetwork import QLocalSocket

from .core import Task, format_selector
from .processes import OwnedProcess, Tools


class Player(QObject):
    started = Signal(str)
    failed = Signal(str, str)
    ended = Signal(str)
    position = Signal(float, float)
    status = Signal(str)
    idle_changed = Signal()
    ipc_reply = Signal(dict)
    input_action = Signal(str)

    def __init__(self, tools: Tools, window_id: int, parent=None):
        super().__init__(parent)
        self.tools, self.window_id = tools, window_id
        self.process: OwnedProcess | None = None
        self.socket: QLocalSocket | None = None
        self.task_id = ""
        self.streaming = False
        self.ready = False
        self.has_file = False
        self.paused = False
        self.current_position = 0.0
        self.duration = 0.0
        self.fps = 0.0
        self.generation = 0
        self.volume, self.muted, self.speed = 70, False, 1.0
        self.retry = QTimer(self)
        self.retry.setInterval(100)
        self.retry.timeout.connect(self._connect)
        self.start_timer = QTimer(self)
        self.start_timer.setSingleShot(True)
        self.start_timer.timeout.connect(self._timeout)
        self.pipe = ""
        self.buffer = bytearray()
        self.target = ""
        self.log = bytearray()

    @property
    def busy(self):
        return self.streaming and self.process is not None

    def play(self, task: Task, local_file: str = ""):
        self.stop()
        self.task_id = task.id
        self.streaming = not bool(local_file)
        self.ready = False
        self.has_file = True
        self.current_position, self.duration = 0, 0
        self.fps = 0.0
        self.position.emit(0, 0)
        self.target = local_file or task.url
        self.quality = task.quality
        self.pipe = f"yt-player-{uuid4().hex}"
        self.buffer.clear()
        self.log.clear()
        self.socket = QLocalSocket(self)
        generation = self.generation
        self.socket.connected.connect(lambda: self._connected(generation))
        self.socket.readyRead.connect(lambda: self._read(generation))
        self.process = OwnedProcess(self.tools, self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(lambda: self._read_log(generation))
        self.process.finished.connect(lambda code, _status: self._exit(generation, code))
        self.process.errorOccurred.connect(lambda error: self._process_error(generation, error))
        args = [
            "--no-config",
            "--idle=yes",
            "--terminal=no",
            "--osc=no",
            "--input-default-bindings=no",
            "--input-vo-keyboard=yes",
            f"--input-doubleclick-time={QGuiApplication.styleHints().mouseDoubleClickInterval()}",
            "--cursor-autohide=no",
            "--keep-open=no",
            "--force-window=yes",
            "--hwdec=auto-safe",
            f"--wid={self.window_id}",
            f"--input-ipc-server=\\\\.\\pipe\\{self.pipe}",
            f"--volume={self.volume}",
            f"--mute={'yes' if self.muted else 'no'}",
            f"--speed={self.speed}",
        ]
        self.process.start(self.tools.path("mpv"), args)
        self.retry.start()
        self.start_timer.start(90000)
        self.status.emit("스트리밍 준비 중" if self.streaming else "저장 파일 여는 중")
        self.idle_changed.emit()

    def _process_error(self, generation, error):
        if generation == self.generation and error == QProcess.ProcessError.FailedToStart:
            self._fail(f"mpv 실행 실패: {self.process.errorString()}")

    def _connect(self):
        if self.socket and self.socket.state() == QLocalSocket.LocalSocketState.UnconnectedState:
            self.socket.connectToServer(self.pipe)

    def _connected(self, generation):
        if generation != self.generation:
            return
        self.retry.stop()
        bindings = {
            "MBTN_LEFT": "video-click",
            "MBTN_LEFT_DBL": "video-double-click",
            "F11": "fullscreen-toggle",
            "ESC": "fullscreen-exit",
            "LEFT": "seek-back",
            "RIGHT": "seek-forward",
            "Shift+LEFT": "seek-back-large",
            "Shift+RIGHT": "seek-forward-large",
            "SPACE": "pause",
        }
        for key, action in bindings.items():
            self.command(["keybind", key, f"script-message yt-player-input {action}"])
        # JSON commands avoid comma/colon escaping problems in tool paths.
        self.command(
            [
                "set_property",
                "options/script-opts",
                {"ytdl_hook-ytdl_path": self.tools.path("yt-dlp")},
            ]
        )
        self.command(["set_property", "options/ytdl-format", format_selector(self.quality)])
        self.command(
            [
                "set_property",
                "options/ytdl-raw-options",
                {
                    "js-runtimes": f"deno:{self.tools.path('deno')}",
                    "no-cache-dir": "",
                    "no-plugin-dirs": "",
                    "socket-timeout": "20",
                    "retries": "3",
                },
            ]
        )
        for number, name in enumerate(
            ["time-pos", "duration", "pause", "paused-for-cache", "container-fps"], 1
        ):
            self.command(["observe_property", number, name])
        self.command(["loadfile", self.target, "replace"])

    def command(self, value: list):
        if self.socket and self.socket.state() == QLocalSocket.LocalSocketState.ConnectedState:
            self.socket.write((json.dumps({"command": value}, ensure_ascii=False) + "\n").encode())

    def _read(self, generation):
        if generation != self.generation or not self.socket:
            return
        self.buffer.extend(bytes(self.socket.readAll()))
        while b"\n" in self.buffer:
            line, _, self.buffer = self.buffer.partition(b"\n")
            try:
                event = json.loads(line)
            except ValueError:
                continue
            kind = event.get("event")
            if kind is None:
                self.ipc_reply.emit(event)
            if kind == "client-message":
                arguments = event.get("args", [])
                if len(arguments) == 2 and arguments[0] == "yt-player-input":
                    self.input_action.emit(arguments[1])
            if kind == "playback-restart" and not self.ready:
                self.ready = True
                self.start_timer.stop()
                self.status.emit("재생 중 · 스트리밍" if self.streaming else "재생 중 · 저장 파일")
                self.started.emit(self.task_id)
            elif kind == "end-file":
                if event.get("reason") == "error":
                    self._fail(event.get("file_error", "영상을 재생할 수 없습니다."))
                    return
                if event.get("reason") == "eof":
                    self.has_file = False
                    self.streaming = False
                    self.status.emit("재생 완료")
                    self.ended.emit(self.task_id)
                    self.idle_changed.emit()
            elif kind == "property-change":
                name, value = event.get("name"), event.get("data")
                if name == "time-pos":
                    self.current_position = float(value or 0)
                elif name == "duration":
                    self.duration = float(value or 0)
                elif name == "container-fps":
                    fps = float(value or 0)
                    self.fps = fps if math.isfinite(fps) and fps > 0 else 0.0
                elif name == "pause" and value:
                    self.paused = True
                    self.status.emit("일시정지")
                elif name == "paused-for-cache" and value:
                    self.status.emit("버퍼링 중")
                elif name in {"pause", "paused-for-cache"} and value is False and self.ready:
                    if name == "pause":
                        self.paused = False
                    self.status.emit(
                        "재생 중 · 스트리밍" if self.streaming else "재생 중 · 저장 파일"
                    )
                self.position.emit(self.current_position, self.duration)

    def _read_log(self, generation):
        if generation == self.generation and self.process:
            self.log.extend(bytes(self.process.readAllStandardOutput()))
            self.log = self.log[-8000:]

    def _exit(self, generation, code):
        if generation == self.generation:
            self._fail(f"mpv가 종료되었습니다 ({code}).")

    def _timeout(self):
        self._fail("재생 준비 시간이 초과되었습니다.")

    def _fail(self, message):
        task_id = self.task_id
        self.stop()
        self.failed.emit(task_id, message)

    def toggle_pause(self):
        self.command(["cycle", "pause"])

    def seek(self, seconds: float):
        if self.has_file and self.duration > 0:
            self.command(["seek", max(0, min(self.duration, seconds)), "absolute+exact"])

    def seek_relative(self, seconds: float):
        if self.has_file and self.ready:
            self.command(["seek", seconds, "relative+exact"])

    def set_volume(self, value):
        self.volume = value
        self.command(["set_property", "volume", value])

    def set_muted(self, value):
        self.muted = value
        self.command(["set_property", "mute", value])

    def set_speed(self, value):
        self.speed = value
        self.command(["set_property", "speed", value])

    def stop(self):
        self.has_file = False
        self.ready = False
        self.paused = False
        self.generation += 1
        self.retry.stop()
        self.start_timer.stop()
        if self.socket:
            self.socket.abort()
            self.socket.deleteLater()
            self.socket = None
        if self.process:
            self.process.shutdown()
            self.process.deleteLater()
            self.process = None
        self.streaming = False
        self.idle_changed.emit()
