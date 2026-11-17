@echo off
cd /d "%~dp0"
set /p "DNM_BIND=IP LAN may tinh: "
if not defined DNM_BIND exit /b 1
set /p "DNM_PASSWORD=Mat khau demo (khong dung mat khau that): "
if not defined DNM_PASSWORD exit /b 1
python usp_web1.py --demo --host "%DNM_BIND%" --port 8080 --password "%DNM_PASSWORD%" --no-browser
pause
