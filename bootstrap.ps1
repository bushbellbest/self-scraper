# self-scraper one-time bootstrap for a new Windows PC.
# Run once (Win+R or PowerShell):
#   powershell -ep bypass -c "irm https://raw.githubusercontent.com/bushbellbest/self-scraper/main/bootstrap.ps1 | iex"
# It downloads the code to %USERPROFILE%\self-scraper, installs it, registers it in Claude Desktop,
# and registers a hidden auto-update that runs at every Windows logon.

$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"
$Repo = "bushbellbest/self-scraper"
$Dir = Join-Path $env:USERPROFILE "self-scraper"

Write-Host "== self-scraper bootstrap =="
Write-Host "Install folder: $Dir"
New-Item -ItemType Directory -Force $Dir | Out-Null

# 1) download the latest code (zip, no git needed)
$tmp = Join-Path $env:TEMP ("self-scraper-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force $tmp | Out-Null
$zip = Join-Path $tmp "src.zip"
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -UseBasicParsing "https://github.com/$Repo/archive/refs/heads/main.zip" -OutFile $zip
    Expand-Archive -Path $zip -DestinationPath $tmp -Force
    $src = Get-ChildItem $tmp -Directory | Where-Object { $_.Name -like "self-scraper-*" } | Select-Object -First 1
    Copy-Item -Path (Join-Path $src.FullName "*") -Destination $Dir -Recurse -Force
} catch {
    Write-Host "[FAILED] download: $_"
    Read-Host "Press Enter to close"
    exit 1
} finally {
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
}

# 2) install (uv, python env, packages, browser check, Claude Desktop registration, self test)
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Dir "install.ps1")

# 3) register hidden auto-update at logon + remember installed version
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Dir "update.ps1") -RegisterOnly

Write-Host ""
Write-Host "Done. Fully quit the Claude desktop app (tray icon > Quit) and open it again."
Write-Host "From now on it updates itself at every Windows logon (log: $Dir\update.log)."
Read-Host "Press Enter to close"
