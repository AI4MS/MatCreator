# MatPES Static Calculation for MLFF Labeling

## Overview

`MatPESStaticSet` is a pymatgen VASP input set designed for generating energy and force labels for Machine Learning Force Field (MLFF) training datasets. Unlike standard SCF (`MPStaticSet`), it strips out everything unnecessary for labeling: no charge density, no DOS projections, no WAVECAR. Settings are locked to ensure uniformity across hundreds or thousands of frames — same `ENCUT`, same `ISPIN`, same k-point density.

The `scripts/prepare_matpes.py` script wraps `MatPESStaticSet` with structure sanity checks, INCAR override enforcement, multi-frame trajectory support, and automated post-generation validation. It produces ready-to-submit `INCAR`/`POSCAR`/`POTCAR` in one command.

## When to Use

**Use when:**
- Generating VASP static calculations for MLFF training datasets (energy + force labels)
- Batch-processing AIMD or MD trajectory frames from a multi-frame extxyz file
- Consistent `ENCUT`/`ISPIN`/`KSPACING` across many structures matters
- Validating existing calculation directories for dataset compliance (`--validate-only`)

**Don't use for:**
- Band structure → use `prepare_nscf_kpath`
- Density of states (DOS) → use `prepare_nscf_uniform`
- Geometry relaxation → use `prepare_relaxation`
- Standard SCF that needs `CHGCAR`/`WAVECAR` for follow-up NSCF → use `prepare_scf`

## Prerequisites

1. **`PMG_VASP_PSP_DIR`** — path to the VASP pseudopotential library (required for `POTCAR` generation):
   ```bash
   echo $PMG_VASP_PSP_DIR    # verify: should print a directory path
   ```
   > If not set, the script exits with an error. Export it or add to `~/.bashrc`.

2. **Python packages**: `pymatgen`, `ase`, `numpy` (all in the project's `requirements.txt`):
   ```bash
   python -c "from pymatgen.io.vasp.sets import MatPESStaticSet; print('OK')"
   python -c "from ase.io import read; print('OK')"
   ```

3. **Structure file** readable by ASE — extxyz, POSCAR, CIF, or any ASE-supported format.

## Usage

### Quick Reference

| Option | Description | Default |
|---|---|---|
| `structure_file` | Input structure (ASE-readable) | required |
| `-o, --output-dir` | Output directory | `matpes_job` |
| `--spin` | Enable spin polarization (`ISPIN=2`, keep MatPES `MAGMOM` guesses) | off (`ISPIN=1`) |
| `--frames START:STOP:STEP` | Frame slice for multi-frame files | all frames |
| `--validate-only DIR...` | Validate existing calc dirs, skip generation | — |

### Outputs

| Output | Type | Description |
|---|---|---|
| `INCAR` | text | VASP parameters (MatPESStaticSet defaults + overrides) |
| `POSCAR` | text | Structure in VASP format |
| `POTCAR` | binary | Pseudopotentials conjugated to POSCAR species |
| (no `KPOINTS`) | — | k-points controlled by `KSPACING` in INCAR |

After VASP run — read from `vasprun.xml`:
| Field | Type | Source |
|---|---|---|
| `final_energy` | float (eV) | `vr.final_energy` |
| `forces` | list[float[3]] (eV/Å) | `vr.ionic_steps[-1]["forces"]` |
| `stress` | float[3,3] or None (kBar) | `vr.ionic_steps[-1].get("stress")` |

### Single Structure

```bash
python scripts/prepare_matpes.py structure.extxyz -o job_si
```

This writes `INCAR`, `POSCAR`, `POTCAR` into `job_si/`. No `KPOINTS` file — k-points are controlled by `KSPACING` in `INCAR`.

### Multi-Frame Trajectory (AIMD / MD)

```bash
python scripts/prepare_matpes.py md_trajectory.extxyz -o matpes_jobs --frames 0:100:5
```

Creates one subdirectory per frame: `matpes_jobs/frame_0000/`, `frame_0005/`, `frame_0010/`, … through frame 95. Each contains independent VASP input files.

Single-frame files produce output directly in the output directory (no `frame_XXXX` subdir).

### Spin-Polarized

```bash
python scripts/prepare_matpes.py fe_bcc.extxyz --spin -o job_fe
```

Sets `ISPIN=2` and removes the `MAGMOM=None` override so MatPESStaticSet's built-in magnetic moment guesses pass through. Only use for genuinely magnetic systems.

### Validate-Only (No Generation)

```bash
python scripts/prepare_matpes.py --validate-only job_si job_fe
```

Checks existing calculation directories for INCAR correctness, file completeness, and POTCAR/POSCAR element consistency. Prints `[OK]` or `[FAIL]` per directory. Exit code 0 = all pass.

## Key INCAR Settings

The script overrides MatPESStaticSet defaults to produce a clean, consistent INCAR:

| Tag | Value | Reason |
|---|---|---|
| `ENCUT` | 600 | Uniform plane-wave cutoff across the dataset |
| `ISPIN` | 1 (2 with `--spin`) | Non-magnetic default; opt-in for magnetism |
| `NSW` | 0 | Static calculation — single energy + force evaluation |
| `LCHARG` | False | No charge density file |
| `LAECHG` | False | No atomic charge density |
| `LMIXTAU` | False | Not needed (no +U) |
| `LORBIT` | *(removed)* | No DOS projection needed for labeling |
| `KSPACING` | 0.22 | Automatic Γ-centered k-point mesh |
| `LWAVE` | False | No WAVECAR (MatPESStaticSet default) |

## Reading Results

After the VASP job completes, extract energy and forces from `vasprun.xml`:

```python
from pymatgen.io.vasp.outputs import Vasprun

vr = Vasprun("job_dir/vasprun.xml")
energy = vr.final_energy               # total energy (eV)
forces = vr.ionic_steps[-1]["forces"]  # forces on each atom (eV/Å)
stress = vr.ionic_steps[-1].get("stress")  # stress tensor (kBar), may be None
```

For multi-frame trajectories, iterate over all `frame_XXXX/` directories.

## Submission

Submit via Bohrium exactly like any other VASP job — `prepare_matpes.py` only generates input files. See `references/bohr.md` for the full VASP submission template (image, command, machine types).

No special submission requirements: labeling jobs are standard VASP static runs (`NSW=0`).

## Common Misuse Patterns

| Wrong | Why it fails | Right |
|---|---|---|
| `prepare_matpes.py traj.extxyz` (no `--frames`) | Loads all 10,000 frames → 10,000 dirs | `--frames 0:10000:50` |
| `prepare_matpes.py diamond.extxyz --spin` | Non-magnetic system gets spurious MAGMOM | Omit `--spin`, use ISPIN=1 default |
| `from_prev_calc("label_dir/")` for NSCF | Label dirs have no CHGCAR/WAVECAR | Use `prepare_scf` output instead |

## Common Pitfalls

1. **`PMG_VASP_PSP_DIR` not set** — script exits with error message. Export the path to your POTCAR library directory before running.
2. **Overlapping atoms or unreasonable cell** — `check_structure` flags interatomic distances < 0.5 Å and volume/atom outside [1, 1000] Å³. The problematic frame is skipped. Fix the structure geometry and re-run.
3. **Accidentally using `--spin` on non-magnetic systems** — MatPESStaticSet will guess `MAGMOM` values. Only use `--spin` for systems with genuine magnetic moments.
4. **Expecting `CHGCAR` or `WAVECAR` output** — this is a labeling run. Neither file is written. If you need charge density for follow-up NSCF, use `prepare_scf` with `MPStaticSet` instead.
5. **Pointing `from_prev_calc()` at a labeling directory** — label directories contain no `CHGCAR`/`WAVECAR`. NSCF jobs that require these files must point at an `MPStaticSet` SCF directory instead.
6. **Forgetting `--frames` on a multi-frame extxyz** — without `--frames`, the script loads all frames. For a 10,000-frame trajectory this will create 10,000 directories. Always use `--frames` to slice large trajectories.

## Verification Checklist

After generation, confirm:
- [ ] `INCAR` is present and `grep ENCUT <dir>/INCAR` shows `600`
- [ ] `INCAR` contains no `LORBIT` tag; `LCHARG`, `LAECHG`, `LMIXTAU` are all `False`
- [ ] `ISPIN` is `1` (or `2` if `--spin` was used)
- [ ] `POSCAR` is a valid structure readable by ASE
- [ ] `POTCAR` elements match the species in `POSCAR`
- [ ] No `KPOINTS` file exists (k-points via `KSPACING` in `INCAR`)
- [ ] For multi-frame: `frame_XXXX` directories match the expected slice count
- [ ] Run `python scripts/prepare_matpes.py --validate-only <dir>` for automated check
