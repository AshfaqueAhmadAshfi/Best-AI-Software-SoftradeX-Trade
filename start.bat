@echo off
REM ============================================================
REM  Exness Trading Dashboard - one click launcher
REM ============================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"
title Exness Trading Dashboard

set "PY="
for %%P in (
  "C:\Python312\python.exe"
  "C:\Python311\python.exe"
  "C:\Python310\python.exe"
  "%~dp0.venv\Scripts\python.exe"
  "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
  "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
  "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
  "%USERPROFILE%\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
) do (
  if not defined PY if exist %%P set "PY=%%~P"
)

if not defined PY (
  py -3 -c "import sys" >nul 2>nul && set "PY=py -3"
)
if not defined PY (
  python -c "import sys" >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo.
  echo   Python 3 was not found on this PC.
  echo   Install it from https://www.python.org/downloads/ and run this file again.
  echo.
  pause
  exit /b 1
)

echo   Using Python: %PY%
%PY% -c "import fastapi, uvicorn" >nul 2>nul
if errorlevel 1 (
  echo   Installing required packages (first run only)...
  %PY% -m pip install --disable-pip-version-check -r requirements.txt
)

echo.
%PY% "backend\app.py"
echo.
echo   Dashboard stopped.
pause
