---
name: vasp-pymatgen
version: 1.0.0
description: >
  Generate VASP inputs (INCAR/POSCAR/POTCAR/KPOINTS) via pymatgen.io.vasp.sets
  for DFT calculations: MPRelaxSet (geometry relaxation), MPStaticSet (SCF),
  MPNonSCFSet (band structure), MatPESStaticSet (MLFF energy/force labeling).
  INCAR is driven by pymatgen defaults; the agent only supplies user_incar_settings
  overrides. Use when the user asks to prepare VASP calculations, run DFT, or
  generate MLFF training data from structures. Do NOT use for VASP result
  post-processing / analysis, non-VASP DFT codes, or molecular dynamics — this
  skill only generates input files and submits jobs.
metadata:
  tools:
    - run_python
    - run_bash
    - load_skill_resource
  dependent_skills:
    - bohrium
  tags:
    - vasp
    - dft
    - relaxation
    - scf
    - band-structure
    - pymatgen
    - label
    - mlff
---

# VASP DFT Skill (pymatgen sets)

This skill generates VASP input files for common DFT calculation types:
relaxation, SCF, band structure, and MLFF energy/force labeling.
Workflow: obtain a structure → prepare inputs → submit to Bohrium → read results.

All input generation uses `pymatgen.io.vasp.sets` (`MPRelaxSet`, `MPStaticSet`,
`MPNonSCFSet`, `MatPESStaticSet`). These classes own the INCAR defaults — the
agent only passes `user_incar_settings` to override individual keys. Never write
a full INCAR dict from scratch.

> Load the reference file for the specific command you are about to run (see per-command pointers below).

## Prerequisites

1. **`PMG_VASP_PSP_DIR`** — path to the VASP pseudopotential library (required for `POTCAR` generation):
   ```bash
   echo $PMG_VASP_PSP_DIR    # verify: should print a directory path
   ```

2. **Python packages**: `pymatgen`, `ase`, `numpy` :
   ```bash
   python -c "from pymatgen.io.vasp.sets import MatPESStaticSet; print('OK')"
   python -c "from ase.io import read; print('OK')"
   ```

3. **Structure file** readable by ASE — extxyz, POSCAR, CIF, or any ASE-supported format.

- The `bohrium` skill loaded (for job submission).

> **Stop and tell the user** if `PMG_VASP_PSP_DIR` is not set or dependencies are missing.

### `user_incar_settings`

All commands accept a Python dict of INCAR overrides. pymatgen merges these with
its own defaults — the agent never writes a raw INCAR.

**Format:** `{ "TAG": value, ... }` — keys are VASP INCAR tag names (case-sensitive).

**Constraints:**
- Use Python types: `bool` for logical flags (`LCHARG`, `LWAVE`), `int` for
  integers (`NSW`, `NBANDS`), `float` for reals (`ENCUT`, `SIGMA`).
- List types for per-atom tags: `MAGMOM` expects `[float, ...]`, one per atom.
- `None` removes the tag from INCAR entirely (pymatgen-specific).


**Common overrides:**

| Tag | Type | Purpose | Example |
|---|---|---|---|
| `NCORE` | `int` | Band-level parallelism | `4` |
| `ENCUT` | `float` | Plane-wave cutoff (eV) | `600` |
| `ISPIN` | `int` | Spin: 1=off, 2=on | `2` |
| `LSORBIT` | `bool` | Spin-orbit coupling | `True` |
| `MAGMOM` | `list[float]` | Initial magnetic moments | `[5.0, 0.6]` |
| `NEDOS` | `int` | DOS grid points | `2000` |

**Notes:**
- `NCORE` controls band-level parallelism in VASP. Set it so that `total_CPU_cores / NCORE`
  is an integer (even workload distribution). A good starting point is `NCORE ≈ √(cores)`:
  | CPU cores | NCORE | cores / NCORE |
  |---|---|---|
  | 8 | 4 | 2 |
  | 16 | 4 | 4 |
  | 32 | 8 | 4 |
  | 64 | 8 | 8 |
- Add `NCORE` to `user_incar_settings` for each calculation type.
---

## Mandatory workflow sequence

1. **Obtain a structure** — generate or load from file.
2. **Prepare inputs** — run the appropriate snippet via `run_python`.
3. **Submit jobs** — pass `calc_dir_list` to the `bohrium` skill.
4. **Read results** — after the job finishes, run `read_results` or `collect_results`.

Run exactly **one property step at a time**. Do not chain relaxation + SCF in a single step.

For **MLFF energy/force labeling**, use `prepare_label` as a standalone step — it replaces the relaxation → SCF chain with a single static calculation optimized for dataset generation (no charge density, no relaxation, KSPACING-based k-points).

---

## Commands

### prepare_relaxation
Structural relaxation with `MPRelaxSet`. Key params: `STRUCTURE_FILE`, `FRAMES`, `USER_INCAR`.
```
load_skill_resource(skill_name="vasp-pymatgen", path="references/relaxation.md")
```

### prepare_scf
Static SCF with `MPStaticSet`. Prefer `from_prev_calc(relax_dir)` when a relaxation dir is available; falls back to direct structure input. Add SOC keys to `USER_INCAR` when needed. Always outputs `CHGCAR`.
```
load_skill_resource(skill_name="vasp-pymatgen", path="references/scf.md")
```

### prepare_label
MLFF dataset static calculation with `MatPESStaticSet`. Optimised for energy/force labeling: no charge density output, KSPACING-based k-points, ENCUT=600, magnetism off by default. Use `--spin` for magnetic systems, `--frames` for multi-frame trajectories. Generates and validates INCAR/POSCAR/POTCAR in one step.
```
load_skill_resource(skill_name="vasp-pymatgen", path="references/label.md")
```

### prepare_nscf_kpath
Band-structure NSCF with `MPNonSCFSet(mode="line")`. Uses `from_prev_calc(scf_dir)` — auto-copies `CHGCAR` and sets ICHARG=11. Key params: `SCF_DIRS`, `SOC`, `USER_INCAR`.
```
load_skill_resource(skill_name="vasp-pymatgen", path="references/nscf-kpath.md")
```

### read_results
Parse `vasprun.xml` via `Vasprun`. Returns energy, forces, band gap, efermi, and (for nscf) band structure summary, etc.
```
load_skill_resource(skill_name="vasp-pymatgen", path="references/read-results.md")
```

---

## Safety

**Job submission confirmation:** Before submitting, state the number of jobs, machine type, and estimated core-hours. Never batch-submit more than 50 jobs without explicit user approval.

**Input validation:** Reject structure files with unreasonable atom counts (< 1 or > 1000 atoms) or paths that traverse outside the working directory.

---

## Submission

### `bohrium` skill (Recommended for Bohrium users)
Submit jobs to Bohrium using the `bohrium` skill, which wraps the `bohr` CLI. This is the recommended submission method for users running on the Bohrium platform.

The INCAR/POSCAR/POTCAR generation is platform-agnostic. The submission layer is pluggable — replace `bohrium` with a Slurm or local queue system as needed.

For the full submission template and environment variables for VASP job on bohrium, see:

```
load_skill_resource(skill_name="vasp-pymatgen", path="references/bohr.md")
```

