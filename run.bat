@echo off
setlocal
cd /d "%~dp0"

if defined VIRTUAL_ENV goto active
if defined CONDA_PREFIX goto active
if not exist ".venv\Scripts\python.exe" call setup.bat
if errorlevel 1 exit /b %ERRORLEVEL%
".venv\Scripts\python.exe" run.py %*
exit /b %ERRORLEVEL%

:active
python run.py %*
exit /b %ERRORLEVEL%
