@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
title Setup wizard
echo.
echo   把这学期的 syllabus 都放进一个文件夹，然后运行这个向导。
echo   它会推断学期日历（含停课周）、识别每门课，并建好全部文件夹。
echo.
python setup_wizard.py
echo.
pause
