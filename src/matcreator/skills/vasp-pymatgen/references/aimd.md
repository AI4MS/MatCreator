# VASP AIMD

Use the requested structure, ensemble, temperature or temperature program, time
step in femtoseconds, and integration step count. For a fresh constant-temperature
NVT run, use the MPMDSet preparation entry point. It fixes the cell, uses a
Nosé–Hoover thermostat, and supplies an explicit random seed for reproducibility.

```python
from pathlib import Path
from matcreator.aidb.aimd import prepare_aimd_nvt

prepare_aimd_nvt(
    Path.cwd(), "H2O-cell.cif", "h2o-aimd-input",
    temperature_kelvin=300, timestep_fs=1, steps=20,
)
```

Use a new input directory for a change in conditions. Pass explicit INCAR
overrides for requested electronic settings. The preparation uses the configured
POTCAR library, a Γ-only mesh, a 520 eV cutoff and a seed of `248489752 0 0` unless
overridden through the preparation arguments. State the thermostat and initial
seed in the plan. If the user supplies initial velocities or restart inputs,
preserve the complete POSCAR and required dependencies in the prepared directory.
For another supported ensemble or temperature program, set its VASP inputs
explicitly and use the same managed submission and collection tools.

Submit the prepared directory with `submit_bohr_batchjob` or
`start_managed_vasp_sandbox`. MatCreator checks AIDB before starting the run.
If the tool returns `waiting_choice`, leave the files in place for the user's
decision. Input preparation alone ends after writing the input directory.

Collect the tracked result with `collect_remote_job_outputs`. The receiver
checks the full MD integration stage and electronic labels for the last computed
frame. The completion card links the available temperature curve and the result
row offers the verified final frame and curve in its export menu. Describe
missing files and archive failures using the completion receipt. The final
record stores one structure with its electronic labels and, when available, the
measured temperatures for each step. The temperature graph uses those measured
values in K and simulated time in fs.
