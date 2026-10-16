"""Create an isolated scientific runtime from the bundled dependency specification.
Usage: python install_engine_runtime.py --root <user-XRD-data-directory>
Run with Python 3.10 or 3.11; the OAW server can use a different interpreter.
"""
import argparse
from pathlib import Path
import os
import subprocess
import sys
import venv

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    if not (3, 10) <= sys.version_info[:2] < (3, 12):
        parser.error('Use Python 3.10 or 3.11 for the scientific runtime')
    destination = args.root.expanduser().resolve() / '.venv-xrd'
    venv.EnvBuilder(with_pip=True).create(destination)
    python = destination / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    requirements = Path(__file__).resolve().parent / 'OAW_XRDfit/requirements.txt'
    subprocess.run([str(python), '-m', 'pip', 'install', '-r', str(requirements)], check=True)
    probe = "import sys; sys.path.insert(0, sys.argv[1]); from OAW_XRDfit.src import WPEM; print(WPEM.__file__)"
    subprocess.run([str(python), '-c', probe, str(requirements.parent.parent)], check=True)
    print('OAW_XRD_PYTHON=' + str(python))

if __name__ == '__main__':
    main()
