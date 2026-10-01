$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
$taskRelease = Join-Path $taskRoot 'release\NH3SCR_AbstractCollector_0.2'
& $taskPython -m PyInstaller --noconfirm --onefile --windowed --name NH3SCR_AbstractCollector --paths $taskRoot --distpath $taskRelease --workpath (Join-Path $taskRoot 'build\abstract_collector') --specpath (Join-Path $taskRoot 'build') --exclude-module lxml --exclude-module html5lib --exclude-module pypdf --exclude-module numpy --exclude-module pandas --exclude-module matplotlib --exclude-module torch (Join-Path $taskRoot 'scripts\abstract_collector_gui.py')
if ($LASTEXITCODE -ne 0) { throw 'EXE build failed' }
Copy-Item -LiteralPath (Join-Path $taskRoot 'browser_extension') -Destination $taskRelease -Recurse -Force
Copy-Item -LiteralPath (Join-Path $taskRoot 'docs\ABSTRACT_COLLECTOR.md') -Destination (Join-Path $taskRelease '使用说明.md') -Force
