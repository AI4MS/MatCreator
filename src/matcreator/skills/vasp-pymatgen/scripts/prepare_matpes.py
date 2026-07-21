#!/usr/bin/env python
"""Generate and validate VASP static inputs for MLFF labeling.

Base set: pymatgen MatPESStaticSet (PBE). Overrides via user_incar_settings:
magnetism off by default (opt-in with --spin), no charge density output,
ENCUT=600, LMIXTAU off, LORBIT removed.
"""

from __future__ import annotations

BASE_OVERRIDES = {
    "ISPIN": 1,
    "ENCUT": 600,
    "LCHARG": False,
    "LAECHG": False,
    "LMIXTAU": False,
    "LORBIT": None,  # None => pymatgen removes the tag from INCAR
    "MAGMOM": None,
}

MIN_DISTANCE = 0.5        # Angstrom
MIN_VOL_PER_ATOM = 1.0    # Angstrom^3
MAX_VOL_PER_ATOM = 1000.0


def build_incar_overrides(spin: bool) -> dict:
    overrides = dict(BASE_OVERRIDES)
    if spin:
        overrides["ISPIN"] = 2
        del overrides["MAGMOM"]
    return overrides
