param([switch]$WithMinerU)
$ErrorActionPreference = 'Stop'
$bundledPython = Join-Path $env:USERPROFILE '.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
$installedPython = Get-Command python -ErrorAction SilentlyContinue | Where-Object { $_.Source -notlike '*\Microsoft\WindowsApps\*' } | Select-Object -First 1
$bootstrapPython = if (Test-Path -LiteralPath $bundledPython) { $bundledPython } elseif ($installedPython) { $installedPython.Source } else { throw 'Install Python 3.10-3.13 first.' }
Push-Location $PSScriptRoot
try {
    if (-not (Test-Path -LiteralPath '.venv/Scripts/python.exe')) {
        & $bootstrapPython -m venv .venv
        if ($LASTEXITCODE) { throw 'Failed to create virtual environment' }
    }
    $scrPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
    & $scrPython -m pip install -r requirements.txt
    if ($LASTEXITCODE) { throw 'Core dependencies failed' }
    if ($WithMinerU) {
        & $scrPython -m pip install '../MinerU-master/MinerU-master[pipeline]' six
        if ($LASTEXITCODE) { throw 'MinerU installation failed' }
    }
    & $scrPython -m pip check
    if ($LASTEXITCODE) { throw 'Dependency compatibility check failed' }
} finally { Pop-Location }
