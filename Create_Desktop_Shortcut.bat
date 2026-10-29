@echo off
title Create VoiceCastTTS v2 Desktop Shortcut
cd /d "%~dp0"

rem Creates (or re-creates) a "VoiceCastTTS v2" shortcut on the Desktop that
rem runs Launch_VoiceCastTTS_v2.bat with the studio icon, starting its window minimized.
rem The icon file is web\voicecast.ico (rebuild it with: python make_icon.py).

if not exist "web\voicecast.ico" (
    echo [!] The icon file web\voicecast.ico is missing.
    if /i not "%~1"=="--quiet" pause
    exit /b 1
)

powershell -NoProfile -Command ^
  "$folder = (Get-Location).Path;" ^
  "$desktop = [Environment]::GetFolderPath('Desktop');" ^
  "$link = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $desktop 'VoiceCastTTS v2.lnk'));" ^
  "$link.TargetPath = Join-Path $folder 'Launch_VoiceCastTTS_v2.bat';" ^
  "$link.WorkingDirectory = $folder;" ^
  "$link.IconLocation = (Join-Path $folder 'web\voicecast.ico') + ',0';" ^
  "$link.WindowStyle = 7;" ^
  "$link.Description = 'Start VoiceCastTTS v2';" ^
  "$link.Save();" ^
  "Write-Host ('Shortcut saved: ' + (Join-Path $desktop 'VoiceCastTTS v2.lnk'))"
if errorlevel 1 (
    echo [!] The shortcut could not be created.
    if /i not "%~1"=="--quiet" pause
    exit /b 1
)

echo.
echo [OK] "VoiceCastTTS v2" is now on your Desktop. Double-click it to start the studio.
echo.
if /i not "%~1"=="--quiet" pause
