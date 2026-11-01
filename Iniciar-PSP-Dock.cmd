@echo off
cd /d "%~dp0"
if exist "D:\Python312\python.exe" (
    "D:\Python312\python.exe" start_dock.py
) else (
    py -3.12 start_dock.py
)
pause
