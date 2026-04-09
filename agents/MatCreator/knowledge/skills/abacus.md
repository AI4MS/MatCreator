---
name: abacus
description: Skill for executing ABACUS DFT materials calculations. Help users set up, run, and analyze ABACUS calculations.
tags: [DFT, ABACUS]
tools: [build_bulk_crystal,build_supercell,perturb_atoms,inspect_structure,filter_by_entropy]
dependent_skills: []
---
Operate ABACUS safely with minimal steps and strict validation.

Must‑follow sequence
- `abacus_prepare` first to create an inputs directory (INPUT, STRU, pseudopotentials, orbitals).
- `check_abacus_input` to validate inputs BEFORE any calculation submission.
- Then run exactly ONE calculation tool per step.
- `collect_abacus_*_results` AFTER the corresponding calculation completes.

Rules
- Never pass raw structure files to property tools; always use the prepared inputs directory.
- Confirm critical parameters with the user; prefer plane‑wave basis unless the user requests otherwise.
- If inputs are missing or invalid, stop and request the minimal fix.
- Never invent tools; only call from the allowlist.

## ⚠️ IMPORTANT: Add Descriptive Job Names for Bohrium

When submitting ABACUS jobs to Bohrium via dpdisp, **always add a descriptive `job_name`** in the `input_data` section of the submission JSON. This makes jobs easy to identify on the Bohrium platform.

**Job name format:** `<system>_<calc_type>_<key_params>`

Examples:
- `Si_bulk_relax_PBE` — Si bulk, relaxation, PBE functional
- `LiCoO2_surface_scf_HSE06` — LiCoO2 surface, SCF, HSE06 functional
- `Fe2O3_defect_band_structure` — Fe2O3 with defect, band structure
- `Cu_bulk_DOS_kpath` — Cu bulk, DOS along k-path

If the user doesn't specify a job name, **construct one automatically** based on:
- Material/system name
- Calculation type (relax, scf, nscf, band, dos, etc.)
- Key parameters (functional, k-mesh, etc.)

Outputs
- Report absolute paths and essential metrics (e.g., final energy). Keep summaries tight and actionable.