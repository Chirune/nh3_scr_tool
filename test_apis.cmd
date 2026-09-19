@echo off
setlocal
set "PROJECT_DIR=%~dp0"
set "PYTHON_EXE=%PROJECT_DIR%.venv\Scripts\pythonw.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=%PROJECT_DIR%.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" (
  echo ERROR: Missing project Python. 1>&2
  exit /b 2
)
pushd "%PROJECT_DIR%"
"%PYTHON_EXE%" scripts\test_apis_gui.py
set "EXIT_CODE=%ERRORLEVEL%"
popd
exit /b %EXIT_CODE%
