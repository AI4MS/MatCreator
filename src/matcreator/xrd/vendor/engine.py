"""Bundled OAW_XRDfit source location, independent of user data directories."""
from pathlib import Path

def engine_root():
    root = Path(__file__).resolve().parent / 'OAW_XRDfit'
    if not (root / 'src/WPEM.py').is_file():
        raise RuntimeError('OAW_XRDfit engine is missing from the plugin package')
    return root
