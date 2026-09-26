@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
title Rebuild readings index
python build_index.py
echo.
pause
