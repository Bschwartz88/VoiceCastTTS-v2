@echo off
if /i "%~1"=="--server" goto server
start "VoiceCastTTS v2" /min "%ComSpec%" /d /c ""%~f0" --server"
exit /b

:server
title VoiceCastTTS v2
cd /d "%~dp0"
set "PYTHON=.venv\Scripts\python.exe"
if not exist "%PYTHON%" goto setup
if not exist ".venv\installed.ok" goto setup
echo Starting VoiceCastTTS v2 in Chrome...
echo Keep this minimized window open while using v2.
echo Close it when finished to stop the v2 server.
"%PYTHON%" -E -s -X utf8 launch_v2.py
if errorlevel 1 (
    echo VoiceCastTTS v2 could not start. See the message above.
    powershell -NoProfile -Command "Add-Type -AssemblyName PresentationFramework; [void][System.Windows.MessageBox]::Show('VoiceCastTTS v2 could not start. Open its window on the taskbar to see why.', 'VoiceCastTTS v2', 'OK', 'Warning')"
    pause
)

exit /b
:setup
echo VoiceCastTTS v2 needs its private Python environment. Run Install_Dependencies.bat first.
powershell -NoProfile -Command "Add-Type -AssemblyName PresentationFramework; [void][System.Windows.MessageBox]::Show('Run Install_Dependencies.bat in the v2 project first.', 'VoiceCastTTS v2 setup', 'OK', 'Warning')"
pause
exit /b 1
