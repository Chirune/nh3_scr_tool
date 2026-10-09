@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo First run: installing the local Python environment.
  where py >nul 2>nul
  if errorlevel 1 (
    echo Install Python 3.11 or 3.12 from python.org, then try again.
    pause
    exit /b 1
  )
  py scripts\setup_workbench.py --team-tools
  if errorlevel 1 (
    pause
    exit /b 1
  )
)
".venv\Scripts\python.exe" -B start_workbench.py --check >nul 2>nul
if errorlevel 1 (
  ".venv\Scripts\python.exe" scripts\setup_workbench.py --team-tools
  if errorlevel 1 (
    pause
    exit /b 1
  )
)
".venv\Scripts\python.exe" -B -X utf8 start_workbench.py %*
if errorlevel 1 pause
endlocal
