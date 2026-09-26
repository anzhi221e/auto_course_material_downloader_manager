@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
title Undo last sort
python sort_downloads.py --undo
echo.
pause
