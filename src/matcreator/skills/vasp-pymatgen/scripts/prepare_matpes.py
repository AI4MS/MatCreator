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


def check_structure(structure) -> str | None:
    import numpy as np

    n = len(structure)
    vol_per_atom = structure.volume / n
    if not (MIN_VOL_PER_ATOM <= vol_per_atom <= MAX_VOL_PER_ATOM):
        return (
            f"volume per atom {vol_per_atom:.2f} A^3 outside "
            f"[{MIN_VOL_PER_ATOM}, {MAX_VOL_PER_ATOM}]"
        )
    if n > 1:
        dm = structure.distance_matrix
        dmin = dm[np.triu_indices(n, k=1)].min()
        if dmin < MIN_DISTANCE:
            return f"minimum interatomic distance {dmin:.2f} A < {MIN_DISTANCE} A"
    return None
