# SCF Input Generation

Generate VASP static calculation input files using `pymatgen` `MPStaticSet`.

## Steps

**1. Create a job directory**
```bash
mkdir -p scf_job
```

**2. Load the structure and generate inputs**
```python
from pymatgen.core import Structure
from pymatgen.io.ase import AseAtomsAdaptor
from pymatgen.io.vasp.sets import MPStaticSet
from ase.io import read as ase_read

# Or from any structure file (cif, extxyz, vasp, ...):
atoms = ase_read("structure.extxyz")
structure = AseAtomsAdaptor.get_structure(atoms)

# Custom 
user_incar = {
    "NCORE": 4,   # ≈ sqrt(number of cores) for optimal performance
}
vis = MPStaticSet(structure, user_incar_settings=user_incar)
vis.write_input("scf_job/")
```
If read from a previous relaxation, simply:

```python
vis = MPStaticSet.from_prev_calc("relax_job/", user_incar_settings=user_incar)
vis.write_input("scf_job/")
```

This writes `INCAR`, `POSCAR`, `KPOINTS`, and `POTCAR` into the job directory.

## Key MPStaticSet defaults

| Tag | Value | Notes |
|-----|-------|-------|
| NSW | 0 | No ionic relaxation |
| IBRION | -1 | No ion movement |
| LCHARG | True | Write CHGCAR |
| LORBIT | 11 | DOS with orbital projections |
| LASPH | True | Non-spherical PAW corrections |
| k-points | `reciprocal_density=100` | Γ-centered MP mesh |

## Notes

- Run the prepared directory through the tracked VASP submission entry point. Include `CHGCAR` in `out_files` when the user wants charge density or the next step uses that file.
- If the current step uses restart inputs, place its actual upstream files in the prepared directory and set the corresponding `ISTART` and `ICHARG`. Set `LWAVE=True` and include `WAVECAR` in `out_files` when it is needed after this step.
- On AIDB reuse, use the returned `artifact_paths` to prepare a downstream step. The same paths are offered as downloads in chat.

