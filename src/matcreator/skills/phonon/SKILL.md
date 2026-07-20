---
name: phonon
description: Run local MLFF phonon calculations from a POSCAR and a DeePMD/DPA model, including phonon bands, DOS, thermal free energy, entropy, heat capacity, and validated outputs.
metadata:
  tools:
    - run_skill_script
  dependent_skills:
    - ase
    - dpa4
  tags:
    - phonon
    - phonopy
    - deepmd
    - dpa
    - mlff
    - imaginary-frequency
---

# Phonon MLFF Skill

Use this skill when the user asks to compute a phonon dispersion with a local
DeePMD/DPA model, inspect imaginary frequencies, or generate reusable phonon
artifacts from a POSCAR-like VASP structure. It also handles standard phonon
postprocessing: total DOS, zero-point energy, Helmholtz free energy, entropy,
and constant-volume heat capacity.

First version scope:
- local MLFF phonons only;
- input is an arbitrary POSCAR/VASP structure path;
- output is a dedicated run directory;
- no VASP, DFT reference, Bohrium, or DPDispatcher submission.

## Mandatory Tool

Use only the bundled script:

```text
run_skill_script(skill_name="phonon", script_name="phonon_tools.py", args="...")
```

Do not write ad hoc phonopy/ASE conversion code. The script contains the safe
PhonopyAtoms-to-ASE conversion and output validation logic.

Do not use `run_bash` to pre-create, delete, or repair the run directory. Pass a
fresh `--outdir` to `run-mlff`; the script creates it. If a previous failed
attempt left partial files, choose a new outdir or pass `--overwrite` only when
the user explicitly wants to replace that directory.

## Standard Workflow

1. Check the runtime environment:

```text
check-env
```

If the current MatCreator Python cannot import `deepmd`, check the user's DPA
environment through an explicit interpreter path instead of writing ad hoc bash:

```text
check-env --python /home/moli/miniconda3/envs/dpa4/bin/python
```

2. Run an end-to-end MLFF phonon calculation:

```text
run-mlff --structure POSCAR --model MODEL --outdir RUN_DIR --dim 2 2 2 --distance 0.01 --mesh 30 30 30
```

When DeePMD/DPA is installed in a separate conda environment, pass that
environment's Python executable:

```text
run-mlff --python /home/moli/miniconda3/envs/dpa4/bin/python --structure POSCAR --model MODEL --outdir RUN_DIR --dim 2 2 2 --distance 0.01 --mesh 30 30 30
```

By default, `run-mlff` also computes total DOS and thermal properties from 0 to
1000 K in 10 K steps. The agent should not run separate phonopy commands for
free energy, entropy, heat capacity, or DOS. If the user needs another
temperature grid, pass `--t-min`, `--t-max`, and `--t-step`.

Use `--device auto` for routine runs, `--device gpu` when the user explicitly
wants GPU execution, and `--device cpu` for CPU-only runs. Do not add custom
environment setup commands; the script handles runtime setup internally.

3. Validate the run directory:

```text
validate --run-dir RUN_DIR
```

Use a fresh run directory for each attempt. If a directory already contains
files, choose a new directory unless the user explicitly asks to overwrite.

Use the phonopy-generated plots from `run-mlff` as the canonical images:
`phonon_band.png`, `phonon_band_dos.png`, `phonon_dos.png`, and
`thermal_properties.png`. Do not generate a second custom band plot from
`band.yaml`; naive branch-index line plotting can create misleading continuity
across path segments. The bundled script intentionally writes PNG plots only.

## Available Commands

### `check-env`

Checks required lightweight packages and optional DeePMD support. Missing
`deepmd` means `run-mlff` cannot calculate forces, but displacement generation
and validation can still be used.

Use `--python /absolute/path/to/python` to check an external conda environment.

### `generate-displacements`

```text
generate-displacements --structure POSCAR --outdir RUN_DIR --dim 2 2 2 --distance 0.01
```

Writes `phonopy_disp.yaml`, `SPOSCAR`, `POSCAR-0001...`, and
`displacements_info.json`.

### `run-mlff`

```text
run-mlff --structure POSCAR --model MODEL --outdir RUN_DIR [--name NAME] [--head HEAD] [--python PYTHON] [--device auto|cpu|gpu] [--t-min 0 --t-max 1000 --t-step 10]
```

Writes `forces.npy`, optional `energies.npy`, `FORCE_CONSTANTS`,
`phonopy_params.yaml`, `band.yaml`, `total_dos.dat`,
`thermal_properties.yaml/csv/json`, plots, and `summary.json`.

Thermal outputs:
- `zero_point_energy_kJ_mol` in `summary.json`;
- `thermal_properties.yaml` from phonopy;
- `thermal_properties.csv/json` with columns/arrays for `temperature_K`,
  `free_energy_kJ_mol`, `entropy_J_K_mol`, and `heat_capacity_J_K_mol`;
- `thermal_properties.png` plus individual PNG
  `thermal_free_energy`, `thermal_entropy`, and `thermal_heat_capacity` plots.

Thermal free energy, entropy, heat capacity, and DOS are computed from the
q-mesh, not from the high-symmetry band path. If significant imaginary modes
exist, report the warning from `summary.json`: these thermal quantities are not
physically reliable unless the user intentionally applies
`--thermal-pretend-real` or `--thermal-cutoff-frequency`.

Use `--python` when the current MatCreator environment lacks `deepmd` but a
dedicated environment such as `/home/moli/miniconda3/envs/dpa4/bin/python` has
`deepmd`, `phonopy`, `ase`, and `seekpath`.

Do not rerun the script through `run_bash` to patch environment variables. Use
`--python` with the target environment and choose `--device auto`,
`--device gpu`, or `--device cpu`.

### `validate`

```text
validate --run-dir RUN_DIR
```

Checks that the run directory has a complete and internally consistent result.
Validation distinguishes small numerical acoustic negatives from significant
imaginary modes. Report both `min_freq_thz` and
`has_significant_imaginary` when discussing stability. Also report
`zero_point_energy_kJ_mol` and the thermal property temperature range when the
user asks for thermodynamic quantities.

## Critical Ordering Rule

The script must preserve phonopy supercell atom order. Never create an ASE
supercell with `ase.build.make_supercell()` and then overwrite its positions
with phonopy positions. See `references/ordering.md`.
