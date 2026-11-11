@echo off
setlocal
title Lingo Local GPU Scorer
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\serve-local.ps1" %*
if errorlevel 1 pause
