"""Regression tests for recognizing conventional VASP structure filenames."""

import sys
from pathlib import Path


WEB_DIR = Path(__file__).resolve().parents[1] / "web"
if str(WEB_DIR) not in sys.path:
    sys.path.insert(0, str(WEB_DIR))

from structure_formats import is_vasp_structure_filename


def test_recognizes_poscar_and_contcar_variants() -> None:
    assert is_vasp_structure_filename("POSCAR")
    assert is_vasp_structure_filename("POSCAR_water_layer2")
    assert is_vasp_structure_filename("POSCAR-2_water6")
    assert is_vasp_structure_filename("CONTCAR.relaxed")


def test_does_not_misidentify_unrelated_filenames() -> None:
    assert not is_vasp_structure_filename("my_POSCAR_backup")
    assert not is_vasp_structure_filename("POSCARwater")
    assert not is_vasp_structure_filename("POSCARwater.txt")
