"""Filename-based structure format detection shared by web endpoints."""

from __future__ import annotations

import re


def is_vasp_structure_filename(filename: str) -> bool:
    """Return whether *filename* is a conventional POSCAR/CONTCAR variant.

    VASP workflows commonly preserve the original POSCAR/CONTCAR prefix when
    creating variants, for example ``POSCAR_water_layer2`` or
    ``POSCAR-2_water6``. ASE cannot infer the VASP format from those names,
    so recognize a prefix followed by the usual filename delimiters.
    """
    return bool(re.fullmatch(r"(?:poscar|contcar)(?:[_.-].*)?", filename, re.IGNORECASE))
