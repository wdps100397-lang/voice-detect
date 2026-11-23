@echo off
cd /d "%~dp0"
py -3.11 -m venv .venv-audio
if errorlevel 1 exit /b 1
".venv-audio\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 exit /b 1
".venv-audio\Scripts\python.exe" scripts\download_model.py
exit /b %errorlevel%
