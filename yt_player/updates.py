"""Verified stable yt-dlp updates, applied only while tools are idle."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.request import Request, urlopen

from PySide6.QtCore import QObject, QThread, QTimer, Signal

from .core import SettingsStore, atomic_json, utc_now
from .processes import ProcessTree, Tools

RELEASE_API = "https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest"
HEADERS = {"User-Agent": "YTDesktopPlayer/0.1", "Accept": "application/vnd.github+json"}


def fetch(url: str, limit: int = 8 * 1024 * 1024) -> bytes:
    if not url.startswith(("https://api.github.com/", "https://github.com/")):
        raise ValueError("공식 GitHub 다운로드 주소가 아닙니다.")
    with urlopen(Request(url, headers=HEADERS), timeout=25) as response:
        data = response.read(limit + 1)
        if len(data) > limit:
            raise ValueError("업데이트 응답이 너무 큽니다.")
        return data


def latest_release() -> dict:
    release = json.loads(fetch(RELEASE_API))
    if release.get("prerelease") or release.get("draft"):
        raise ValueError("안정 릴리스가 아닙니다.")
    version = release["tag_name"]
    if not re.fullmatch(r"\d{4}\.\d{2}\.\d{2}(?:\.\d+)?", version):
        raise ValueError("지원하지 않는 버전 형식입니다.")
    assets = {a["name"]: a["browser_download_url"] for a in release["assets"]}
    return {"version": version, "exe": assets["yt-dlp.exe"], "sha": assets["SHA2-256SUMS"]}


def expected_digest(manifest: bytes, filename: str = "yt-dlp.exe") -> str:
    for line in manifest.decode("utf-8").splitlines():
        match = re.fullmatch(r"([a-fA-F0-9]{64})\s+\*?(.+)", line.strip())
        if match and match[2] == filename:
            return match[1].lower()
    raise ValueError(f"{filename}의 SHA-256을 찾지 못했습니다.")


def executable_version(path: Path) -> str:
    tree = ProcessTree()
    process = None
    try:
        process = subprocess.Popen(
            [str(path), "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            env={**os.environ, "TEMP": str(path.parent), "TMP": str(path.parent)},
        )
        tree.assign(process.pid)
        out, err = process.communicate(timeout=20)
        if process.returncode:
            raise ValueError(err.decode("utf-8", "replace")[-1000:] or "버전 확인 실패")
        return out.decode("utf-8").strip()
    finally:
        tree.close()
        if process and process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def recover_update(tools: Tools) -> None:
    """A journal remains until validation succeeds; recover interrupted replacements."""
    target = Path(tools.path("yt-dlp"))
    backup = target.with_suffix(".exe.bak")
    journal = tools.bin / "update-journal.json"
    if journal.exists():
        if backup.exists():
            os.replace(backup, target)
        journal.unlink()
    elif not target.exists() and backup.exists():
        os.replace(backup, target)
    target.with_name("yt-dlp.new.exe").unlink(missing_ok=True)


def install_release(tools: Tools, release: dict, progress: Callable[[str], None]) -> str:
    tools.bin.mkdir(parents=True, exist_ok=True)
    target = Path(tools.path("yt-dlp"))
    staged = tools.bin / "yt-dlp.new.exe"
    backup = target.with_suffix(".exe.bak")
    journal = tools.bin / "update-journal.json"
    checksum = expected_digest(fetch(release["sha"]))
    digest = hashlib.sha256()
    progress("yt-dlp 다운로드 중")
    try:
        if not release["exe"].startswith("https://github.com/yt-dlp/yt-dlp/releases/download/"):
            raise ValueError("공식 yt-dlp 릴리스 주소가 아닙니다.")
        with urlopen(Request(release["exe"], headers=HEADERS), timeout=25) as response:
            with staged.open("wb") as output:
                size = 0
                while chunk := response.read(1024 * 1024):
                    size += len(chunk)
                    if size > 150 * 1024 * 1024:
                        raise ValueError("업데이트 파일 크기 제한 초과")
                    output.write(chunk)
                    digest.update(chunk)
        if digest.hexdigest() != checksum:
            raise ValueError("SHA-256이 일치하지 않습니다. 기존 버전을 유지합니다.")
        progress("새 버전 실행 검증 중")
        if executable_version(staged) != release["version"]:
            raise ValueError("다운로드한 실행 파일 버전이 일치하지 않습니다.")
        # A stale backup must never be mistaken for this transaction's original.
        backup.unlink(missing_ok=True)
        atomic_json(journal, {"version": release["version"]})
        replaced_old = False
        try:
            if target.exists():
                os.replace(target, backup)
                replaced_old = True
            os.replace(staged, target)
            if executable_version(target) != release["version"]:
                raise ValueError("설치 후 버전 검증 실패")
            journal.unlink()
        except Exception:
            if replaced_old and backup.exists():
                os.replace(backup, target)
            journal.unlink(missing_ok=True)
            raise
        return release["version"]
    finally:
        staged.unlink(missing_ok=True)


class Background(QThread):
    result = Signal(object)
    error = Signal(str)
    progress = Signal(str)

    def __init__(self, operation: Callable, parent=None):
        super().__init__(parent)
        self.operation = operation

    def run(self):
        try:
            self.result.emit(self.operation())
        except Exception as exc:
            self.error.emit(str(exc))


class Updater(QObject):
    changed = Signal()
    message = Signal(str)

    def __init__(self, tools: Tools, settings: SettingsStore, is_busy: Callable, parent=None):
        super().__init__(parent)
        self.tools, self.settings, self.is_busy = tools, settings, is_busy
        self.current = "확인 중"
        self.latest = settings.value.latest_version
        self.last_check = settings.value.last_update_check
        self.status = ""
        self.release = None
        self.worker: Background | None = None
        self.pending = False
        self.applying = False
        self.poll = QTimer(self)
        self.poll.setInterval(1000)
        self.poll.timeout.connect(self._maybe_apply)
        self.poll.start()

    def _launch(self, operation, callback):
        if self.worker:
            return False
        worker = Background(operation, self)
        self.worker = worker
        worker.result.connect(callback)
        worker.error.connect(self._error)
        worker.finished.connect(self._worker_finished)
        worker.progress.connect(self._status)
        worker.start()
        return True

    def _worker_finished(self):
        worker = self.worker
        self.worker = None
        if worker:
            worker.deleteLater()
        self.changed.emit()

    def _status(self, message):
        self.status = message
        self.changed.emit()

    def initialize(self):
        path = Path(self.tools.path("yt-dlp"))
        if not path.exists():
            self.current = "미설치"
            self.changed.emit()
            return
        self._launch(lambda: executable_version(path), self._version_loaded)

    def _version_loaded(self, version):
        self.current = version
        self.changed.emit()
        if self.settings.value.check_updates:
            QTimer.singleShot(100, lambda: self.check(force=False))

    def check(self, force=True):
        if self.worker or self.applying:
            return
        if not force and self.last_check:
            try:
                if datetime.now(UTC) - datetime.fromisoformat(self.last_check) < timedelta(
                    hours=24
                ):
                    self._status("최근 확인 결과 사용 중")
                    return
            except (ValueError, TypeError):
                pass
        self._status("최신 안정 버전 확인 중")
        self._launch(latest_release, self._checked)

    def _checked(self, release):
        self.release = release
        self.latest = release["version"]
        self.last_check = utc_now()
        self.settings.update(latest_version=self.latest, last_update_check=self.last_check)
        self._status("최신 버전입니다." if self.current == self.latest else "업데이트 가능")

    def request_install(self):
        if self.worker or self.applying:
            return
        if self.latest == self.current:
            self._status("최신 버전입니다.")
            return
        self.pending = True
        if not self.release:
            self._launch(latest_release, self._checked)
        self._maybe_apply()

    def _maybe_apply(self):
        if not self.pending or self.worker or not self.release:
            return
        if self.is_busy():
            self._status("재생·다운로드·주소 분석 종료 후 업데이트 적용 대기")
            return
        self.pending = False
        self.applying = True
        release = self.release

        def operation():
            return install_release(self.tools, release, lambda msg: self.worker.progress.emit(msg))

        self._status("업데이트 적용 중")
        self._launch(operation, self._installed)

    def _installed(self, version):
        self.current = version
        self.applying = False
        self._status("업데이트 완료 · 다음 작업부터 적용됩니다.")

    def _error(self, message):
        self.applying = False
        self.pending = False
        if self.current == "확인 중":
            self.current = "확인 실패"
        self._status(f"업데이트 오류: {message}")
        self.message.emit(self.status)
