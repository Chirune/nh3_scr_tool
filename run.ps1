# Prefer user Python, fall back to the Codex bundled interpreter on this machine.
$pythonCommand = Get-Command python -ErrorAction SilentlyContinue | Where-Object { $_.Source -notlike '*\Microsoft\WindowsApps\*' } | Select-Object -First 1
$localPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
$scrPython = if (Test-Path -LiteralPath $localPython) { $localPython } elseif ($pythonCommand) { $pythonCommand.Source } else { Join-Path $env:USERPROFILE '.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe' }
if (-not (Test-Path -LiteralPath $scrPython)) { throw 'Python is missing. Install Python 3.10+.' }
Push-Location $PSScriptRoot
try { & $scrPython -m scrtool @args; $scrExitCode = $LASTEXITCODE } finally { Pop-Location }
exit $scrExitCode
