@echo off
setlocal
title Sentinel Flare - Local Source
echo.
echo ========================================
echo   Sentinel Flare - Local Source
echo ========================================
echo.
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Launch-Sentinel-Flare.ps1" %*
set EXITCODE=%ERRORLEVEL%
if not "%EXITCODE%"=="0" (
  echo.
  echo Sentinel Flare launcher exited with code %EXITCODE%.
  pause
)
exit /b %EXITCODE%
