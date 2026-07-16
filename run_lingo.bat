@echo off
setlocal

title Lingo RunPod Session
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\runpod-session.ps1" %*
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
  echo.
  echo The RunPod session ended with an error.
  pause
)

exit /b %EXIT_CODE%
