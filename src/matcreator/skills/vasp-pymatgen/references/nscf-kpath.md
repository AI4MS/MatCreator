# NSCF Band Structure Input Generation

Generate VASP non-self-consistent (band structure) input files using `MPNonSCFSet`.
Run SCF and NSCF as separate dependent steps through the tracked VASP tools. For
SCF, retain `CHGCAR` and any actually needed `WAVECAR` in `out_files`. Use the
new SCF completion or the selected SCF reuse delivery to obtain the exact AIDB
record ID and local artifact paths. Stop if the upstream stage cannot deliver
the files. Use the user's requested k path when supplied.

## Steps

**1. Create a job directory**
```bash
mkdir -p nscf_job
```

**2. Generate inputs from previous SCF**
```python
from pymatgen.io.vasp.sets import MPNonSCFSet

user_incar = {
    "NCORE": 4,   # ≈ sqrt(number of cores)
    # "NBANDS": 64,
}

vis = MPNonSCFSet.from_prev_calc("scf_job/", mode="line", user_incar_settings=user_incar)
vis.write_input("nscf_job/")   # copies CHGCAR automatically
```

For SOC calculations, also copy `WAVECAR` manually:
```python
import shutil
shutil.copy2("scf_job/WAVECAR", "nscf_job/WAVECAR")
```

## Key MPNonSCFSet defaults

| Tag | Value | Notes |
|-----|-------|-------|
| ICHARG | 11 | Read charge density from CHGCAR |
| LCHARG | False | Do not write new CHGCAR |
| LWAVE | False | Do not write WAVECAR |
| LORBIT | 11 | DOS with orbital projections |
| k-path | `HighSymmKpath` | 64 points per segment |

## Notes

- Stage the delivered `CHGCAR` inside the NSCF input directory. For restart settings
  that read `WAVECAR`, stage the upstream `WAVECAR` too.
- Submit using `submit_bohr_batchjob` or `start_managed_vasp_sandbox` with
  `upstream_record_id` set to the exact SCF AIDB record ID. Use reciprocal coordinates
  in `KPOINTS`; explicit paths and VASP line-mode paths are supported.
- For a new NSCF run, collect outputs through `collect_remote_job_outputs`. Its
  successful verification delivers `bands.json` and `bands.svg`; a selected NSCF
  result delivers them via the reuse receipt. Give the user both files and the
  two stages' record IDs. If NSCF has not delivered the files, keep the chain open.
