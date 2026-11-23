@echo off
cd /d "%~dp0"
if not exist ".venv-audio\Scripts\python.exe" (
  echo Run setup.cmd first. Python 3.11 x64 is required.
  exit /b 1
)
".venv-audio\Scripts\python.exe" -X utf8 voice_app.py %*
exit /b %errorlevel%
