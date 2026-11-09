"""Publish staged program files without removing portable settings or downloads."""

import shutil
from pathlib import Path


def promote(stage: Path, destination: Path) -> None:
    if not (stage / "YTPlayer.exe").is_file():
        raise FileNotFoundError("Staged executable is missing")
    destination.mkdir(parents=True, exist_ok=True)
    for child in stage.iterdir():
        if child.name in {"data", "downloads", ".tmp"}:
            continue
        target = destination / child.name
        if child.is_dir():
            shutil.copytree(child, target, dirs_exist_ok=True)
        else:
            shutil.copy2(child, target)


if __name__ == "__main__":
    root = Path(__file__).resolve().parent.parent
    promote(root / ".tmp" / "portable-stage" / "YTPlayer", root / "dist" / "YTPlayer")
    print("Published program files; existing data and downloads preserved.")
