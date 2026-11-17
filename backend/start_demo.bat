@echo off
cd /d "%~dp0"
python usp_web1.py --demo --host 127.0.0.1 --port 8080 --no-browser
pause
