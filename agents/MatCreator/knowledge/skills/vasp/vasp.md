---
name: vasp
description: Skills for VASP DFT calculations — input preparation, remote submission to Bohrium, and result collection.
tags: [vasp, dft, relaxation, scf, band-structure, dos, bohrium]
tools: [run_bash,run_python_file]
dependent_skills: [dpdisp]
---

# VASP DFT Skill


One script handles VASP-specific work; job submission is now delegated to the `dpdisp-submit` skill (DPDispatcher), which supports both Bohrium and standard Slurm/HPC clusters:

| Script | Role |
|---|---|
| `vasp_tools.py` | Prepare input files; collect / read results |
| `skills/dpdisp/` | Submit prepared directories as jobs via DPDispatcher (see `dpdisp-submit` skill) |

`vasp_tools.py` lives alongside `config.yaml` and `.env`.
Every command prints JSON to stdout and exits 0 on success, 1 on error.

> **Note:** Submission is now handled by the `dpdisp-submit` skill, which supports both Bohrium and standard Slurm clusters. The previous `bohr` skill and `bohrium_submit.py` are deprecated for new workflows.

---


## Mandatory workflow sequence

1. **Obtain a structure** — if the user has not supplied one, generate it first.
2. **Prepare inputs** — run the appropriate `vasp_tools.py prepare_*` command.
3. **Submit jobs** — pass the returned `calc_dir_list` to the `dpdisp-submit` skill by generating a `submission.json` (see [Submission — dpdisp-submit skill](#submission-dpdisp-skill) below).
4. **Collect / read results** — after the job finishes, run `collect_results` or `read_results`.

Run exactly **one property step at a time**. Do not chain relaxation + SCF in a single step.

---

## vasp_tools.py — Command reference

### Common flags (all prepare_* commands)

| Flag | Default | Description |
|---|---|---|
| `--config PATH` | `config.yaml` next to the script | Path to config.yaml |
| `--incar_tags JSON` | `{}` | Extra INCAR overrides, e.g. `'{"ENCUT": 600}'` |
| `--potcar_map JSON` | `{}` | Element → POTCAR label, e.g. `'{"Bi": "Bi_d"}'` |

All `prepare_*` commands return:
```json
{ "status": "success", "calc_dir_list": ["<abs_path>", ...] }
```

---

### prepare_relaxation

Prepare structural relaxation (IBRION=2, ISIF=3, NSW=200).

```bash
python vasp_tools.py prepare_relaxation \
    --structure <path.extxyz|path.vasp> \
    [--frames 0 1 2] \
    [--kpoints NX NY NZ] \
    [--incar_tags '{"ENCUT": 600}'] \
    [--potcar_map '{"Bi": "Bi_d"}']
```

- `--structure`: structure file to read. Supported formats:
  - `*.extxyz` — may contain multiple frames; all frames are processed by default.
  - `*.vasp` (POSCAR format) — always contains exactly one frame.
- `--frames`: integer indices to process (extxyz only); default is all frames.
- `--kpoints`: explicit Gamma-centred mesh. Default: auto KPPRA density 40.

---

### prepare_scf

Prepare a self-consistent field calculation (NSW=0, IBRION=-1).

```bash
python vasp_tools.py prepare_scf \
    --structure <path.extxyz|path.vasp> \
    [--frames 0 1 2] \
    [--kpoints NX NY NZ] \
    [--soc] \
    [--incar_tags '{"ENCUT": 600}']
```

- `--structure`: structure file to read. Supported formats:
  - `*.extxyz` — may contain multiple frames; all frames are processed by default.
  - `*.vasp` (POSCAR format) — always contains exactly one frame.
- `--frames`: integer indices to process (extxyz only); default is all frames.
- `--soc`: use `scf_soc` INCAR preset (ISPIN=2, LSORBIT=.TRUE.); default: `scf_nsoc`.
- Default k-mesh: auto KPPRA density 40.
- SCF outputs CHGCAR and WAVECAR when `LCHARG=true` / `LWAVE=true` (SOC preset only by default).

---

### prepare_nscf_kpath

Prepare a non-self-consistent band-structure calculation along a k-path.
Requires completed SCF directories that contain `CONTCAR` and `CHGCAR`.

```bash
python vasp_tools.py prepare_nscf_kpath \
    --scf_dirs <scf_dir1> [<scf_dir2> ...] \
    [--kpath GMKG] \
    [--n_kpoints 16] \
    [--soc] \
    [--incar_tags '{"NBANDS": 48}']
```

- `--kpath`: explicit path string (e.g. `GMKG`). Default: auto from pymatgen `HighSymmKpath`.
- `--n_kpoints`: points per segment (default 16).
- Copies `CHGCAR` (and `WAVECAR` if present) from the SCF directory.

---

### prepare_nscf_uniform

Prepare a non-self-consistent uniform-mesh calculation (for DOS).
Requires completed SCF directories.

```bash
python vasp_tools.py prepare_nscf_uniform \
    --scf_dirs <scf_dir1> [<scf_dir2> ...] \
    [--kpoints NX NY NZ] \
    [--soc] \
    [--incar_tags '{"NEDOS": 2000}']
```

- Default k-mesh: auto KPPRA density 100.
- Copies `CHGCAR` (and `WAVECAR` if present) from the SCF directory.

---

### collect_results

Parse `OUTCAR` files and write all frames into a single extxyz (via dpdata).

```bash
python vasp_tools.py collect_results \
    --dirs <calc_dir1> [<calc_dir2> ...]
```

Returns:
```json
{ "status": "success", "scf_result": "<abs_path_to_scf_result.extxyz>" }
```

---

### read_results

Read `vasprun.xml` / `OUTCAR` and return key scalar results as JSON.

```bash
python vasp_tools.py read_results \
    --calc_type <relaxation|scf|nscf> \
    --calc_dir  <calc_dir>
```

| calc_type | Returned fields |
|---|---|
| `relaxation` | `structure`, `total_energy`, `max_force`, `stress`, `ionic_steps` |
| `scf` | `structure`, `total_energy`, `efermi`, `band_gap`, `is_metal` |
| `nscf` | `structure`, `efermi`, `is_metal`, `band_gap`, `cbm`, `vbm` |

---


## Submission — `dpdisp-submit` skill {#submission-dpdisp-skill}

Submission is handled by the `dpdisp-submit` skill (DPDispatcher), which supports both Bohrium and standard Slurm/HPC clusters. See the `dpdisp-submit` skill documentation for full details and schema.

### Required environment variables

Set in your environment or via `.env` as needed for your backend (e.g., Bohrium credentials, Slurm SSH info, etc). For Bohrium, typical variables include:

| Variable | Description |
|---|---|
| `BOHRIUM_EMAIL` | Bohrium account e-mail |
| `BOHRIUM_PASSWORD` | Bohrium account password |
| `BOHRIUM_PROJECT_ID` | Bohrium project ID (integer) |
| `BOHRIUM_VASP_MACHINE` | Machine/scass type, e.g. `c32_m128_cpu` |
| `BOHRIUM_VASP_IMAGE` | Container image URI for VASP |

For Slurm or other clusters, set the appropriate SSH and resource variables (see `dpdisp-submit` docs).

### Example submission.json for VASP (Bohrium)

Bohrium uses `remote_profile` with an `input_data` sub-object. The `scass_type`, `image_name`, `platform`, and `job_type` fields go inside `input_data`.

```json
{
    "work_base": ".",
    "machine": {
        "batch_type": "Bohrium",
        "context_type": "BohriumContext",
        "local_root": ".",
        "remote_profile": {
            "email": "${BOHRIUM_EMAIL}",
            "password": "${BOHRIUM_PASSWORD}",
            "program_id": ${BOHRIUM_PROJECT_ID},
            "input_data": {
                "job_type": "container",
                "log_file": "log",
                "scass_type": "${BOHRIUM_VASP_MACHINE}",
                "platform": "ali",
                "image_name": "${BOHRIUM_VASP_IMAGE}"
            }
        }
    },
    "resources": {
        "group_size": 4
    },
    "task_list": [
        {
            "command": "source /opt/intel/oneapi/setvars.sh && mpirun -n 32 vasp_std",
            "task_work_path": "<calc_dir>",
            "forward_files": ["POSCAR", "INCAR", "POTCAR", "KPOINTS"],
            "backward_files": ["OSZICAR", "CONTCAR", "OUTCAR", "vasprun.xml", "log", "err"]
        }
    ]
}
```

For Slurm, set `batch_type` to `Slurm`, `context_type` to `SSHContext`, and fill in the SSH and resource fields as needed.

### Submission flow

1. Generate `submission.template.json` as above, using `${VARNAME}` for any environment variables.
2. Substitute variables:
     ```bash
     envsubst '${BOHRIUM_EMAIL} ${BOHRIUM_PASSWORD} ${BOHRIUM_PROJECT_ID} ${BOHRIUM_VASP_MACHINE} ${BOHRIUM_VASP_IMAGE}' < submission.template.json > submission.json
     ```
3. Validate and submit:
     ```bash
     uv run -m json.tool submission.json >/dev/null
     uvx --with dpdispatcher dargs check -f dpdispatcher.entrypoints.submit.submission_args submission.json
     uvx --from dpdispatcher --with oss2 dpdisp submit submission.json
     ```

> **Note:** Always use `--with oss2` for Bohrium jobs. `oss2` (Aliyun OSS SDK) is required by `BohriumContext` but is not bundled with dpdispatcher in uvx isolated environments. Omitting it causes `NameError: name 'oss2' is not defined`.

Append or adjust fields for SCF/NSCF as needed (e.g., add `CHGCAR`, `WAVECAR` to `forward_files`/`backward_files`).

---


## End-to-end example: relaxation → SCF → band structure

```bash
# 1. Prepare relaxation
RELAX=$(python vasp_tools.py prepare_relaxation --structure Al.extxyz)
RELAX_DIRS=$(echo $RELAX | python -c "import sys,json; print(' '.join(json.load(sys.stdin)['calc_dir_list'])))

# 2. Generate submission.template.json for relaxation (see above for schema)
#    (Repeat for each calc_dir as a task in task_list)

# 3. Substitute environment variables
envsubst '${BOHRIUM_USERNAME} ${BOHRIUM_PASSWORD} ${BOHRIUM_PROJECT_ID} ${BOHRIUM_VASP_MACHINE} ${BOHRIUM_VASP_IMAGE}' < submission.template.json > submission.json

# 4. Validate and submit
uv run -m json.tool submission.json >/dev/null
uvx --with dpdispatcher dargs check -f dpdispatcher.entrypoints.submit.submission_args submission.json
uvx --from dpdispatcher --with oss2 dpdisp submit submission.json

# 5. Read relaxation results
python vasp_tools.py read_results --calc_type relaxation --calc_dir <relax_dir>

# 6. Prepare SCF from relaxed structure (CONTCAR → extxyz conversion needed, or pass CONTCAR directly)
SCF=$(python vasp_tools.py prepare_scf --structure Al_relaxed.extxyz)
SCF_DIRS=$(echo $SCF | python -c "import sys,json; print(' '.join(json.load(sys.stdin)['calc_dir_list'])))

# 7. Repeat submission steps for SCF (adjust forward/backward files as needed, add CHGCAR/WAVECAR to backward_files for SOC)

# 8. Prepare NSCF k-path from SCF output
NSCF=$(python vasp_tools.py prepare_nscf_kpath --scf_dirs $SCF_DIRS)
NSCF_DIRS=$(echo $NSCF | python -c "import sys,json; print(' '.join(json.load(sys.stdin)['calc_dir_list'])))

# 9. Repeat submission steps for NSCF

# 10. Read NSCF results (band gap, CBM/VBM)
python vasp_tools.py read_results --calc_type nscf --calc_dir <nscf_dir>
```

---

## Configuration file (config.yaml)

`config.yaml` lives in the same directory as `vasp_tools.py`. It controls:

- `work_dir` — where all calculation subdirectories are created (default `/tmp/vasp_server`).
- `VASP_default_INCAR` — one sub-key per preset (`relaxation`, `scf_nsoc`, `scf_soc`, `nscf_nsoc`, `nscf_soc`).

**When asked about default INCAR settings, read `config.yaml` directly** — do not guess from memory, as the user may have edited it.

```bash
# Show the full config
cat "$(dirname $(which python))"/../skills/vasp/config.yaml
# Or read it in Python
python -c "import yaml; print(yaml.dump(yaml.safe_load(open('config.yaml'))))"
```

To override individual tags for a single run without editing the file, use `--incar_tags`:
```bash
--incar_tags '{"ENCUT": 600, "EDIFF": 1e-6}'
```

To permanently change a default, edit the relevant preset in `config.yaml` directly.
