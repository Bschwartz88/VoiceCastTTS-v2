@echo off
setlocal
rem Require an existing bootstrap; do not let the launcher install global Python.
set "PYTHON_MANAGER_AUTOMATIC_INSTALL=false"
set "PYLAUNCHER_ALLOW_INSTALL="
set "PYLAUNCHER_ALWAYS_INSTALL="
title Set up VoiceCastTTS v2
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 goto python
py -3 -E -s setup_v2.py %*
if errorlevel 1 goto failed
goto done
:python
where python >nul 2>nul
if errorlevel 1 goto missing
python -E -s setup_v2.py %*
if errorlevel 1 goto failed
:done
echo.
echo Setup complete. Run Launch_VoiceCastTTS_v2.bat.
if /i not "%~1"=="--help" pause
exit /b 0
:missing
echo Install 64-bit Python 3.10 or newer from python.org, then run this file again.
echo Python is only used to prepare the private v2 runtime here.
pause
exit /b 1
:failed
echo.
echo Setup did not finish. Read the message above before retrying.
pause
exit /b 1
