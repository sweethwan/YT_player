# Third-party software

This application launches these independently distributed tools. Their upstream
license terms continue to apply. Exact bundled versions and release/source links
are recorded in `tools/versions.json`; supplied notices remain in `tools/`.

| Component | License / source |
| --- | --- |
| Python | PSF License; https://www.python.org/ |
| PySide6 / Qt | LGPL-3.0 / GPL-3.0 / commercial; https://www.qt.io/qt-for-python |
| mpv and its bundled libraries | See upstream `Copyright`, archive notices and build details; https://github.com/mpv-player/mpv |
| yt-dlp | Unlicense, with bundled dependencies under their own licenses; https://github.com/yt-dlp/yt-dlp |
| Deno | MIT, with third-party dependencies; https://github.com/denoland/deno |
| FFmpeg essentials build | GPLv3 and bundled dependency notices; https://www.gyan.dev/ffmpeg/builds/ |
| PyInstaller bootloader | GPL-2.0-or-later with bootloader exception; https://pyinstaller.org/ |

The portable application uses shared Qt libraries and keeps external tools
replaceable in `tools`. Redistribution must retain notices and satisfy applicable
source-availability obligations of the chosen upstream binaries. Upstream release
pages and build README files identify corresponding sources and configurations.
