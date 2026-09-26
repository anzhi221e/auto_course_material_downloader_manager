@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
title Fetch public courses
echo.
echo   处理 courses.json 里所有 public=true 的课程（对外公开、你未必正式选课）。
echo   这类课 Python 可以直连抓取，不需要浏览器。
echo   老师发布新的一周后重跑即可，已下过的会自动跳过。
echo.
python fetch_public_course.py --all-public
echo.
choice /c yn /n /m "Download for real? [y/n] "
if errorlevel 2 goto end
echo.
python fetch_public_course.py --all-public --apply
echo.
python sort_downloads.py --coverage
:end
echo.
pause
