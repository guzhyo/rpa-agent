@echo off
chcp 65001 >nul
cd /d "%~dp0"
.venv\Scripts\pip.exe install -r requirements.txt
pause
