# MatPES MLFF Labeling Input Script Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A standalone CLI script that converts structure files to VASP static inputs via `MatPESStaticSet` with MLFF-labeling INCAR overrides, and validates the generated inputs.

**Architecture:** Single module `prepare_matpes.py` with pure helper functions (override builder, structure sanity check, frame loader, INCAR validator) composed by a `main()` CLI. Tests live next to the script in `tests/`; POTCAR-dependent tests skip when `PMG_VASP_PSP_DIR` is unset.

**Tech Stack:** Python 3.10+, pymatgen (`MatPESStaticSet`, `Incar`, `Poscar`, `Potcar`), ASE (`ase.io.read`), pytest.

**Spec:** `docs/superpowers/specs/2026-07-21-matpes-labeling-script-design.md`

## Global Constraints

- INCAR overrides (non-spin): `ISPIN=1`, `ENCUT=600`, `LCHARG=False`, `LAECHG=False`, `LMIXTAU=False`, `LORBIT` removed, `MAGMOM` removed.
- `--spin`: `ISPIN=2`, MAGMOM kept (MatPES defaults), all other overrides unchanged.
- ENAUG untouched (dead parameter under PREC=Accurate; keep upstream fidelity). KSPACING=0.22 untouched. xc_functional fixed to PBE.
- Structure sanity thresholds: min interatomic distance < 0.5 Å, or volume/atom outside [1, 1000] Å³ → warn + skip frame, never abort the batch.
- Exit 1 if any validation failed or all frames were skipped; exit 0 otherwise.
- `PMG_VASP_PSP_DIR` must be set for generation (not for `--validate-only`); error out early if missing.
- All commands below run from the repo root `/home/fish/MatCreator`.

---

### Task 1: Scaffold + INCAR override builder

**Files:**
- Create: `src/matcreator/skills/vasp-pymatgen/scripts/prepare_matpes.py`
- Create: `src/matcreator/skills/vasp-pymatgen/scripts/tests/conftest.py`
- Test: `src/matcreator/skills/vasp-pymatgen/scripts/tests/test_prepare_matpes.py`

**Interfaces:**
- Produces: `build_incar_overrides(spin: bool) -> dict` — returns the `user_incar_settings` dict. Keys with value `None` mean "remove this tag" (pymatgen semantics). Module constants `BASE_OVERRIDES`, `MIN_DISTANCE`, `MIN_VOL_PER_ATOM`, `MAX_VOL_PER_ATOM`.

- [ ] **Step 1: Create conftest.py so tests can import the script**

```python
# src/matcreator/skills/vasp-pymatgen/scripts/tests/conftest.py
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
```

- [ ] **Step 2: Write the failing tests**

```python
# src/matcreator/skills/vasp-pymatgen/scripts/tests/test_prepare_matpes.py
import prepare_matpes as pm


class TestBuildIncarOverrides:
    def test_default_no_spin(self):
        ov = pm.build_incar_overrides(spin=False)
        assert ov["ISPIN"] == 1
        assert ov["ENCUT"] == 600
        assert ov["LCHARG"] is False
        assert ov["LAECHG"] is False
        assert ov["LMIXTAU"] is False
        assert ov["LORBIT"] is None  # None => pymatgen removes the tag
        assert ov["MAGMOM"] is None

    def test_spin_enabled(self):
        ov = pm.build_incar_overrides(spin=True)
        assert ov["ISPIN"] == 2
        assert "MAGMOM" not in ov  # keep MatPES default MAGMOM guesses
        assert ov["LORBIT"] is None
        assert ov["ENCUT"] == 600

    def test_does_not_mutate_base(self):
        pm.build_incar_overrides(spin=True)
        assert pm.BASE_OVERRIDES["ISPIN"] == 1
        assert "MAGMOM" in pm.BASE_OVERRIDES
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest src/matcreator/skills/vasp-pymatgen/scripts/tests/test_prepare_matpes.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'prepare_matpes'`

- [ ] **Step 4: Write minimal implementation**

```python
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest src/matcreator/skills/vasp-pymatgen/scripts/tests/test_prepare_matpes.py -v`
Expected: 3 PASSED

- [ ] **Step 6: Commit**

```bash
git add src/matcreator/skills/vasp-pymatgen/scripts
git commit -m "feat(vasp-pymatgen): add MatPES INCAR override builder for MLFF labeling"
```

---

### Task 2: Structure sanity check

**Files:**
- Modify: `src/matcreator/skills/vasp-pymatgen/scripts/prepare_matpes.py`
- Test: `src/matcreator/skills/vasp-pymatgen/scripts/tests/test_prepare_matpes.py`

**Interfaces:**
- Consumes: constants `MIN_DISTANCE`, `MIN_VOL_PER_ATOM`, `MAX_VOL_PER_ATOM` from Task 1.
- Produces: `check_structure(structure: pymatgen.core.Structure) -> str | None` — returns a human-readable warning string if the frame should be skipped, else `None`.

- [ ] **Step 1: Write the failing tests**

Append to `test_prepare_matpes.py`:

```python
from pymatgen.core import Lattice, Structure


def make_si(a: float = 5.43) -> Structure:
    return Structure(
        Lattice.cubic(a),
        ["Si", "Si"],
        [[0.0, 0.0, 0.0], [0.25, 0.25, 0.25]],
    )


class TestCheckStructure:
    def test_good_structure_passes(self):
        assert pm.check_structure(make_si()) is None

    def test_overlapping_atoms_flagged(self):
        s = Structure(
            Lattice.cubic(5.43),
            ["Si", "Si"],
            [[0.0, 0.0, 0.0], [0.01, 0.0, 0.0]],
        )
        msg = pm.check_structure(s)
        assert msg is not None and "distance" in msg

    def test_huge_volume_per_atom_flagged(self):
        s = Structure(Lattice.cubic(30.0), ["Si"], [[0.0, 0.0, 0.0]])
        msg = pm.check_structure(s)
        assert msg is not None and "volume" in msg

    def test_tiny_volume_per_atom_flagged(self):
        s = Structure(Lattice.cubic(0.9), ["Si"], [[0.0, 0.0, 0.0]])
        msg = pm.check_structure(s)
        assert msg is not None and "volume" in msg
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest src/matcreator/skills/vasp-pymatgen/scripts/tests/test_prepare_matpes.py -v -k CheckStructure`
Expected: FAIL with `AttributeError: module 'prepare_matpes' has no attribute 'check_structure'`

- [ ] **Step 3: Write minimal implementation**

Append to `prepare_matpes.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest src/matcreator/skills/vasp-pymatgen/scripts/tests/test_prepare_matpes.py -v -k CheckStructure`
Expected: 4 PASSED

- [ ] **Step 5: Commit**

```bash
git add src/matcreator/skills/vasp-pymatgen/scripts
git commit -m "feat(vasp-pymatgen): add structure sanity check for labeling frames"
```

---

### Task 3: Frame loading and slicing

**Files:**
- Modify: `src/matcreator/skills/vasp-pymatgen/scripts/prepare_matpes.py`
- Test: `src/matcreator/skills/vasp-pymatgen/scripts/tests/test_prepare_matpes.py`

**Interfaces:**
- Produces:
  - `parse_frames_slice(spec: str) -> slice` — parses `"START:STOP:STEP"` (any part may be empty); raises `ValueError` on malformed input.
  - `load_frames(structure_file: str, frames_spec: str | None = None) -> list[Structure]` — reads all frames via ASE, applies optional slice, converts to pymatgen Structures.

- [ ] **Step 1: Write the failing tests**

Append to `test_prepare_matpes.py`:

```python
import pytest
from ase.build import bulk
from ase.io import write as ase_write


@pytest.fixture
def traj_file(tmp_path):
    atoms = bulk("Si", "diamond", a=5.43)
    images = []
    for i in range(5):
        img = atoms.copy()
        img.positions[0, 0] += 0.01 * i
        images.append(img)
    path = tmp_path / "traj.extxyz"
    ase_write(path, images)
    return str(path)


class TestParseFramesSlice:
    def test_full_spec(self):
        assert pm.parse_frames_slice("0:10:2") == slice(0, 10, 2)

    def test_open_ended(self):
        assert pm.parse_frames_slice("2:") == slice(2, None, None)

    def test_step_only(self):
        assert pm.parse_frames_slice("::5") == slice(None, None, 5)

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            pm.parse_frames_slice("1:2:3:4")
        with pytest.raises(ValueError):
            pm.parse_frames_slice("a:b")


class TestLoadFrames:
    def test_loads_all_frames(self, traj_file):
        frames = pm.load_frames(traj_file)
        assert len(frames) == 5
        assert all(len(f) == 2 for f in frames)  # pymatgen Structures

    def test_slice_applied(self, traj_file):
        frames = pm.load_frames(traj_file, "1:5:2")
        assert len(frames) == 2

    def test_single_frame_file(self, tmp_path):
        atoms = bulk("Si", "diamond", a=5.43)
        path = tmp_path / "POSCAR"
        ase_write(path, atoms, format="vasp")
        frames = pm.load_frames(str(path))
        assert len(frames) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest src/matcreator/skills/vasp-pymatgen/scripts/tests/test_prepare_matpes.py -v -k "ParseFrames or LoadFrames"`
Expected: FAIL with `AttributeError: module 'prepare_matpes' has no attribute 'parse_frames_slice'`

- [ ] **Step 3: Write minimal implementation**

Append to `prepare_matpes.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest src/matcreator/skills/vasp-pymatgen/scripts/tests/test_prepare_matpes.py -v -k "ParseFrames or LoadFrames"`
Expected: 7 PASSED

- [ ] **Step 5: Commit**

```bash
git add src/matcreator/skills/vasp-pymatgen/scripts
git commit -m "feat(vasp-pymatgen): add trajectory frame loading with slice subsampling"
```

---

### Task 4: INCAR validation

**Files:**
- Modify: `src/matcreator/skills/vasp-pymatgen/scripts/prepare_matpes.py`
- Test: `src/matcreator/skills/vasp-pymatgen/scripts/tests/test_prepare_matpes.py`

**Interfaces:**
- Produces: `validate_incar(incar: dict, spin: bool) -> list[str]` — pure function; returns a list of error strings (empty = valid). Accepts any mapping (pymatgen `Incar` is a dict subclass).

- [ ] **Step 1: Write the failing tests**

Append to `test_prepare_matpes.py`:

```python
GOOD_INCAR = {
    "ISPIN": 1,
    "ENCUT": 600.0,
    "LCHARG": False,
    "LAECHG": False,
    "LMIXTAU": False,
    "ALGO": "Normal",
    "KSPACING": 0.22,
}


class TestValidateIncar:
    def test_good_incar_passes(self):
        assert pm.validate_incar(dict(GOOD_INCAR), spin=False) == []

    def test_wrong_ispin(self):
        incar = dict(GOOD_INCAR, ISPIN=2)
        errs = pm.validate_incar(incar, spin=False)
        assert any("ISPIN" in e for e in errs)

    def test_wrong_encut(self):
        incar = dict(GOOD_INCAR, ENCUT=680.0)
        errs = pm.validate_incar(incar, spin=False)
        assert any("ENCUT" in e for e in errs)

    def test_lorbit_present_fails(self):
        incar = dict(GOOD_INCAR, LORBIT=11)
        errs = pm.validate_incar(incar, spin=False)
        assert any("LORBIT" in e for e in errs)

    def test_magmom_present_without_spin_fails(self):
        incar = dict(GOOD_INCAR, MAGMOM=[0.6, 0.6])
        errs = pm.validate_incar(incar, spin=False)
        assert any("MAGMOM" in e for e in errs)

    def test_spin_requires_ispin2_and_magmom(self):
        incar = dict(GOOD_INCAR, ISPIN=2, MAGMOM=[0.6, 0.6])
        assert pm.validate_incar(incar, spin=True) == []
        errs = pm.validate_incar(dict(GOOD_INCAR), spin=True)
        assert any("ISPIN" in e for e in errs)
        assert any("MAGMOM" in e for e in errs)

    def test_lcharg_true_fails(self):
        incar = dict(GOOD_INCAR, LCHARG=True)
        errs = pm.validate_incar(incar, spin=False)
        assert any("LCHARG" in e for e in errs)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest src/matcreator/skills/vasp-pymatgen/scripts/tests/test_prepare_matpes.py -v -k ValidateIncar`
Expected: FAIL with `AttributeError: module 'prepare_matpes' has no attribute 'validate_incar'`

- [ ] **Step 3: Write minimal implementation**

Append to `prepare_matpes.py`:

```python
def validate_incar(incar: dict, spin: bool) -> list[str]:
    errors = []
    expected = {
        "ISPIN": 2 if spin else 1,
        "ENCUT": 600,
        "LCHARG": False,
        "LAECHG": False,
        "LMIXTAU": False,
    }
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest src/matcreator/skills/vasp-pymatgen/scripts/tests/test_prepare_matpes.py -v -k ValidateIncar`
Expected: 7 PASSED

- [ ] **Step 5: Commit**

```bash
git add src/matcreator/skills/vasp-pymatgen/scripts
git commit -m "feat(vasp-pymatgen): add INCAR override validation"
```

---

### Task 5: Directory validation (completeness + POTCAR order + KPOINTS check)

**Files:**
- Modify: `src/matcreator/skills/vasp-pymatgen/scripts/prepare_matpes.py`
- Test: `src/matcreator/skills/vasp-pymatgen/scripts/tests/test_prepare_matpes.py`

**Interfaces:**
- Consumes: `validate_incar` from Task 4.
- Produces: `validate_dir(calc_dir: Path, spin: bool) -> list[str]` — checks INCAR/POSCAR/POTCAR existence (returns early if any missing), INCAR overrides, no KPOINTS when KSPACING set, POTCAR element order matches POSCAR.

- [ ] **Step 1: Write the failing tests**

POTCAR parsing needs real POTCAR content, so file-existence tests run everywhere and the full-directory test is exercised in Task 6's end-to-end test. Append:

```python
from pathlib import Path


class TestValidateDir:
    def test_empty_dir_reports_all_missing(self, tmp_path):
        errs = pm.validate_dir(tmp_path, spin=False)
        assert len(errs) == 3
        assert any("INCAR" in e for e in errs)
        assert any("POSCAR" in e for e in errs)
        assert any("POTCAR" in e for e in errs)

    def test_missing_potcar_only(self, tmp_path):
        (tmp_path / "INCAR").write_text("ISPIN = 1\n")
        (tmp_path / "POSCAR").write_text("x\n")
        errs = pm.validate_dir(tmp_path, spin=False)
        assert errs == ["missing POTCAR"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest src/matcreator/skills/vasp-pymatgen/scripts/tests/test_prepare_matpes.py -v -k ValidateDir`
Expected: FAIL with `AttributeError: module 'prepare_matpes' has no attribute 'validate_dir'`

- [ ] **Step 3: Write minimal implementation**

Append to `prepare_matpes.py`:

```python
from pathlib import Path


def validate_dir(calc_dir: Path, spin: bool) -> list[str]:
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
    errors.extend(validate_incar(incar, spin))

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
```

Note: the `from pathlib import Path` import goes at the top of the module with the other imports, not inline.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest src/matcreator/skills/vasp-pymatgen/scripts/tests/test_prepare_matpes.py -v -k ValidateDir`
Expected: 2 PASSED

- [ ] **Step 5: Commit**

```bash
git add src/matcreator/skills/vasp-pymatgen/scripts
git commit -m "feat(vasp-pymatgen): add calc-dir validation (files, INCAR, POTCAR order)"
```

---

### Task 6: Input generation + end-to-end validation test

**Files:**
- Modify: `src/matcreator/skills/vasp-pymatgen/scripts/prepare_matpes.py`
- Test: `src/matcreator/skills/vasp-pymatgen/scripts/tests/test_prepare_matpes.py`

**Interfaces:**
- Consumes: `build_incar_overrides` (Task 1), `validate_dir` (Task 5).
- Produces: `generate_inputs(structure, outdir: Path, spin: bool) -> None` — writes INCAR/POSCAR/POTCAR into `outdir` via `MatPESStaticSet` (no KPOINTS: KSPACING is INCAR-driven).

- [ ] **Step 1: Write the failing tests**

Append to `test_prepare_matpes.py`:

```python
import os

needs_potcar = pytest.mark.skipif(
    not os.environ.get("PMG_VASP_PSP_DIR"),
    reason="PMG_VASP_PSP_DIR not set; POTCAR library unavailable",
)


@needs_potcar
class TestGenerateInputs:
    def test_generate_and_validate_no_spin(self, tmp_path):
        outdir = tmp_path / "job"
        pm.generate_inputs(make_si(), outdir, spin=False)
        assert (outdir / "INCAR").is_file()
        assert (outdir / "POSCAR").is_file()
        assert (outdir / "POTCAR").is_file()
        assert not (outdir / "KPOINTS").exists()
        assert pm.validate_dir(outdir, spin=False) == []

    def test_generate_and_validate_spin(self, tmp_path):
        outdir = tmp_path / "job_spin"
        pm.generate_inputs(make_si(), outdir, spin=True)
        assert pm.validate_dir(outdir, spin=True) == []

    def test_validator_catches_tampered_incar(self, tmp_path):
        outdir = tmp_path / "job_bad"
        pm.generate_inputs(make_si(), outdir, spin=False)
        incar_path = outdir / "INCAR"
        content = incar_path.read_text().replace("ENCUT = 600", "ENCUT = 520")
        incar_path.write_text(content)
        errs = pm.validate_dir(outdir, spin=False)
        assert any("ENCUT" in e for e in errs)
```

- [ ] **Step 2: Run tests to verify they fail (or skip)**

Run: `pytest src/matcreator/skills/vasp-pymatgen/scripts/tests/test_prepare_matpes.py -v -k GenerateInputs`
Expected: if `PMG_VASP_PSP_DIR` is set — FAIL with `AttributeError: ... no attribute 'generate_inputs'`; otherwise 3 SKIPPED (then implementation still proceeds; the code path is covered on machines with a POTCAR library).

- [ ] **Step 3: Write minimal implementation**

Append to `prepare_matpes.py`:

```python
def generate_inputs(structure, outdir: Path, spin: bool) -> None:
    from pymatgen.io.vasp.sets import MatPESStaticSet

    vis = MatPESStaticSet(
        structure, user_incar_settings=build_incar_overrides(spin)
    )
    vis.write_input(str(outdir))
```

- [ ] **Step 4: Run tests to verify they pass (or skip)**

Run: `pytest src/matcreator/skills/vasp-pymatgen/scripts/tests/test_prepare_matpes.py -v -k GenerateInputs`
Expected: 3 PASSED (with POTCAR library) or 3 SKIPPED (without)

- [ ] **Step 5: Commit**

```bash
git add src/matcreator/skills/vasp-pymatgen/scripts
git commit -m "feat(vasp-pymatgen): generate MatPES static inputs with MLFF overrides"
```

---

### Task 7: CLI (argparse, batch loop, summary, exit codes)

**Files:**
- Modify: `src/matcreator/skills/vasp-pymatgen/scripts/prepare_matpes.py`
- Test: `src/matcreator/skills/vasp-pymatgen/scripts/tests/test_prepare_matpes.py`

**Interfaces:**
- Consumes: `load_frames`, `check_structure`, `generate_inputs`, `validate_dir`.
- Produces:
  - `parse_args(argv: list[str] | None) -> argparse.Namespace` — attributes: `structure_file`, `output_dir`, `spin`, `frames`, `validate_only`.
  - `main(argv: list[str] | None = None) -> int` — returns exit code; `if __name__ == "__main__": sys.exit(main())`.
- Directory layout: 1 frame → `output_dir/` directly; >1 frame → `output_dir/frame_0000/` etc.

- [ ] **Step 1: Write the failing tests**

Monkeypatched tests cover routing/exit codes without a POTCAR library. Append:

```python
class TestCli:
    def test_requires_structure_file(self, capsys):
        with pytest.raises(SystemExit):
            pm.parse_args([])

    def test_validate_only_needs_no_structure(self):
        args = pm.parse_args(["--validate-only", "some_dir"])
        assert args.validate_only == ["some_dir"]

    def test_missing_psp_dir_errors(self, traj_file, monkeypatch, capsys):
        monkeypatch.delenv("PMG_VASP_PSP_DIR", raising=False)
        rc = pm.main([traj_file])
        assert rc == 1
        assert "PMG_VASP_PSP_DIR" in capsys.readouterr().err

    def test_multiframe_layout_and_summary(self, traj_file, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("PMG_VASP_PSP_DIR", "/fake")
        targets = []
        monkeypatch.setattr(pm, "generate_inputs", lambda s, d, spin: targets.append(Path(d)))
        monkeypatch.setattr(pm, "validate_dir", lambda d, spin: [])
        outdir = tmp_path / "out"
        rc = pm.main([traj_file, "-o", str(outdir)])
        assert rc == 0
        assert targets == [outdir / f"frame_{i:04d}" for i in range(5)]
        out = capsys.readouterr().out
        assert "5 generated, 0 skipped, 0 failed" in out

    def test_single_frame_no_subdir(self, tmp_path, monkeypatch):
        from ase.build import bulk
        from ase.io import write as ase_write
        path = tmp_path / "si.cif"
        ase_write(path, bulk("Si", "diamond", a=5.43))
        monkeypatch.setenv("PMG_VASP_PSP_DIR", "/fake")
        targets = []
        monkeypatch.setattr(pm, "generate_inputs", lambda s, d, spin: targets.append(Path(d)))
        monkeypatch.setattr(pm, "validate_dir", lambda d, spin: [])
        outdir = tmp_path / "single"
        rc = pm.main([str(path), "-o", str(outdir)])
        assert rc == 0
        assert targets == [outdir]

    def test_bad_frame_skipped_not_fatal(self, traj_file, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("PMG_VASP_PSP_DIR", "/fake")
        monkeypatch.setattr(pm, "generate_inputs", lambda s, d, spin: None)
        monkeypatch.setattr(pm, "validate_dir", lambda d, spin: [])
        warnings = iter(["atoms overlap", None, None, None, None])
        monkeypatch.setattr(pm, "check_structure", lambda s: next(warnings))
        rc = pm.main([traj_file, "-o", str(tmp_path / "out")])
        assert rc == 0
        out = capsys.readouterr().out
        assert "4 generated, 1 skipped, 0 failed" in out
        assert "[SKIP]" in out

    def test_validation_failure_exits_1(self, traj_file, tmp_path, monkeypatch):
        monkeypatch.setenv("PMG_VASP_PSP_DIR", "/fake")
        monkeypatch.setattr(pm, "generate_inputs", lambda s, d, spin: None)
        monkeypatch.setattr(pm, "validate_dir", lambda d, spin: ["bad INCAR"])
        rc = pm.main([traj_file, "-o", str(tmp_path / "out")])
        assert rc == 1

    def test_all_frames_skipped_exits_1(self, traj_file, tmp_path, monkeypatch):
        monkeypatch.setenv("PMG_VASP_PSP_DIR", "/fake")
        monkeypatch.setattr(pm, "check_structure", lambda s: "bad")
        rc = pm.main([traj_file, "-o", str(tmp_path / "out")])
        assert rc == 1

    def test_validate_only_mode(self, tmp_path, monkeypatch, capsys):
        d1, d2 = tmp_path / "a", tmp_path / "b"
        d1.mkdir(); d2.mkdir()
        results = {str(d1): [], str(d2): ["missing INCAR"]}
        monkeypatch.setattr(pm, "validate_dir", lambda d, spin: results[str(d)])
        rc = pm.main(["--validate-only", str(d1), str(d2)])
        assert rc == 1
        out = capsys.readouterr().out
        assert "[OK]" in out and "[FAIL]" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest src/matcreator/skills/vasp-pymatgen/scripts/tests/test_prepare_matpes.py -v -k TestCli`
Expected: FAIL with `AttributeError: module 'prepare_matpes' has no attribute 'parse_args'`

- [ ] **Step 3: Write minimal implementation**

Add to the top of `prepare_matpes.py`:

```python
import argparse
import os
import sys
```

Append:

```python
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

    if args.validate_only:
        n_failed = 0
        for d in args.validate_only:
            errs = validate_dir(Path(d), args.spin)
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
        generate_inputs(structure, target, args.spin)
        errs = validate_dir(target, args.spin)
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
```

- [ ] **Step 4: Run the full test suite**

Run: `pytest src/matcreator/skills/vasp-pymatgen/scripts/tests/test_prepare_matpes.py -v`
Expected: all PASSED (GenerateInputs tests SKIPPED without POTCAR library); no failures

- [ ] **Step 5: Smoke-test the CLI manually**

```bash
python src/matcreator/skills/vasp-pymatgen/scripts/prepare_matpes.py --help
```
Expected: usage text showing `structure_file`, `-o`, `--spin`, `--frames`, `--validate-only`

If `PMG_VASP_PSP_DIR` is set, also run a real generation:

```bash
python -c "
from ase.build import bulk
from ase.io import write
write('/tmp/si_test.cif', bulk('Si', 'diamond', a=5.43))
"
python src/matcreator/skills/vasp-pymatgen/scripts/prepare_matpes.py /tmp/si_test.cif -o /tmp/matpes_test
cat /tmp/matpes_test/INCAR
```
Expected: `Summary: 1 generated, 0 skipped, 0 failed`; INCAR shows ISPIN=1, ENCUT=600, LCHARG=False, LAECHG=False, LMIXTAU=False, no LORBIT, no MAGMOM.

- [ ] **Step 6: Commit**

```bash
git add src/matcreator/skills/vasp-pymatgen/scripts
git commit -m "feat(vasp-pymatgen): add prepare_matpes CLI with batch generation and validation"
```
