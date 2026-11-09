"""Download current stable Windows x64 tools with published SHA-256 verification.

Uses only the standard library: python scripts/bootstrap_tools.py
All downloads, archives and extraction stay within the project.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from time import time
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / ".tmp" / "tool-cache"
TOOLS = ROOT / "tools"
LICENSES = TOOLS / "licenses"


def read(url: str) -> bytes:
    with urlopen(
        Request(url, headers={"User-Agent": "YTPlayer-build/0.1"}), timeout=60
    ) as response:
        return response.read()


def release(repo: str) -> dict:
    data = json.loads(read(f"https://api.github.com/repos/{repo}/releases/latest"))
    if data.get("draft") or data.get("prerelease"):
        raise ValueError(f"{repo}: not a stable release")
    return data


def download(url: str, sha256: str, api_url: str | None = None) -> Path:
    name = url.rsplit("/", 1)[-1]
    path = CACHE / name
    if path.exists():
        with path.open("rb") as source:
            if hashlib.file_digest(source, "sha256").hexdigest() == sha256:
                return path
    partial = path.with_suffix(path.suffix + ".part")
    print(f"Downloading {name}", flush=True)
    with urlopen(
        Request(
            (api_url or url) + f"?ytplayer={int(time())}",
            headers={"User-Agent": "YTPlayer-build/0.1", "Accept": "application/octet-stream"},
        ),
        timeout=60,
    ) as response:
        with partial.open("wb") as output:
            shutil.copyfileobj(response, output)
    with partial.open("rb") as source:
        actual = hashlib.file_digest(source, "sha256").hexdigest()
    if actual != sha256:
        partial.unlink()
        raise ValueError(f"SHA-256 mismatch: {name}")
    partial.replace(path)
    return path


def github_asset(repo: str, select) -> tuple[dict, Path]:
    data = release(repo)
    asset = next(a for a in data["assets"] if select(a["name"]))
    digest = asset.get("digest", "")
    if not digest or not digest.startswith("sha256:"):
        raise ValueError(f"No published SHA-256 for {asset['name']}")
    return data, download(
        asset["browser_download_url"], digest.removeprefix("sha256:"), asset["url"]
    )


def extract(zip_path: Path, destination: Path):
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        for item in archive.infolist():
            if not (destination / item.filename).resolve().is_relative_to(destination.resolve()):
                raise ValueError("Unsafe archive path")
        archive.extractall(destination)


def ytdlp():
    data, path = github_asset("yt-dlp/yt-dlp", lambda name: name == "yt-dlp.exe")
    shutil.copy2(path, TOOLS / "yt-dlp.exe")
    (LICENSES / "yt-dlp.txt").write_bytes(
        read(f"https://raw.githubusercontent.com/yt-dlp/yt-dlp/{data['tag_name']}/LICENSE")
    )
    return {"name": "yt-dlp", "version": data["tag_name"], "source": data["html_url"]}


def deno():
    data, path = github_asset(
        "denoland/deno", lambda name: name == "deno-x86_64-pc-windows-msvc.zip"
    )
    extract(path, CACHE / "deno")
    shutil.copy2(CACHE / "deno" / "deno.exe", TOOLS / "deno.exe")
    (LICENSES / "deno.txt").write_bytes(
        read(f"https://raw.githubusercontent.com/denoland/deno/{data['tag_name']}/LICENSE.md")
    )
    return {"name": "deno", "version": data["tag_name"], "source": data["html_url"]}


def mpv():
    data, path = github_asset(
        "mpv-player/mpv",
        lambda name: name.startswith("mpv-v") and name.endswith("-x86_64-w64-mingw32-full.zip"),
    )
    folder = CACHE / "mpv"
    extract(path, folder)
    exe = next(folder.rglob("mpv.exe"))
    # Keep the complete upstream distribution including licenses, DLLs and build information.
    for child in exe.parent.iterdir():
        target = TOOLS / child.name
        if child.is_dir():
            shutil.copytree(child, target, dirs_exist_ok=True)
        else:
            shutil.copy2(child, target)
    (LICENSES / "mpv-copyright.txt").write_bytes(
        read(f"https://raw.githubusercontent.com/mpv-player/mpv/{data['tag_name']}/Copyright")
    )
    return {"name": "mpv", "version": data["tag_name"], "source": data["html_url"]}


def ffmpeg():
    version = read("https://www.gyan.dev/ffmpeg/builds/release-version").decode().strip()
    url = f"https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-{version}-essentials_build.zip"
    digest = read(url + ".sha256").decode().split()[0]
    path = download(url, digest)
    folder = CACHE / "ffmpeg"
    extract(path, folder)
    for name in ["ffmpeg.exe", "ffprobe.exe"]:
        shutil.copy2(next(folder.rglob(name)), TOOLS / name)
    upstream = next(folder.glob("ffmpeg-*"))
    for name in ["LICENSE", "README.txt"]:
        if (upstream / name).exists():
            shutil.copy2(upstream / name, LICENSES / f"ffmpeg-{name}")
    return {
        "name": "ffmpeg",
        "version": version,
        "source": "https://www.gyan.dev/ffmpeg/builds/",
        "archive_sha256": digest,
    }


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    LICENSES.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        records = list(pool.map(lambda operation: operation(), [ytdlp, mpv, deno, ffmpeg]))
    (TOOLS / "versions.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
    print(json.dumps(records, indent=2), flush=True)


if __name__ == "__main__":
    main()
