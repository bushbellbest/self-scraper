# self-scraper auto-update. Runs hidden at every Windows logon (registered by bootstrap.ps1).
#   -RegisterOnly : only (re)create the logon entry and record the current version
#   -Force        : reinstall even if the version did not change
param([switch]$RegisterOnly, [switch]$Force)

$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"
$Repo = "bushbellbest/self-scraper"
$Dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Log = Join-Path $Dir "update.log"
$VerFile = Join-Path $Dir ".version"
$Py = Join-Path $Dir ".venv\Scripts\python.exe"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

function Log($m) {
    $line = "{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $m
    Add-Content -Path $Log -Value $line -Encoding UTF8
    Write-Host $line
}

function Get-LatestSha {
    try {
        $r = Invoke-RestMethod -UseBasicParsing -TimeoutSec 20 -Uri "https://api.github.com/repos/$Repo/commits/main" -Headers @{ "User-Agent" = "self-scraper-updater" }
        return $r.sha
    } catch { return $null }
}

function Register-Logon {
    $startup = [Environment]::GetFolderPath("Startup")
    $vbs = Join-Path $startup "self-scraper-autoupdate.vbs"
    $cmd = "powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ + (Join-Path $Dir "update.ps1") + """"
    $body = 'CreateObject("WScript.Shell").Run "' + $cmd.Replace('"', '""') + '", 0, False'
    Set-Content -Path $vbs -Value $body -Encoding ASCII
    Log "logon entry: $vbs"
}

function Register-Mcp {
    if (-not (Test-Path $Py)) { return }
    $dirs = @()
    $std = Join-Path $env:APPDATA "Claude"
    $pkgRoot = Join-Path $env:LOCALAPPDATA "Packages"
    if (Test-Path $pkgRoot) {
        Get-ChildItem $pkgRoot -Directory -Filter "Claude_*" -ErrorAction SilentlyContinue | ForEach-Object {
            $d = Join-Path $_.FullName "LocalCache\Roaming\Claude"
            if (Test-Path $d) { $dirs += $d }
        }
    }
    if ((Test-Path $std) -or $dirs.Count -eq 0) { $dirs += $std }
    foreach ($d in $dirs) {
        $out = & $Py (Join-Path $Dir "register_mcp.py") (Join-Path $d "claude_desktop_config.json") 2>&1
        Log ("mcp: " + ($out -join " "))
    }
}

if ($RegisterOnly) {
    Register-Logon
    $sha = Get-LatestSha
    if ($sha) { Set-Content -Path $VerFile -Value $sha -Encoding ASCII; Log "version recorded: $sha" }
    exit 0
}

# wait a little after logon so the network (e.g. phone hotspot) is up
Start-Sleep -Seconds 30
Log "== update check =="
Register-Logon

$sha = $null
for ($i = 0; $i -lt 5 -and -not $sha; $i++) {
    $sha = Get-LatestSha
    if (-not $sha) { Start-Sleep -Seconds 30 }
}
if (-not $sha) {
    Log "offline or GitHub unreachable; keeping current version"
    Register-Mcp
    exit 0
}

$cur = if (Test-Path $VerFile) { (Get-Content $VerFile -Raw).Trim() } else { "" }
if (-not $Force -and $cur -eq $sha -and (Test-Path $Py)) {
    Log "up to date ($sha)"
    Register-Mcp
    exit 0
}

Log "updating $cur -> $sha"
$tmp = Join-Path $env:TEMP ("self-scraper-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force $tmp | Out-Null
try {
    $zip = Join-Path $tmp "src.zip"
    Invoke-WebRequest -UseBasicParsing "https://github.com/$Repo/archive/$sha.zip" -OutFile $zip
    Expand-Archive -Path $zip -DestinationPath $tmp -Force
    $src = Get-ChildItem $tmp -Directory | Where-Object { $_.Name -like "self-scraper-*" } | Select-Object -First 1
    Copy-Item -Path (Join-Path $src.FullName "*") -Destination $Dir -Recurse -Force
} catch {
    Log "download failed: $_"
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
    Register-Mcp
    exit 1
}
Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue

$out = & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Dir "install.ps1") 2>&1
$out | ForEach-Object { Add-Content -Path $Log -Value ("    " + $_) -Encoding UTF8 }
if ($LASTEXITCODE -eq 0) {
    Set-Content -Path $VerFile -Value $sha -Encoding ASCII
    Log "update done ($sha). New code is used the next time the Claude desktop app starts."
} else {
    Log "install failed (exit $LASTEXITCODE); will retry next logon"
}
