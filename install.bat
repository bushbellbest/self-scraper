@echo off
cd /d "%~dp0"
echo Installing self-scraper... (log: install.log)
powershell -NoProfile -ExecutionPolicy Bypass -Command "& '%~dp0install.ps1' *>&1 | Tee-Object -FilePath '%~dp0install.log'"
echo.
echo Finished. You can close this window.
pause
