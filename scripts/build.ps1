param([switch]$SkipTools, [switch]$NoArchive)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$env:UV_CACHE_DIR = Join-Path $projectRoot '.tmp\uv-cache'
$env:PYINSTALLER_CONFIG_DIR = Join-Path $projectRoot '.tmp\pyinstaller-cache'
$env:TEMP = Join-Path $projectRoot '.tmp\build-temp'
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Force -Path $env:TEMP | Out-Null
$runningPlayer = Get-Process YTPlayer -ErrorAction SilentlyContinue | Where-Object {
    $_.Path -eq (Join-Path $projectRoot 'dist\YTPlayer\YTPlayer.exe') -or
    $_.Path -eq (Join-Path $projectRoot 'YTPlayer.exe')
}
if ($runningPlayer) { throw 'Close YT Player before building. Existing application data is preserved.' }
uv sync --frozen --group dev
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
if (-not $SkipTools) {
    uv run python scripts/bootstrap_tools.py
    if ($LASTEXITCODE -ne 0) { throw 'Tool download failed' }
}
uv run python scripts/bundle_licenses.py
if ($LASTEXITCODE -ne 0) { throw 'License collection failed' }
uv run pyinstaller --noconfirm --distpath .tmp/portable-stage YTPlayer.spec
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed' }
uv run python scripts/promote_build.py
if ($LASTEXITCODE -ne 0) { throw 'Publishing staged files failed' }
Write-Output "Portable app: $projectRoot\dist\YTPlayer\YTPlayer.exe"
& (Join-Path $PSScriptRoot 'build_launcher.ps1')
if (-not $NoArchive) {
    uv run python scripts/archive.py
    if ($LASTEXITCODE -ne 0) { throw 'Portable zip creation failed' }
}
