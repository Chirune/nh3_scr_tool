"""Install in a repository-local virtual environment; never install globally."""
import argparse
from pathlib import Path
import subprocess
import sys
import venv


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--team-tools', action='store_true', help='also install collection-tool dependencies')
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    if not (3, 10) <= sys.version_info[:2] <= (3, 12):
        raise SystemExit('Please use Python 3.10, 3.11 or 3.12 (64-bit).')
    import tkinter  # fail before installing when Tk is missing
    folder = root / '.venv'
    python = folder / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
    if not python.is_file():
        venv.EnvBuilder(with_pip=True).create(folder)
    files = [root / 'requirements-workbench.txt']
    if args.team_tools:
        files.append(root / 'requirements.txt')
    for requirement in files:
        subprocess.run([str(python), '-m', 'pip', 'install', '-r', str(requirement)], check=True)
    subprocess.run([str(python), str(root / 'start_workbench.py'), '--check'], check=True)
    print('Ready. Open start_workbench.cmd, or run .venv/Scripts/python.exe start_workbench.py')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
