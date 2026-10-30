@echo off
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
.venv\Scripts\python.exe tests\test_offline.py > live_test_log.txt 2>&1
.venv\Scripts\python.exe live_test.py >> live_test_log.txt 2>&1
echo Live test finished. You can close this window.
pause
