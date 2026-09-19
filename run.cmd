@echo off
setlocal
set "PROJECT_DIR=%~dp0"
set "PYTHON_EXE=%PROJECT_DIR%.venv\Scripts\python.exe"

if not exist "%PYTHON_EXE%" (
  where python >nul 2>nul
  if errorlevel 1 (
    echo ERROR: Python was not found. Install Python 3.10 or newer. 1>&2
    exit /b 2
  )
  set "PYTHON_EXE=python"
)

pushd "%PROJECT_DIR%"
"%PYTHON_EXE%" -m scrtool %*
set "EXIT_CODE=%ERRORLEVEL%"
popd
exit /b %EXIT_CODE%
