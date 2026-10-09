"""One non-recursive suite, in its own process and import namespace."""
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
name=sys.argv[1]
if name not in {'catalyst_workbench','catalyst_workbench/gateway','catalyst_workbench/digitizer','tests'}:
    raise SystemExit('Unknown test suite')
folder=ROOT/name
code=ROOT/'catalyst_workbench'
sys.path[:0]=[str(folder),str(code),str(code/'digitizer'),str(ROOT)]
names=[p.stem for p in sorted(folder.glob('test*.py'))]
if name.endswith('/digitizer'):names=['digitizer.'+n for n in names]
suite=unittest.defaultTestLoader.loadTestsFromNames(names)
result=unittest.TextTestRunner(verbosity=1).run(suite)
raise SystemExit(not result.wasSuccessful())
