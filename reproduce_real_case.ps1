$ErrorActionPreference = 'Stop'
$scrPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $scrPython)) { throw 'Install the project environment first.' }
Push-Location $PSScriptRoot
try {
    $paper = Join-Path $PSScriptRoot '../论文/Insights into the mechanisms of NH3 inhibition on Cu-CHA SCR catalysts.pdf'
    $supplement = 'output/real_paper/downloads/supplementary.pdf'
    if (-not (Test-Path -LiteralPath $supplement)) { throw 'Download the supplementary PDF listed in REAL_PAPER_TEST.md first.' }
    & $scrPython -m scrtool extract $paper -o output/real_paper/main_extracted --paper-id 10.1038/s41467-026-72879-7
    if ($LASTEXITCODE) { throw 'Main paper extraction failed' }
    & $scrPython -m scrtool extract $supplement -o output/real_paper/supplement_extracted --paper-id 10.1038/s41467-026-72879-7
    if ($LASTEXITCODE) { throw 'Supplement extraction failed' }
    & $scrPython -m scrtool vector $paper --profile profiles/deka_2026_fig1a.json -o output/real_paper/fig1a
    if ($LASTEXITCODE) { throw 'Fig.1a failed' }
    & $scrPython -m scrtool vector $paper --profile profiles/deka_2026_fig1b.json -o output/real_paper/fig1b
    if ($LASTEXITCODE) { throw 'Fig.1b failed' }
    & $scrPython -m scripts.build_real_case
    if ($LASTEXITCODE) { throw 'Case report failed' }
    & $scrPython -m unittest discover -s tests -v
    if ($LASTEXITCODE) { throw 'Regression tests failed' }
} finally { Pop-Location }
