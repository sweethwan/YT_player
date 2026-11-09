"""Serializable application state and filesystem boundaries (no network or UI)."""

from __future__ import annotations

import json
import re
import shutil
import sys
from dataclasses import asdict, dataclass, field, fields
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from PySide6.QtCore import QIODevice, QObject, QSaveFile, QTimer, Signal

QUALITIES = ["best", "2160", "1440", "1080", "720", "480"]
TERMINAL = {"completed", "failed", "cancelled", "deleted", "cleanup_pending"}


def app_root() -> Path:
    return (
        Path(sys.executable).parent
        if getattr(sys, "frozen", False)
        else Path(__file__).resolve().parent.parent
    )


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def normalize_url(value: str) -> tuple[str, str]:
    """Accept only public YouTube video URLs; never accept arbitrary yt-dlp inputs."""
    raw = value.strip()
    if "://" not in raw:
        raw = "https://" + raw
    try:
        url = urlparse(raw)
        if url.scheme not in {"https", "http"} or url.username or url.password or url.port:
            raise ValueError
        host = (url.hostname or "").lower()
        if host == "youtu.be":
            video_id = url.path.strip("/")
        elif host in {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}:
            if url.path == "/watch":
                video_id = parse_qs(url.query).get("v", [""])[0]
            else:
                match = re.fullmatch(r"/(?:shorts|embed)/([\w-]{11})/?", url.path)
                video_id = match.group(1) if match else ""
        else:
            video_id = ""
        if not re.fullmatch(r"[a-zA-Z0-9_-]{11}", video_id):
            raise ValueError
    except ValueError as exc:
        raise ValueError("공개 YouTube 영상 또는 Shorts 주소를 입력하세요.") from exc
    return f"https://www.youtube.com/watch?v={video_id}", video_id


def format_selector(quality: str) -> str:
    limit = "" if quality == "best" else f"[height<={int(quality)}]"
    return f"bestvideo{limit}+bestaudio/best{limit}"


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    output = QSaveFile(str(path))
    if not output.open(QIODevice.OpenModeFlag.WriteOnly):
        raise OSError(output.errorString())
    payload = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")
    if output.write(payload) != len(payload):
        output.cancelWriting()
        raise OSError(output.errorString())
    if not output.commit():
        raise OSError(output.errorString())


@dataclass
class Settings:
    download_dir: str = ""
    auto_delete: bool = False
    quality: str = "1080"
    check_updates: bool = True
    volume: int = 70
    muted: bool = False
    speed: float = 1.0
    seek_seconds: int = 5
    seek_large_seconds: int = 30
    thumbnail_mode: str = "fast"
    width: int = 1180
    height: int = 800
    sidebar_width: int = 320
    latest_version: str = ""
    last_update_check: str = ""

    @classmethod
    def load(cls, raw: dict, root: Path) -> Settings:
        defaults = cls(download_dir=str(root / "downloads"))
        for item in fields(cls):
            value = raw.get(item.name, getattr(defaults, item.name))
            expected = type(getattr(defaults, item.name))
            if type(value) is expected:
                setattr(defaults, item.name, value)
        if defaults.quality not in QUALITIES:
            defaults.quality = "1080"
        defaults.volume = max(0, min(100, defaults.volume))
        if defaults.speed not in {0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0}:
            defaults.speed = 1.0
        defaults.seek_seconds = max(1, min(3599, defaults.seek_seconds))
        defaults.seek_large_seconds = max(
            defaults.seek_seconds + 1, min(3600, defaults.seek_large_seconds)
        )
        if defaults.thumbnail_mode not in {"fast", "precise"}:
            defaults.thumbnail_mode = "fast"
        defaults.width = max(800, min(7680, defaults.width))
        defaults.height = max(600, min(4320, defaults.height))
        defaults.sidebar_width = max(260, min(520, defaults.sidebar_width))
        if not Path(defaults.download_dir).is_absolute():
            defaults.download_dir = str(root / "downloads")
        return defaults


class SettingsStore(QObject):
    error = Signal(str)

    def __init__(self, root: Path):
        super().__init__()
        self.path = root / "data" / "settings.json"
        self.warnings: list[str] = []
        raw = read_json(self.path, {}, self.warnings)
        self.value = Settings.load(raw if isinstance(raw, dict) else {}, root)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(300)
        self.timer.timeout.connect(self.flush)

    def update(self, **changes) -> None:
        for key, value in changes.items():
            setattr(self.value, key, value)
        self.timer.start()

    def flush(self) -> None:
        self.timer.stop()
        try:
            record = asdict(self.value)
            if Path(self.value.download_dir) == self.path.parent.parent / "downloads":
                record["download_dir"] = ""  # Keep the default portable across folder moves.
            atomic_json(self.path, record)
        except OSError as exc:
            self.error.emit(f"설정을 저장할 수 없습니다: {exc}")


def read_json(path: Path, default, warnings: list[str]):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError):
        backup = path.with_name(f"{path.stem}.corrupt-{uuid4().hex[:8]}.json")
        shutil.copy2(path, backup)
        warnings.append(f"손상된 {path.name}을 {backup.name}에 보존하고 기본값으로 복구했습니다.")
        return default


@dataclass
class Task:
    id: str
    url: str
    video_id: str
    quality: str
    folder: str
    title: str = "영상 정보 확인 중"
    state: str = "queued"
    progress: float = 0.0
    speed: float = 0.0
    eta: float = 0.0
    error: str = ""
    file: str = ""
    file_size: int = 0
    created_at: str = field(default_factory=utc_now)
    delete_pending: bool = False
    list_visible: bool = True
    list_order: int = 0

    @classmethod
    def create(cls, url: str, video_id: str, quality: str, root: Path) -> Task:
        task_id = uuid4().hex
        return cls(task_id, url, video_id, quality, str(root.resolve() / f"ytp-{task_id}"))


class TaskStore(QObject):
    error = Signal(str)

    def __init__(self, root: Path):
        super().__init__()
        self.root = root.resolve()
        self.path = root / "data" / "tasks.json"
        self.warnings: list[str] = []
        self.tasks: dict[str, Task] = {}
        raw = read_json(self.path, [], self.warnings)
        if not isinstance(raw, list):
            self.warnings.append("작업 기록 형식이 잘못되어 빈 목록으로 시작합니다.")
            raw = []
        for item in raw:
            try:
                task = Task(**item)
                if type(task.list_visible) is not bool or type(task.list_order) is not int:
                    raise ValueError("invalid list metadata")
                if not re.fullmatch(r"[0-9a-f]{32}", task.id):
                    raise ValueError("invalid id")
                if not Path(task.folder).is_absolute():
                    task.folder = str((self.root / task.folder).resolve())
                if task.file and not Path(task.file).is_absolute():
                    task.file = str((self.root / task.file).resolve())
                if task.state not in TERMINAL:
                    task.state = "cancelled"
                    task.error = "앱 종료로 중단됨 · 재시도 가능"
                self.tasks[task.id] = task
            except (ValueError, TypeError):
                self.warnings.append("읽을 수 없는 작업 기록 하나를 건너뛰었습니다.")

    def recent_tasks(self) -> list[Task]:
        # Legacy records have order zero and retain their creation-time order.
        tasks = [task for task in self.tasks.values() if task.list_visible]
        return sorted(tasks, key=lambda t: (t.list_order, t.created_at), reverse=True)[:20]

    def show_recent(self, task: Task) -> None:
        task.list_visible = True
        task.list_order = max((t.list_order for t in self.tasks.values()), default=0) + 1
        self.save()

    def save(self) -> None:
        try:
            records = []
            for task in self.tasks.values():
                record = asdict(task)
                for key in ["folder", "file"]:
                    if record[key] and Path(record[key]).is_relative_to(self.root):
                        record[key] = Path(record[key]).relative_to(self.root).as_posix()
                records.append(record)
            atomic_json(self.path, records)
        except OSError as exc:
            self.error.emit(f"작업 기록을 저장할 수 없습니다: {exc}")

    def prepare(self, task: Task) -> Path:
        folder = Path(task.folder)
        if folder.name != f"ytp-{task.id}" or folder.is_symlink():
            raise OSError("안전하지 않은 작업 폴더입니다.")
        folder.mkdir(parents=True, exist_ok=True)
        marker = folder / ".yt-player-task"
        if marker.exists() and marker.read_text(encoding="utf-8") != task.id:
            raise OSError("다른 작업이 소유한 폴더입니다.")
        marker.write_text(task.id, encoding="utf-8")
        return folder

    def cleanup(self, task: Task) -> bool:
        """Delete only a marked, dedicated folder; never glob the download root."""
        folder = Path(task.folder)
        try:
            if folder.exists():
                if (
                    folder.name != f"ytp-{task.id}"
                    or folder.is_symlink()
                    or folder.resolve() != folder.absolute()
                    or (folder / ".yt-player-task").read_text(encoding="utf-8") != task.id
                ):
                    raise OSError("작업 폴더 소유권 확인 실패")
                # Refuse junctions/reparse points anywhere in the folder.
                for child in folder.rglob("*"):
                    if child.is_symlink() or child.is_junction():
                        raise OSError("작업 폴더에 외부 경로 링크가 있습니다.")
                shutil.rmtree(folder)
            task.state = "deleted"
            task.delete_pending = False
            task.file = ""
            task.error = ""
            return True
        except OSError as exc:
            task.state = "cleanup_pending"
            task.delete_pending = True
            task.error = f"파일 정리 대기: {exc}"
            return False
        finally:
            self.save()

    def local_file(self, task: Task) -> Path | None:
        if task.state != "completed" or not task.file or task.delete_pending:
            return None
        path = Path(task.file)
        try:
            if (
                path.resolve().is_relative_to(Path(task.folder).resolve())
                and path.is_file()
                and path.stat().st_size == task.file_size
                and task.file_size > 0
            ):
                return path
        except OSError:
            pass
        return None
