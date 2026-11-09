# Run: uv run pyinstaller --noconfirm YTPlayer.spec
from pathlib import Path
import shutil

root = Path(SPECPATH)
a = Analysis([str(root / 'scripts' / 'entrypoint.py')], pathex=[str(root)],
             binaries=[], datas=[], hiddenimports=[], hookspath=[], hooksconfig={},
             runtime_hooks=[], excludes=['PySide6.QtWebEngineCore', 'PySide6.QtQml',
                                         'PySide6.QtQuick', 'PySide6.QtPdf'])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='YTPlayer', debug=False,
          bootloader_ignore_signals=False, strip=False, upx=False, console=False)
collection = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='YTPlayer')
destination = Path(DISTPATH) / 'YTPlayer'
tools = root / 'tools'
required = ['yt-dlp.exe', 'mpv.exe', 'ffmpeg.exe', 'ffprobe.exe', 'deno.exe', 'versions.json']
for name in required:
    if not (tools / name).is_file():
        raise RuntimeError(f'Missing tools/{name}; run scripts/bootstrap_tools.py first')
shutil.copytree(tools, destination / 'tools', dirs_exist_ok=True,
                ignore=shutil.ignore_patterns('*.bak', '*.new.exe', 'update-journal.json'))
for name in ['README.md', 'THIRD_PARTY_NOTICES.md']:
    shutil.copy2(root / name, destination / name)
shutil.copytree(root / 'licenses', destination / 'licenses', dirs_exist_ok=True)
qt_license = root / '.venv' / 'Lib' / 'site-packages' / 'PySide6' / 'Qt' / 'licenses'
if qt_license.exists():
    shutil.copytree(qt_license, destination / 'licenses' / 'Qt', dirs_exist_ok=True)
