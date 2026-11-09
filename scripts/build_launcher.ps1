$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $compiler)) {
    $compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319\csc.exe'
}
if (-not (Test-Path -LiteralPath $compiler)) { throw '.NET Framework C# compiler not found' }
$launcherTemp = Join-Path $projectRoot '.tmp\launcher-build'
New-Item -ItemType Directory -Force -Path $launcherTemp | Out-Null
$env:TEMP = $launcherTemp
$env:TMP = $launcherTemp
& $compiler /nologo /target:winexe /optimize+ /codepage:65001 /reference:System.Windows.Forms.dll "/out:$projectRoot\YTPlayer.exe" "$PSScriptRoot\Launcher.cs"
if ($LASTEXITCODE -ne 0) { throw 'Root launcher compilation failed' }
Write-Output "Root launcher: $projectRoot\YTPlayer.exe"
