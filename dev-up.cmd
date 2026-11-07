@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\dev-stack.ps1" %*
if errorlevel 1 pause
