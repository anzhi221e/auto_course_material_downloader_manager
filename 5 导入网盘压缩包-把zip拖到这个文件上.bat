@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
title Import course archive

set "ZIP=%~1"
if "%ZIP%"=="" set /p "ZIP=Drag the .zip here, or paste its full path: "
if "%ZIP%"=="" goto end

echo.
echo   1 = Language in Culture I
echo   2 = Ethnographic Research and Argumentation
echo   3 = Identity and Culture in the Age of AI
echo   4 = Perspectives
echo.
choice /c 1234 /n /m "Which course does this archive belong to? "
if errorlevel 4 set "C=Perspectives"& goto go
if errorlevel 3 set "C=Identity and Culture in the Age of AI"& goto go
if errorlevel 2 set "C=Ethnographic Research and Argumentation"& goto go
set "C=Language in Culture I"

:go
echo.
python ingest_zip.py "%ZIP%" --course "%C%" --list
echo.
python ingest_zip.py "%ZIP%" --course "%C%"
echo.
choice /c yn /n /m "Extract and file these? [y/n] "
if errorlevel 2 goto end
echo.
python ingest_zip.py "%ZIP%" --course "%C%" --apply

:end
echo.
pause
