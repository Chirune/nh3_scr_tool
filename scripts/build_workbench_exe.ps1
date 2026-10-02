param([switch]$IncludeLocalAbstracts)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
$taskRelease = Join-Path $taskRoot 'artifact_work\NH3SCR_Workbench_0.8_Public'
$taskSample = Join-Path $taskRoot 'examples\demo_abstracts.json'
if ($IncludeLocalAbstracts) {
    $taskRelease = Join-Path $taskRoot 'artifact_work\NH3SCR_Workbench_0.8'
    $taskSample = Join-Path $taskRoot 'evaluation\abstract_collector_exe_20260929\records_with_abstracts.json'
    if (-not (Test-Path -LiteralPath $taskSample)) { throw 'Local abstracts file is missing' }
}
& $taskPython -m PyInstaller --noconfirm --onefile --windowed --name NH3SCR_Workbench --paths $taskRoot --paths $PSScriptRoot --distpath $taskRelease --workpath (Join-Path $taskRoot 'artifact_work\build08') --specpath (Join-Path $taskRoot 'artifact_work\build08') --add-data ($taskSample + ';data') --exclude-module pandas --exclude-module matplotlib --exclude-module torch (Join-Path $taskRoot 'scripts\workbench_gui.py')
if ($LASTEXITCODE -ne 0) { throw 'EXE build failed' }
$extensionRelease = Join-Path $taskRelease 'browser_extension'
New-Item -ItemType Directory -Path $extensionRelease -Force | Out-Null
foreach ($name in 'manifest.json','popup.html','popup.js') {
    Copy-Item -LiteralPath (Join-Path $taskRoot "browser_extension\$name") -Destination (Join-Path $extensionRelease $name) -Force
}
$quickstart = (Get-Content -LiteralPath (Join-Path $taskRoot 'docs\WORKBENCH_QUICKSTART.md') -Raw -Encoding UTF8).Replace('(BROWSER_CAPTURE.md)', '(浏览器插件说明.md)')
Set-Content -LiteralPath (Join-Path $taskRelease '简易操作说明.md') -Value $quickstart -Encoding UTF8
Copy-Item -LiteralPath (Join-Path $taskRoot 'docs\BROWSER_CAPTURE.md') -Destination (Join-Path $taskRelease '浏览器插件说明.md') -Force
Copy-Item -LiteralPath (Join-Path $taskRoot 'docs\RELEASE_0.8.md') -Destination (Join-Path $taskRelease '版本说明.md') -Force
