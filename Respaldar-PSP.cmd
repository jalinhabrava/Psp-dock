@echo off
cd /d "%~dp0"
python psp_backup.py --scope critical --run
echo.
pause
