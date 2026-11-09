import hashlib
import io
from pathlib import Path

import pytest

import yt_player.updates as updates
from yt_player.processes import Tools
from yt_player.updates import expected_digest, install_release, recover_update


def test_sha_manifest_requires_exact_filename():
    digest = "a" * 64
    assert expected_digest(f"{digest}  yt-dlp.exe\n".encode()) == digest
    with pytest.raises(ValueError):
        expected_digest(f"{digest}  yt-dlp.exe.evil\n".encode())


def prepare(tmp_path, monkeypatch, digest=None):
    tools = Tools(tmp_path)
    tools.bin.mkdir()
    target = Path(tools.path("yt-dlp"))
    target.write_bytes(b"old")
    data = b"new"
    digest = digest or hashlib.sha256(data).hexdigest()
    monkeypatch.setattr(updates, "fetch", lambda _url: f"{digest}  yt-dlp.exe\n".encode())
    monkeypatch.setattr(updates, "urlopen", lambda *a, **kw: io.BytesIO(data))
    release = {
        "version": "2026.10.26",
        "sha": "sha",
        "exe": "https://github.com/yt-dlp/yt-dlp/releases/download/2026.10.26/yt-dlp.exe",
    }
    return tools, target, release


def test_bad_hash_keeps_existing_binary(qapp, tmp_path, monkeypatch):
    tools, target, release = prepare(tmp_path, monkeypatch, "0" * 64)
    with pytest.raises(ValueError, match="SHA-256"):
        install_release(tools, release, lambda _: None)
    assert target.read_bytes() == b"old"
    assert not (tools.bin / "yt-dlp.new.exe").exists()


def test_post_install_failure_rolls_back(qapp, tmp_path, monkeypatch):
    tools, target, release = prepare(tmp_path, monkeypatch)
    monkeypatch.setattr(
        updates,
        "executable_version",
        lambda path: release["version"] if path.name == "yt-dlp.new.exe" else "bad",
    )
    with pytest.raises(ValueError):
        install_release(tools, release, lambda _: None)
    assert target.read_bytes() == b"old"
    assert not (tools.bin / "update-journal.json").exists()


def test_verified_install_and_interrupted_recovery(qapp, tmp_path, monkeypatch):
    tools, target, release = prepare(tmp_path, monkeypatch)
    monkeypatch.setattr(updates, "executable_version", lambda _path: release["version"])
    assert install_release(tools, release, lambda _: None) == release["version"]
    assert target.read_bytes() == b"new"
    assert target.with_suffix(".exe.bak").read_bytes() == b"old"
    (tools.bin / "update-journal.json").write_text("{}")
    recover_update(tools)
    assert target.read_bytes() == b"old"


def test_deferred_update_applies_only_when_idle(qtbot, tmp_path, monkeypatch):
    from yt_player.core import SettingsStore
    from yt_player.updates import Updater

    busy, calls = [True], []
    updater = Updater(Tools(tmp_path), SettingsStore(tmp_path), lambda: busy[0])
    updater.current = "2026.01.01"
    updater.latest = "2026.10.26"
    updater.release = {"version": updater.latest}

    def install(tools, release, progress):
        calls.append(release)
        return release["version"]

    monkeypatch.setattr(updates, "install_release", install)
    updater.request_install()
    assert updater.pending and not updater.applying
    assert not calls
    busy[0] = False
    updater._maybe_apply()
    qtbot.waitUntil(lambda: updater.worker is None)
    assert calls == [{"version": "2026.10.26"}]
    assert updater.current == "2026.10.26"
    updater.poll.stop()


def test_automatic_check_uses_cache_manual_check_refreshes(qtbot, tmp_path, monkeypatch):
    from yt_player.core import SettingsStore, utc_now
    from yt_player.updates import Updater

    calls = []
    settings = SettingsStore(tmp_path)
    settings.value.last_update_check = utc_now()
    updater = Updater(Tools(tmp_path), settings, lambda: False)

    def latest():
        calls.append(True)
        return {"version": "2026.10.26"}

    monkeypatch.setattr(updates, "latest_release", latest)
    updater.check(force=False)
    assert not calls
    updater.check(force=True)
    qtbot.waitUntil(lambda: updater.worker is None)
    assert calls == [True]
    assert settings.value.latest_version == "2026.10.26"
    updater.poll.stop()
    settings.flush()
