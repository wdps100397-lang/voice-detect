@echo off
call "%~dp0voice.cmd" ui %*
if errorlevel 1 pause
