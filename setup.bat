@echo off
setlocal
cd /d "%~dp0"

set "PYTHON_CMD=python"
python --version >nul 2>nul
if errorlevel 1 set "PYTHON_CMD=py -3"
%PYTHON_CMD% --version >nul 2>nul
if errorlevel 1 (
  echo Error: Python was not found on PATH. Install Python 3.10 or newer.
  exit /b 1
)

%PYTHON_CMD% setup.py %*
exit /b %ERRORLEVEL%
