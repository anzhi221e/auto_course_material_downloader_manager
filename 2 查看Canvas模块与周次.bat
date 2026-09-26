@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
title Canvas modules overview
echo.
echo   Canvas 的下载链接不带签名，只认浏览器登录会话，Python 下不了。
echo   下载请用 canvas_snippet.js（DOWNLOAD_FILES = true），
echo   下完再跑「3 整理下载文件夹.bat」归位。
echo.
echo   这里只做诊断：列出各课模块名和识别到的周次。
echo.
python canvas_fetch.py --show-modules
echo.
pause
