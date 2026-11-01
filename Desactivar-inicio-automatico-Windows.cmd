@echo off
cd /d "%~dp0"
if exist "D:\Python312\python.exe" (
    "D:\Python312\python.exe" setup_startup.py --remove
) else (
    py -3.12 setup_startup.py --remove
)
pause
