#!/usr/bin/env python
"""Generate and validate VASP static inputs for MLFF labeling.

Base set: pymatgen MatPESStaticSet (PBE). Overrides via user_incar_settings:
magnetism off by default (opt-in with --spin), no charge density output,
ENCUT=600, LMIXTAU off, LORBIT removed.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

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

POTCAR_FUNCTIONALS = ("PBE_64", "PBE_54", "PBE")


def build_incar_overrides(spin: bool, extra: dict | None = None) -> dict:
    overrides = dict(BASE_OVERRIDES)
    if spin:
        overrides["ISPIN"] = 2
        del overrides["MAGMOM"]
    if extra:
        protected = set(BASE_OVERRIDES)
        clashes = protected & set(extra)
        if clashes:
            raise ValueError(
                f"--incar may not override protected keys: {sorted(clashes)} "
                "(managed by --spin and the labeling policy)"
            )
        overrides.update(extra)
    return overrides


def parse_incar_kv(pairs: list[str]) -> dict:
    """Parse KEY=VALUE strings; values become int/float/bool when possible."""
    extra = {}
    for pair in pairs:
        key, sep, raw = pair.partition("=")
        if not sep or not key or not raw:
            raise ValueError(f"invalid --incar {pair!r}, expected KEY=VALUE")
        if raw.lower() in ("true", ".true."):
            value = True
        elif raw.lower() in ("false", ".false."):
            value = False
        else:
            try:
                value = int(raw)
            except ValueError:
                try:
                    value = float(raw)
                except ValueError:
                    value = raw
        extra[key.upper()] = value
    return extra


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


def parse_frames_slice(spec: str) -> slice:
    parts = spec.split(":")
    if len(parts) > 3:
        raise ValueError(f"invalid frame slice: {spec!r}")
    try:
        vals = [int(p) if p else None for p in parts]
    except ValueError as exc:
        raise ValueError(f"invalid frame slice: {spec!r}") from exc
    vals += [None] * (3 - len(vals))
    return slice(*vals)


def load_frames(structure_file: str, frames_spec: str | None = None) -> list:
    from ase.io import read as ase_read
    from pymatgen.io.ase import AseAtomsAdaptor

    images = ase_read(structure_file, index=":")
    if frames_spec:
        images = images[parse_frames_slice(frames_spec)]
    return [AseAtomsAdaptor.get_structure(a) for a in images]


def validate_incar(incar: dict, spin: bool, extra: dict | None = None) -> list[str]:
    errors = []
    expected = {
        "ISPIN": 2 if spin else 1,
        "ENCUT": 600,
        "LCHARG": False,
        "LAECHG": False,
        "LMIXTAU": False,
    }
    if extra:
        expected.update(extra)
    for key, val in expected.items():
        if incar.get(key) != val:
            errors.append(f"INCAR {key}={incar.get(key)!r}, expected {val!r}")
    if "LORBIT" in incar:
        errors.append("INCAR contains LORBIT (should be removed)")
    if spin and "MAGMOM" not in incar:
        errors.append("INCAR missing MAGMOM (required with --spin)")
    if not spin and "MAGMOM" in incar:
        errors.append("INCAR contains MAGMOM (should be removed when ISPIN=1)")
    return errors


def validate_dir(calc_dir: Path, spin: bool, extra: dict | None = None) -> list[str]:
    from pymatgen.io.vasp.inputs import Incar, Poscar, Potcar

    calc_dir = Path(calc_dir)
    errors = [
        f"missing {name}"
        for name in ("INCAR", "POSCAR", "POTCAR")
        if not (calc_dir / name).is_file()
    ]
    if errors:
        return errors

    incar = Incar.from_file(calc_dir / "INCAR")
    errors.extend(validate_incar(incar, spin, extra))

    if "KSPACING" in incar and (calc_dir / "KPOINTS").is_file():
        errors.append("KPOINTS file present although KSPACING is set")

    poscar = Poscar.from_file(calc_dir / "POSCAR")
    potcar = Potcar.from_file(str(calc_dir / "POTCAR"))
    potcar_elements = [p.element for p in potcar]
    if potcar_elements != poscar.site_symbols:
        errors.append(
            f"POTCAR elements {potcar_elements} != POSCAR {poscar.site_symbols}"
        )
    return errors


def generate_inputs(
    structure,
    outdir: Path,
    spin: bool,
    extra: dict | None = None,
    potcar_functional: str = "PBE_64",
) -> None:
    from pymatgen.io.vasp.sets import MatPESStaticSet

    vis = MatPESStaticSet(
        structure,
        user_incar_settings=build_incar_overrides(spin, extra),
        user_potcar_functional=potcar_functional,
    )
    vis.write_input(str(outdir))


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "structure_file",
        nargs="?",
        help="structure file readable by ASE (cif, POSCAR, extxyz, ...)",
    )
    parser.add_argument("-o", "--output-dir", default="matpes_job")
    parser.add_argument(
        "--spin",
        action="store_true",
        help="enable spin polarization (ISPIN=2, keep MatPES MAGMOM guesses)",
    )
    parser.add_argument(
        "--frames",
        help="frame slice START:STOP:STEP for multi-frame files",
    )
    parser.add_argument(
        "--incar",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="extra INCAR override, repeatable (e.g. --incar NCORE=4); "
        "policy-managed keys (ISPIN, ENCUT, LCHARG, ...) are rejected",
    )
    parser.add_argument(
        "--potcar",
        choices=POTCAR_FUNCTIONALS,
        default="PBE_64",
        help="POTCAR functional/library generation (default: PBE_64, "
        "the MatPES recommendation; use PBE_54/PBE for older libraries)",
    )
    parser.add_argument(
        "--validate-only",
        nargs="+",
        metavar="DIR",
        help="only validate existing calc dirs, do not generate",
    )
    args = parser.parse_args(argv)
    if not args.validate_only and not args.structure_file:
        parser.error("structure_file is required unless --validate-only is given")
    return args


def main(argv=None) -> int:
    args = parse_args(argv)

    try:
        extra = parse_incar_kv(args.incar)
        build_incar_overrides(args.spin, extra)  # reject protected keys early
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.validate_only:
        n_failed = 0
        for d in args.validate_only:
            errs = validate_dir(Path(d), args.spin, extra)
            if errs:
                n_failed += 1
                for e in errs:
                    print(f"[FAIL] {d}: {e}")
            else:
                print(f"[OK] {d}")
        return 1 if n_failed else 0

    if not os.environ.get("PMG_VASP_PSP_DIR"):
        print(
            "error: PMG_VASP_PSP_DIR is not set (required for POTCAR generation)",
            file=sys.stderr,
        )
        return 1

    structures = load_frames(args.structure_file, args.frames)
    outdir = Path(args.output_dir)
    multi = len(structures) > 1

    generated, skipped, failed = [], [], []
    for i, structure in enumerate(structures):
        target = outdir / f"frame_{i:04d}" if multi else outdir
        warning = check_structure(structure)
        if warning:
            print(f"[SKIP] frame {i}: {warning}")
            skipped.append(i)
            continue
        generate_inputs(structure, target, args.spin, extra, args.potcar)
        errs = validate_dir(target, args.spin, extra)
        if errs:
            for e in errs:
                print(f"[FAIL] {target}: {e}")
            failed.append(str(target))
        else:
            generated.append(str(target))

    print(
        f"\nSummary: {len(generated)} generated, "
        f"{len(skipped)} skipped, {len(failed)} failed"
    )
    if failed or not generated:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
