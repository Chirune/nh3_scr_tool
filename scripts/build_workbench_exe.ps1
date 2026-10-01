$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
$taskRelease = Join-Path $taskRoot 'release\NH3SCR_Workbench_0.4'
$taskSample = Join-Path $taskRoot 'evaluation\abstract_collector_exe_20260929\records_with_abstracts.json'
if (-not (Test-Path -LiteralPath $taskSample)) {
    $taskSample = Join-Path $taskRoot 'examples\demo_abstracts.json'
}
& $taskPython -m PyInstaller --noconfirm --onefile --windowed --name NH3SCR_Workbench --paths $taskRoot --paths $PSScriptRoot --distpath $taskRelease --workpath (Join-Path $taskRoot 'build\workbench') --specpath (Join-Path $taskRoot 'build') --add-data ($taskSample + ';data') --exclude-module pandas --exclude-module matplotlib --exclude-module torch (Join-Path $taskRoot 'scripts\workbench_gui.py')
if ($LASTEXITCODE -ne 0) { throw 'EXE build failed' }
Copy-Item -LiteralPath (Join-Path $taskRoot 'browser_extension') -Destination $taskRelease -Recurse -Force
Copy-Item -LiteralPath (Join-Path $taskRoot 'docs\WORKBENCH_QUICKSTART.md') -Destination (Join-Path $taskRelease '简易操作说明.md') -Force
