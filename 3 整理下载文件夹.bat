@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
title Sort downloads into week folders
python sort_downloads.py
echo.
pause
