# self-scraper installer for Windows (PowerShell 5.1+)
# Usage (in this folder):  powershell -ExecutionPolicy Bypass -File .\install.ps1
# Re-running is safe. Use -Uninstall to remove the Claude Desktop registration.
param([switch]$Uninstall)

# Native tools (uv, git) write progress to stderr; PowerShell 5.1 would treat that as an error under "Stop".
# So keep "Continue" and check exit codes explicitly.
$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"
function Assert-Ok($what) { if ($LASTEXITCODE -ne 0) { Write-Host "[FAILED] $what (exit $LASTEXITCODE)"; exit 1 } }
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root
$Py = Join-Path $Root ".venv\Scripts\python.exe"

function Get-ClaudeConfigDirs {
    # Always register in the normal location (%APPDATA%\Claude) AND in any MSIX package location,
    # because which one the app reads depends on how Claude Desktop was installed.
    $std = Join-Path $env:APPDATA "Claude"
    New-Item -ItemType Directory -Force $std | Out-Null
    $dirs = @($std)
    $pkgRoot = Join-Path $env:LOCALAPPDATA "Packages"
    if (Test-Path $pkgRoot) {
        Get-ChildItem $pkgRoot -Directory -Filter "Claude_*" -ErrorAction SilentlyContinue | ForEach-Object {
            $d = Join-Path $_.FullName "LocalCache\Roaming\Claude"
            if (Test-Path $d) { $dirs += $d }
        }
    }
    # Newer Claude Desktop builds keep their config in %LOCALAPPDATA%\Claude-Data\<package id>\
    $dataRoot = Join-Path $env:LOCALAPPDATA "Claude-Data"
    if (Test-Path $dataRoot) {
        Get-ChildItem $dataRoot -Directory -ErrorAction SilentlyContinue | ForEach-Object { $dirs += $_.FullName }
    }
    return $dirs
}

if ($Uninstall) {
    foreach ($d in Get-ClaudeConfigDirs) { & $Py register_mcp.py (Join-Path $d "claude_desktop_config.json") --remove }
    Write-Host "Done. Restart the Claude desktop app."
    exit 0
}

Write-Host "[1/5] Checking uv (Python package manager)..."
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "      installing uv..."
    powershell -ExecutionPolicy Bypass -NoProfile -Command "irm https://astral.sh/uv/install.ps1 | iex"
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { throw "uv install failed. Open a new PowerShell window and run this script again." }
}

Write-Host "[2/5] Creating Python environment (.venv, Python 3.12)..."
if (-not (Test-Path $Py)) { uv venv .venv --python 3.12 --clear 2>&1 | ForEach-Object { "$_" }; Assert-Ok "venv" }
uv pip install --python $Py -r requirements.txt 2>&1 | ForEach-Object { "$_" }
Assert-Ok "package install"

Write-Host "[3/5] Browser for the stealth stage..."
$chrome = @(
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if ($chrome) {
    Write-Host "      Google Chrome found: $chrome  (no browser download needed)"
} else {
    Write-Host "      Chrome not found. Downloading bundled Chromium (~150MB)..."
    & $Py -m patchright install chromium 2>&1 | ForEach-Object { "$_" }; Assert-Ok "chromium download"
}

Write-Host "[4/5] Registering in Claude Desktop..."
foreach ($d in Get-ClaudeConfigDirs) { & $Py register_mcp.py (Join-Path $d "claude_desktop_config.json") }

Write-Host "[5/5] Self test..."
& $Py tests\test_offline.py 2>&1 | ForEach-Object { "$_" }
& $Py -c "import sys, logging; sys.path.insert(0,'.'); logging.getLogger('scrapling').setLevel(logging.WARNING); from scraper import core; r=core.fetch_auto('https://example.com', mode='http', max_chars=200); print('live fetch:', r['ok'], r.get('status'), r.get('title'))" 2>&1 | ForEach-Object { "$_" }

Write-Host ""
Write-Host "Install complete. Fully quit the Claude desktop app (tray icon > Quit) and open it again."
exit 0
