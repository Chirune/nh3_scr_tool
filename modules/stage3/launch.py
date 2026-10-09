from pathlib import Path
import subprocess
import sys

root=Path(__file__).resolve().parents[2]
raise SystemExit(subprocess.call([sys.executable,str(root/'start_workbench.py'),'--stage','3',*sys.argv[1:]]))
