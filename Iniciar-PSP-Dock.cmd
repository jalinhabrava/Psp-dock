@echo off
cd /d "%~dp0"
if exist "D:\Python312\pythonw.exe" (
    start "" "D:\Python312\pythonw.exe" "%~dp0start_dock.py"
) else (
    start "" pyw -3.12 "%~dp0start_dock.py"
)
exit /b
