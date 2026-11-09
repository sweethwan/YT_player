"""Create a portable zip, excluding any runtime/test data from the distribution."""

import hashlib
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from yt_player import __version__

root = Path(__file__).resolve().parent.parent
distribution = root / "dist" / "YTPlayer"
target = root / "dist" / f"YTPlayer-{__version__}-win-x64.zip"
with ZipFile(target, "w", compression=ZIP_DEFLATED, compresslevel=5) as archive:
    for path in sorted(distribution.rglob("*")):
        relative = path.relative_to(distribution)
        if path.is_file() and relative.parts[0] not in {"data", "downloads", ".tmp"}:
            archive.write(path, Path("YTPlayer") / relative)
with target.open("rb") as source:
    digest = hashlib.file_digest(source, "sha256").hexdigest()
target.with_suffix(".zip.sha256").write_text(f"{digest}  {target.name}\n", encoding="ascii")
print(target)
