@echo off
chcp 65001 >nul
title QuantFlow
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_windows.ps1"
if errorlevel 1 pause
