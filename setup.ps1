# One-shot setup on a new Windows laptop. From this folder, in PowerShell:
#     powershell -ExecutionPolicy Bypass -File setup.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Have($cmd) { [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }
function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
}

Write-Host "== System tools" -ForegroundColor Cyan
if (-not (Have python)) { winget install -e --id Python.Python.3.11 --accept-source-agreements --accept-package-agreements; Refresh-Path }
if (-not (Have ffmpeg)) { winget install -e --id Gyan.FFmpeg --accept-source-agreements --accept-package-agreements; Refresh-Path }
if (-not (Have node))   { winget install -e --id OpenJS.NodeJS.LTS --accept-source-agreements --accept-package-agreements; Refresh-Path }

Write-Host "== Python packages" -ForegroundColor Cyan
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

Write-Host "== Sound effects and music (free Mixkit assets)" -ForegroundColor Cyan
python fetch_sfx.py
python fetch_bgm.py

Write-Host "== Checking everything" -ForegroundColor Cyan
python doctor.py
