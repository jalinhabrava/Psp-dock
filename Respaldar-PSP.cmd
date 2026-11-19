@echo off
cd /d "%~dp0"
if exist "D:\Python312\python.exe" (
    "D:\Python312\python.exe" -m tools.psp.psp_backup --scope critical --run
) else (
    py -3.12 -m tools.psp.psp_backup --scope critical --run
)
echo.
pause
