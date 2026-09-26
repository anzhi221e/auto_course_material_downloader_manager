@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
title Reorganize by syllabus
python reorganize.py
echo.
choice /c yn /n /m "Apply these moves? [y/n] "
if errorlevel 2 goto end
echo.
python reorganize.py --apply
:end
echo.
echo ===== Coverage =====
python sort_downloads.py --coverage
echo.
pause
