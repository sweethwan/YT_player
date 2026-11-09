import json
import shutil
from dataclasses import asdict
from pathlib import Path

import pytest

from yt_player.core import SettingsStore, Task, TaskStore, format_selector, normalize_url


def test_recent_list_legacy_limit_reopen_and_persistence(qapp, tmp_path):
    store = TaskStore(tmp_path)
    records = []
    for index in range(23):
        task = Task.create("url", str(index), "1080", tmp_path)
        task.created_at = f"2026-11-09T00:00:{index:02d}+00:00"
        task.state = "cancelled"
        record = asdict(task)
        record.pop("list_visible")
        record.pop("list_order")
        records.append(record)
    store.path.parent.mkdir(parents=True)
    store.path.write_text(json.dumps(records), encoding="utf-8")
    store = TaskStore(tmp_path)
    assert [t.video_id for t in store.recent_tasks()] == [str(i) for i in range(22, 2, -1)]
    oldest = next(iter(store.tasks.values()))
    store.show_recent(oldest)
    assert store.recent_tasks()[0].id == oldest.id
    oldest.progress = 42
    store.save()
    restored = TaskStore(tmp_path)
    assert restored.recent_tasks()[0].id == oldest.id
    assert len(restored.tasks) == 23
    for task in restored.tasks.values():
        task.list_visible = False
    restored.save()
    assert not TaskStore(tmp_path).recent_tasks()
    restored.show_recent(restored.tasks[oldest.id])
    assert [t.id for t in TaskStore(tmp_path).recent_tasks()] == [oldest.id]


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=BaW_jenozKc&list=PLtest",
        "https://youtu.be/BaW_jenozKc?t=12",
        "youtube.com/shorts/BaW_jenozKc",
        "https://m.youtube.com/watch?v=BaW_jenozKc",
    ],
)
def test_normalize_video_urls(url):
    assert normalize_url(url) == ("https://www.youtube.com/watch?v=BaW_jenozKc", "BaW_jenozKc")


@pytest.mark.parametrize(
    "url",
    [
        "",
        "https://youtube.com/playlist?list=PLtest",
        "file:///video.mp4",
        "https://evil.example/watch?v=BaW_jenozKc",
        "https://youtube.com.evil.com/watch?v=BaW_jenozKc",
        "https://user:pass@youtube.com/watch?v=BaW_jenozKc",
        "--exec calc",
        "https://youtube.com:443/watch?v=BaW_jenozKc",
        "https://youtube.com/watch?v=short",
    ],
)
def test_reject_non_video_input(url):
    with pytest.raises(ValueError):
        normalize_url(url)


def test_quality_is_a_hard_upper_bound():
    assert format_selector("1080") == "bestvideo[height<=1080]+bestaudio/best[height<=1080]"
    assert format_selector("best") == "bestvideo+bestaudio/best"


def test_settings_debounce_and_restart(qtbot, tmp_path):
    store = SettingsStore(tmp_path)
    store.update(volume=23, auto_delete=True, quality="720", speed=1.5)
    assert not store.path.exists()
    qtbot.waitUntil(store.path.exists)
    restored = SettingsStore(tmp_path)
    assert restored.value.volume == 23
    assert restored.value.auto_delete is True
    assert restored.value.quality == "720"
    assert restored.value.speed == 1.5
    store.update(volume=42)
    store.flush()
    assert SettingsStore(tmp_path).value.volume == 42


def test_seek_preview_settings_validation_and_restart(qapp, tmp_path):
    store = SettingsStore(tmp_path)
    assert (store.value.seek_seconds, store.value.seek_large_seconds) == (5, 30)
    store.update(seek_seconds=17, seek_large_seconds=80, thumbnail_mode="precise")
    store.flush()
    restored = SettingsStore(tmp_path)
    assert (restored.value.seek_seconds, restored.value.seek_large_seconds) == (17, 80)
    assert restored.value.thumbnail_mode == "precise"
    record = json.loads(store.path.read_text(encoding="utf-8"))
    record.update(seek_seconds=4000, seek_large_seconds=-2, thumbnail_mode="bad")
    store.path.write_text(json.dumps(record))
    restored = SettingsStore(tmp_path)
    assert (restored.value.seek_seconds, restored.value.seek_large_seconds) == (3599, 3600)
    assert restored.value.thumbnail_mode == "fast"


def test_corrupt_settings_preserved_and_bad_types_defaulted(qapp, tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "settings.json").write_text("{broken", encoding="utf-8")
    store = SettingsStore(tmp_path)
    assert store.warnings
    assert next(data.glob("settings.corrupt-*.json")).read_text() == "{broken"
    assert store.value.quality == "1080"
    (data / "settings.json").write_text(json.dumps({"volume": True, "quality": "evil", "width": 1}))
    restored = SettingsStore(tmp_path)
    assert restored.value.volume == 70
    assert restored.value.width == 800
    assert restored.value.quality == "1080"


def test_cleanup_deletes_only_marked_task(qapp, tmp_path):
    store = TaskStore(tmp_path)
    task = Task.create("url", "video", "1080", tmp_path / "downloads")
    store.tasks[task.id] = task
    folder = store.prepare(task)
    (folder / "part.mp4.part").write_bytes(b"partial")
    unrelated = folder.parent / "my-video.mp4"
    unrelated.write_bytes(b"keep")
    assert store.cleanup(task)
    assert not folder.exists()
    assert unrelated.read_bytes() == b"keep"


def test_cleanup_refuses_unowned_folder(qapp, tmp_path):
    store = TaskStore(tmp_path)
    task = Task.create("url", "video", "1080", tmp_path)
    folder = Path(task.folder)
    folder.mkdir()
    keep = folder / "keep.txt"
    keep.write_text("keep")
    assert not store.cleanup(task)
    assert task.delete_pending
    assert task.state == "cleanup_pending"
    assert keep.exists()


def test_cleanup_lock_failure_is_recoverable(qapp, tmp_path, monkeypatch):
    store = TaskStore(tmp_path)
    task = Task.create("url", "video", "1080", tmp_path)
    store.tasks[task.id] = task
    store.prepare(task)
    import yt_player.core as core

    original = core.shutil.rmtree
    monkeypatch.setattr(
        core.shutil, "rmtree", lambda _p: (_ for _ in ()).throw(PermissionError("locked"))
    )
    assert not store.cleanup(task)
    recovered = TaskStore(tmp_path)
    assert recovered.tasks[task.id].delete_pending
    monkeypatch.setattr(core.shutil, "rmtree", original)
    assert recovered.cleanup(recovered.tasks[task.id])


def test_local_file_validation_and_interrupted_restart(qapp, tmp_path):
    store = TaskStore(tmp_path)
    task = Task.create("url", "video", "1080", tmp_path)
    store.tasks[task.id] = task
    folder = store.prepare(task)
    video = folder / "video.mkv"
    video.write_bytes(b"video")
    task.file, task.file_size, task.state = str(video), 5, "completed"
    assert store.local_file(task) == video
    video.write_bytes(b"truncated")
    assert store.local_file(task) is None
    task.state = "downloading"
    store.save()
    restored = TaskStore(tmp_path)
    assert restored.tasks[task.id].state == "cancelled"


def test_portable_folder_copy_keeps_default_paths_inside_new_copy(qapp, tmp_path):
    original, copied = tmp_path / "original", tmp_path / "copied"
    original.mkdir()
    settings = SettingsStore(original)
    settings.flush()
    store = TaskStore(original)
    task = Task.create("url", "video", "1080", original / "downloads")
    store.tasks[task.id] = task
    video = store.prepare(task) / "video.mkv"
    video.write_bytes(b"video")
    task.state, task.file, task.file_size = "completed", str(video), 5
    store.save()
    shutil.copytree(original, copied)
    restored = TaskStore(copied)
    assert SettingsStore(copied).value.download_dir == str(copied / "downloads")
    copied_task = restored.tasks[task.id]
    assert restored.local_file(copied_task) == copied / "downloads" / video.parent.name / video.name
    assert restored.cleanup(copied_task)
    assert video.exists()
