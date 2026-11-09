"""Collect Qt/Python license texts next to the portable application sources."""

import sys
from pathlib import Path
from urllib.request import Request, urlopen

from PySide6 import __version__

root = Path(__file__).resolve().parent.parent
destination = root / "licenses"
destination.mkdir(exist_ok=True)
python_license = Path(sys.base_prefix) / "LICENSE.txt"
if python_license.exists():
    (destination / "Python.txt").write_bytes(python_license.read_bytes())
for name in ["LGPL-3.0-only.txt", "GPL-3.0-only.txt", "GPL-2.0-only.txt"]:
    target = destination / name
    if not target.exists():
        request = Request(
            f"https://raw.githubusercontent.com/qt/qtbase/v{__version__}/LICENSES/{name}",
            headers={"User-Agent": "YTPlayer-build/0.1"},
        )
        with urlopen(request, timeout=30) as response:
            target.write_bytes(response.read())
(destination / "Qt-sources.txt").write_text(
    f"Qt {__version__}: https://download.qt.io/official_releases/qt/"
    f"{'.'.join(__version__.split('.')[:2])}/{__version__}/submodules/\n"
    f"PySide/Shiboken {__version__}: "
    f"https://github.com/pyside/pyside-setup/tree/v{__version__}\n"
    "Qt libraries in _internal/PySide6 are dynamically linked and replaceable.\n",
    encoding="utf-8",
)
