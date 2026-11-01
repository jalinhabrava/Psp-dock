@echo off
cd /d "%~dp0"
if exist "D:\Python312\python.exe" (
    "D:\Python312\python.exe" dock.py --demo
) else (
    py -3.12 dock.py --demo
)
pause
