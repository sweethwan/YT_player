"""Latest-request-only storyboard fetching and isolated frame extraction."""

import math
from collections import OrderedDict

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QObject, QProcess, QTimer, QUrl, Signal
from PySide6.QtGui import QImage, QImageReader
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from .downloads import MetadataProbe
from .preview_data import storyboard_cell
from .processes import OwnedProcess


def decode_image(data: bytes) -> QImage:
    buffer = QBuffer()
    buffer.setData(QByteArray(data))
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buffer)
    size = reader.size()
    if not size.isValid() or size.width() * size.height() > 16_000_000:
        return QImage()
    return reader.read()


class ImageCache:
    def __init__(self, limit=64 * 1024 * 1024):
        self.limit = limit
        self.size = 0
        self.images = OrderedDict()

    def get(self, key):
        image = self.images.get(key)
        if image is not None:
            self.images.move_to_end(key)
        return image

    def put(self, key, image):
        if image.isNull() or image.sizeInBytes() > self.limit:
            return
        if key in self.images:
            self.size -= self.images.pop(key).sizeInBytes()
        self.images[key] = image
        self.size += image.sizeInBytes()
        while self.size > self.limit:
            self.size -= self.images.popitem(last=False)[1].sizeInBytes()

    def clear(self):
        self.images.clear()
        self.size = 0


class PreviewService(QObject):
    ready = Signal(int, float, object, str)
    invalidated = Signal()
    activity = Signal()

    def __init__(self, tools, settings, store, parent=None):
        super().__init__(parent)
        self.tools, self.settings, self.store = tools, settings, store
        self.network = QNetworkAccessManager(self)
        self.cache = ImageCache()
        self.metadata = {}
        self.task = None
        self.local = ""
        self.serial = 0
        self.key = None
        self.seconds = self.frame_time = self.duration = 0.0
        self.last = None
        self.refresh_allowed = True
        self.probe = None
        self.reply = None
        self.process = None
        self.output = bytearray()
        self.errors = bytearray()
        self.failed_sheets = set()
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(150)
        self.timer.timeout.connect(lambda: self._render(self.serial))
        self.watchdog = QTimer(self)
        self.watchdog.setSingleShot(True)
        self.watchdog.setInterval(10000)
        self.watchdog.timeout.connect(self._timeout)

    @property
    def busy(self):
        return self.probe is not None

    def set_source(self, task, local=""):
        self.cancel()
        self.task, self.local = task, local
        self.cache.clear()
        self.failed_sheets.clear()

    def remember(self, task_id, info):
        self.metadata[task_id] = info.get("preview", {})

    def cancel(self):
        self.serial += 1
        self.key = None
        self.last = None
        self.timer.stop()
        self._stop_io()
        self.invalidated.emit()

    def forget(self, task_id):
        self.metadata.pop(task_id, None)
        if self.task and self.task.id == task_id:
            self.cancel()
            self.task = None
            self.cache.clear()

    def _stop_io(self):
        self.watchdog.stop()
        if self.reply:
            reply, self.reply = self.reply, None
            reply.abort()
            reply.deleteLater()
        if self.process:
            process, self.process = self.process, None
            process.shutdown()
            process.deleteLater()
        if self.probe:
            probe, self.probe = self.probe, None
            probe.stop()
            probe.deleteLater()
            self.activity.emit()

    def request(self, seconds: float, duration: float, fps: float = 0):
        if not self.task or self.task.delete_pending or duration <= 0:
            return
        self.duration = duration
        self.seconds = max(0, min(seconds, max(0, duration - 0.001)))
        mode = self.settings.value.thumbnail_mode
        rate = fps or (self.metadata.get(self.task.id, {}).get("stream") or {}).get("fps") or 30
        if not math.isfinite(rate) or rate <= 0:
            rate = 30
        # No frame starts at the exact end timestamp. Request the last frame instead.
        image_time = min(self.seconds, max(0, duration - 1 / rate - 0.000001))
        frame_time = int(image_time) if mode == "fast" else round(image_time, 3)
        key = (self.task.id, mode, frame_time, bool(self.local))
        board = self.metadata.get(self.task.id, {}).get("storyboard")
        if mode == "fast" and board:
            try:
                url, rect = storyboard_cell(board, self.seconds, duration)
                if url not in self.failed_sheets:
                    key = (self.task.id, mode, "board", url, rect)
            except (ValueError, KeyError, IndexError, TypeError, ZeroDivisionError):
                pass
        if key == self.key:
            if self.last is not None:
                self.ready.emit(self.serial, self.seconds, self.last[0], self.last[1])
            return
        self.serial += 1
        self._stop_io()
        self.key, self.frame_time = key, frame_time
        self.last = None
        self.refresh_allowed = True
        image = self.cache.get(key)
        if image is not None:
            self._deliver(self.serial, image)
        else:
            self.timer.start()

    def _deliver(self, serial, image=None, error=""):
        if serial != self.serial or self.key is None:
            return
        self.watchdog.stop()
        self.last = (image, error)
        if image is not None:
            self.cache.put(self.key, image)
        self.ready.emit(serial, self.seconds, image, error)

    def _render(self, serial):
        if serial != self.serial or not self.task or self.key is None:
            return
        info = self.metadata.get(self.task.id, {})
        if self.settings.value.thumbnail_mode == "fast" and info.get("storyboard"):
            try:
                url, rect = storyboard_cell(info["storyboard"], self.seconds, self.duration)
                if url in self.failed_sheets:
                    self._extract(serial)
                    return
            except (ValueError, KeyError, IndexError, TypeError, ZeroDivisionError):
                self._extract(serial)
                return
            sprite = self.cache.get(("sheet", url))
            if sprite is not None:
                self._crop(serial, sprite, rect)
            else:
                self._fetch(serial, url, rect)
        elif self.local or info.get("stream"):
            self._extract(serial)
        else:
            self._resolve(serial)

    def _resolve(self, serial):
        if serial != self.serial or not self.task:
            return
        probe = MetadataProbe(self.tools, self.task, self)
        self.probe = probe
        probe.ready.connect(lambda task_id, info: self._resolved(serial, probe, task_id, info))
        probe.failed.connect(lambda _id, _err: self._resolve_failed(serial, probe))
        probe.start()
        probe.timer.start(10000)
        self.activity.emit()

    def _resolved(self, serial, probe, task_id, info):
        if self.probe is not probe or serial != self.serial:
            return
        self.probe = None
        probe.stop()
        probe.deleteLater()
        self.activity.emit()
        self.remember(task_id, info)
        if not self.local and not self.metadata[task_id].get("stream"):
            # A storyboard alone can still support fast previews.
            if not (
                self.settings.value.thumbnail_mode == "fast"
                and self.metadata[task_id].get("storyboard")
            ):
                self._deliver(serial, error="미리보기 불가")
                return
        self._render(serial)

    def _resolve_failed(self, serial, probe):
        if self.probe is probe:
            self.probe = None
            probe.stop()
            probe.deleteLater()
            self.activity.emit()
            self._deliver(serial, error="미리보기 불가")

    def _fetch(self, serial, url, rect):
        request = QNetworkRequest(QUrl(url))
        request.setTransferTimeout(10000)
        reply = self.network.get(request)
        self.reply = reply
        reply.finished.connect(lambda: self._fetched(serial, reply, url, rect))
        reply.downloadProgress.connect(
            lambda size, _total: reply.abort() if size > 8_000_000 else None
        )
        self.watchdog.start()

    def _fetched(self, serial, reply, url, rect):
        if self.reply is not reply:
            return
        self.reply = None
        self.watchdog.stop()
        success = reply.error() == QNetworkReply.NetworkError.NoError
        data = bytes(reply.readAll()) if success else b""
        reply.deleteLater()
        if serial != self.serial:
            return
        image = decode_image(data) if data else QImage()
        if image.isNull():
            self.failed_sheets.add(url)
            self._extract(serial)
        else:
            self.cache.put(("sheet", url), image)
            self._crop(serial, image, rect)

    def _crop(self, serial, image, rect):
        x, y, width, height = rect
        if x + width > image.width() or y + height > image.height():
            self._extract(serial)
            return
        self._deliver(serial, image.copy(x, y, width, height))

    def _extract(self, serial):
        if serial != self.serial or not self.task:
            return
        info = self.metadata.get(self.task.id, {})
        # Prefer the validated completed file once a download has finished.
        local = self.local or self.store.local_file(self.task)
        stream = info.get("stream") or {}
        source = str(local) if local else stream.get("url", "")
        if not source:
            self._deliver(serial, error="미리보기 불가")
            return
        args = ["-v", "error", "-nostdin", "-rw_timeout", "10000000"]
        if not local:
            headers = []
            for name, value in stream.get("headers", {}).items():
                if (
                    isinstance(name, str)
                    and isinstance(value, str)
                    and not any(c in name + value for c in "\r\n")
                    and name.replace("-", "").isalnum()
                ):
                    headers.append(f"{name}: {value}\r\n")
            if headers:
                args += ["-headers", "".join(headers)]
        args += [
            "-ss",
            str(self.frame_time),
            "-i",
            source,
            "-map",
            "0:v:0",
            "-an",
            "-sn",
            "-frames:v",
            "1",
            "-vf",
            "scale=320:180:force_original_aspect_ratio=decrease",
            "-threads",
            "1",
            "-f",
            "image2pipe",
            "-vcodec",
            "png",
            "pipe:1",
        ]
        process = OwnedProcess(self.tools, self)
        self.process = process
        self.output.clear()
        self.errors.clear()
        process.readyReadStandardOutput.connect(lambda: self._read_frame(process))
        process.readyReadStandardError.connect(lambda: self._read_error(process))
        process.finished.connect(
            lambda code, _status: self._extracted(serial, process, code, bool(local))
        )
        process.errorOccurred.connect(
            lambda error: self._start_error(serial, process, error, bool(local))
        )
        self.watchdog.start()
        process.start(self.tools.path("ffmpeg"), args)

    def _read_frame(self, process):
        if self.process is process:
            self.output.extend(bytes(process.readAllStandardOutput()))
            if len(self.output) > 4_000_000:
                self._timeout()

    def _read_error(self, process):
        if self.process is process:
            self.errors.extend(bytes(process.readAllStandardError()))
            self.errors = self.errors[-4000:]

    def _start_error(self, serial, process, error, local):
        if self.process is process and error == QProcess.ProcessError.FailedToStart:
            self._frame_failed(serial, local)

    def _extracted(self, serial, process, code, local):
        if self.process is not process:
            return
        self._read_frame(process)
        if self.process is not process:
            return
        self.process = None
        self.watchdog.stop()
        process.deleteLater()
        image = decode_image(bytes(self.output)) if code == 0 else QImage()
        if not image.isNull():
            self._deliver(serial, image)
        else:
            self._frame_failed(serial, local)

    def _frame_failed(self, serial, local):
        self._stop_io()
        if serial != self.serial:
            return
        if not local and self.refresh_allowed:
            self.refresh_allowed = False
            self.metadata.pop(self.task.id, None)
            self._resolve(serial)
        else:
            self._deliver(serial, error="미리보기 불가")

    def _timeout(self):
        self._stop_io()
        self._deliver(self.serial, error="미리보기 불가 · 응답 시간 초과")
