---
name: deepmd
description: >
  DeePMD-kit training, finetuning, testing, and model inspection skill.
  Use this skill whenever training or finetuning a Deep Potential (DP / DPA-1 / DPA-2)
  model, running model tests, or inspecting model parameters.
  Training is split into a **preparation phase** (data conversion + input.json generation,
  always local) and an **execution phase** (dp CLI commands, local or via dpdisp skill on
  hpc or Bohrium).
tags: [deepmd, dpa, training, finetuning, machine-learning-potential]
tools: [run_bash]
dependent_skills: [dpdisp]
---

# DeePMD-kit Skill

Training and evaluation are split into two decoupled phases:

| Phase | Tool | Where |
|---|---|---|
| **Prepare** | `deepmd_prepare.py` | always local |
| **Execute** | `dp` CLI | local **or** remote via dpdisp skill |

Script location:
```
skills/deepmd/deepmd_prepare.py
```

---

## ⚠️ Critical: No Symlinks for Remote Submission

**Problem**: dpdispatcher **cannot upload symlinks**. If `train_data/`, `valid_data/`, or model files are symlinks, they will appear empty on the remote server, causing `FileNotFoundError`.

**Solution**: Always use **actual file copies** (not symlinks) for all files in `forward_files`.

### How to verify and fix symlinks

```bash
# Check if files are symlinks
ls -la train_data/ | head -5
# If you see "lrwxrwxrwx" → these are symlinks (BAD for dpdispatcher)

# Fix: Copy actual files instead of symlinks
python << 'EOF'
import os, shutil
src_dir = "/path/to/original/deepmd_npy"
dst_dir = "./train_data"
os.makedirs(dst_dir, exist_ok=True)
for sys in os.listdir(src_dir):
    shutil.copytree(os.path.join(src_dir, sys), os.path.join(dst_dir, sys))
print(f"Copied {len(os.listdir(dst_dir))} systems (actual files, not symlinks)")
EOF

# Verify no symlinks remain
find train_data valid_data -type l  # Should return nothing
```

### When symlinks are created

The `deepmd_prepare.py` script may create symlinks when:
- Reusing existing `deepmd/npy` datasets from another directory
- Copying base model files without `--copy_model` flag

**Always verify** before submitting to Bohrium:
```bash
ls -la train_data/ valid_data/ *.pt  # Check for "l" at start of permissions
```

---

## Phase 1 — Preparation

`deepmd_prepare.py` converts raw structure files into `deepmd/npy` format and writes
`input.json` ready for `dp train`. It always runs locally and requires `ase`, `dpdata`,
and `numpy`.

Check env variable `DEEPMD_MODEL_PATH` for default pre-trained model, or submit explicit model path.

Each sub-command prints a JSON summary to stdout that includes the exact `dp` execution
command to use in Phase 2.

### 1a. Train from scratch

```bash
python skills/deepmd/deepmd_prepare.py prepare-training \
    --workdir    <workdir>             \
    --train_data file1.xyz [file2.xyz ...] \
    [--numb_steps        1000]         \
    [--rcut              6.0]          \
    [--rcut_smth         0.5]          \
    [--descriptor_neuron 25 50 100]    \
    [--neuron            240 240 240]  \
    [--split_ratio       0.1]          \
    [--type_map          Fe Ni Cu ...] \
    [--impl              pytorch]      \
    [--mixed_type]                     \
    [--seed              42]
```

### 1b. Finetune a DPA model (single-task)

```bash
python skills/deepmd/deepmd_prepare.py prepare-finetune \
    --workdir    <workdir>             \
    --train_data file1.xyz [...]       \
    --base_model /path/to/model.pt     \
    [--head      <branch_name>]        \
    [--numb_steps 500]                 \
    [--split_ratio 0.1]                \
    [--type_map   Fe Ni ...]           \
    [--copy_model]      # required when submitting to Bohrium
```

### 1c. Finetune a DPA model (multi-task)

```bash
python skills/deepmd/deepmd_prepare.py prepare-finetune-multitask \
    --workdir    <workdir>                              \
    --base_model /path/to/model.pt                      \
    --task_data  task1:file1.xyz,file2.xyz  task2:file3.xyz \
    [--numb_steps 500]                                  \
    [--neuron     240 240 240]                          \
    [--model_prob 1.0]                                  \
    [--copy_model]      # required when submitting to Bohrium
```

**Contents of `<workdir>` after preparation:**

| Path | Description |
|---|---|
| `input.json` | Training configuration for `dp train` |
| `train_data/` | deepmd/npy training split |
| `valid_data/` | deepmd/npy validation split (when `split_ratio > 0`) |
| `train_data_<task>/` | Per-task training data (multitask only) |
| `valid_data_<task>/` | Per-task validation data (multitask only) |
| `<model>.pt` | Copy or symlink to base model (finetune variants) |

> **Remote submission:** The base model must be a regular file (not a symlink) inside
> `<workdir>` for dpdispatcher to upload it. Pass `--copy_model` during preparation to
> copy the file rather than symlink it.

---

## Phase 2 — Execution (local)

All commands run from **inside the workdir** (`cd <workdir>`).

### Training from scratch (PyTorch backend)

```bash
dp --pt train input.json
```

### Training from scratch (TensorFlow backend)

```bash
dp train input.json
dp freeze -o frozen_model.pb      # export frozen graph after training
```

### Finetuning — single-task

```bash
# head=None → reinitialise fitting network
dp --pt train input.json --finetune <model>.pt --use-pretrain-script

# head specified → continue from an existing branch
dp --pt train input.json --finetune <model>.pt --use-pretrain-script \
    --model-branch <head_name>
```

### Finetuning — multi-task

```bash
dp --pt train input.json --finetune <model>.pt --use-pretrain-script
```

### Restarting an interrupted run

```bash
dp --pt train input.json --restart model.ckpt
```

### Output files

| File | Description |
|---|---|
| `model.ckpt.pt` | Saved PyTorch checkpoint |
| `lcurve.out` | Training loss curve (step, energy MAE, force MAE, …) |
| `input_v2_compat.json` | Updated config written by compat migration (finetune only) |

---

## Phase 3 — Test / Evaluation

`dp test` computes energy and force MAE / RMSE against a labelled dataset.
Its `-s` argument must point to a `deepmd/npy` system directory, not a raw xyz file.
Use the `convert-data` sub-command to convert any ASE-readable format first.

### 3a. Convert test data to deepmd/npy

```bash
python skills/deepmd/deepmd_prepare.py convert-data \
    --data   test.extxyz [test2.extxyz ...]  \
    --outdir ./test_data                     \
    [--mixed_type]                           \
    [--head  <head_name>]                    \
    [--nframes 200]
```

The command prints a JSON result containing:

| Field | Description |
|---|---|
| `outdir` | Absolute path to the output directory |
| `system_dirs` | List of `deepmd/npy` system directories created |
| `dp_test_commands` | Ready-to-run `dp --pt test` command(s) with all flags filled in |

The `--head` and `--nframes` flags are optional — they are only used to pre-fill the
printed `dp test` commands; they do not affect the data conversion.

### 3b. Run dp test

> **Tip:** Run `dp --pt test --help` to see the full list of available flags and options.

Copy the commands from the JSON output, substituting the actual model path.
Always add `-d` to write per-frame detailed output files (DFT vs DP energies, forces, virials, pairs, etc.):

```bash
# Single-task model
dp --pt test -m model.ckpt.pt -s ./test_data/<system_dir> [-n <nframes>] -d

# Multi-task model — specify the head to evaluate
dp --pt test -m model.ckpt.pt -s ./test_data/<system_dir> --head <head_name> [-n <nframes>] -d
```

**Output files** (written to the current directory):

| File | Description |
|---|---|
| `e_peratom.out` | Per-frame: DFT energy/atom vs predicted energy/atom (eV/atom) |
| `f.out` | Per-component: DFT force vs predicted force (eV/Å) |
| stdout | Summary MAE / RMSE for energy and forces |

> The `-d` flag enables detailed output: per-frame DFT and DP energies, forces, virials, and pair information are written to separate files for further analysis.

---

## Phase 4 — Model inspection and compression

```bash
# List available heads/branches (multi-task model)
dp show model.ckpt.pt model-branch

# Inspect descriptor parameters
dp show model.ckpt.pt descriptor

# Compress model for faster inference
dp --pt compress -i model.ckpt.pt -o model_compressed.pt
```

---

## Remote execution via the dpdisp skill

Submission uses the `dpdisp` skill (DPDispatcher) with `BohriumContext`. The `bohr` skill and `bohrium_submit.py` are deprecated — do not use them for new workflows.

### Environment variables

| Variable | Description |
|---|---|
| `BOHRIUM_EMAIL` | Bohrium account e-mail |
| `BOHRIUM_PASSWORD` | Bohrium account password |
| `BOHRIUM_PROJECT_ID` | Bohrium project ID (integer) |
| `BOHRIUM_DEEPMD_MACHINE` | Machine/scass type for training, e.g. `gpu_8_v100_32g` |
| `BOHRIUM_DEEPMD_IMAGE` | Container image URI with deepmd-kit installed |

### ⚠️ IMPORTANT: Add Descriptive Job Names for Bohrium

When submitting to Bohrium, **always add a descriptive `job_name`** in the `input_data` section. This makes jobs easy to identify on the Bohrium platform.

**Job name format:** `<system>_<calc_type>_<key_params>`

Examples:
- `Fe32_bulk_DPA2_finetune_2000steps` — Fe bulk, DPA-2 finetuning, 2000 steps
- `LiCoO2_surface_dp_train_5000steps` — LiCoO2 surface, training from scratch, 5000 steps
- `Cu_Au_multitask_finetune_v2` — Cu-Au multi-task finetuning, version 2
- `TiO2_Pt_cluster_dp_test` — TiO2-Pt cluster, model testing

If the user doesn't specify a job name, **construct one automatically** based on:
- Material/system name
- Calculation type (train, finetune, test, etc.)
- Key parameters (steps, model version, data descriptor, etc.)

### Step 1 — Prepare locally

Always use `--copy_model` for finetune jobs so the model file is a regular file inside `<workdir>` (dpdispatcher cannot upload symlinks).

```bash
python skills/deepmd/deepmd_prepare.py prepare-finetune \
    --workdir    ./train_001          \
    --train_data data.extxyz          \
    --base_model /models/DPA2.pt      \
    --numb_steps 2000                 \
    --copy_model
```

### Step 2 — Generate submission.template.json

Use `remote_profile` with an `input_data` sub-object for Bohrium. **Always include `job_name`.** Adjust `forward_files` to match the job type (see variants below).

**Training from scratch:**

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
        "job_name": "<system>_train_<steps>steps",
        "job_type": "container",
        "log_file": "log",
        "scass_type": "${BOHRIUM_DEEPMD_MACHINE}",
        "platform": "ali",
        "image_name": "${BOHRIUM_DEEPMD_IMAGE}"
      }
    }
  },
  "resources": { "group_size": 1 },
  "task_list": [
    {
      "command": "dp --pt train input.json",
      "task_work_path": "./train_001",
      "forward_files": ["input.json", "train_data", "valid_data"],
      "backward_files": ["model.ckpt.pt", "lcurve.out", "log", "err"]
    }
  ]
}
```

**Finetuning (single-task)** — add the model file to `forward_files` and extend the command:

```json
{
  "command": "dp --pt train input.json --finetune DPA2.pt --use-pretrain-script",
  "task_work_path": "./train_001",
  "forward_files": ["input.json", "train_data", "valid_data", "DPA2.pt"],
  "backward_files": ["model.ckpt.pt", "input.json", "lcurve.out", "log", "err"]
}
```

**Finetuning (multi-task)** — list each per-task data directory explicitly:

```json
{
  "command": "dp --pt train input.json --finetune DPA2.pt --use-pretrain-script",
  "task_work_path": "./train_001",
  "forward_files": [
    "input.json",
    "train_data_task1", "valid_data_task1",
    "train_data_task2", "valid_data_task2",
    "DPA2.pt"
  ],
  "backward_files": ["model.ckpt.pt", "input.json", "lcurve.out", "log", "err"]
}
```

> Directory names in `forward_files` are uploaded recursively by dpdispatcher.

### Step 3 — Substitute, validate, and submit

```bash
envsubst '${BOHRIUM_EMAIL} ${BOHRIUM_PASSWORD} ${BOHRIUM_PROJECT_ID} ${BOHRIUM_DEEPMD_MACHINE} ${BOHRIUM_DEEPMD_IMAGE}' \
    < submission.template.json > submission.json

uv run -m json.tool submission.json >/dev/null
uvx --with dpdispatcher dargs check -f dpdispatcher.entrypoints.submit.submission_args submission.json

# Always use --with oss2 for Bohrium jobs (oss2 is not bundled with dpdispatcher in uvx environments)
uvx --from dpdispatcher --with oss2 dpdisp submit submission.json
```

For long-running training jobs, wrap in `tmux` to survive SSH disconnects:

```bash
tmux new-session -d -s deepmd_train \
    "uvx --from dpdispatcher --with oss2 dpdisp submit submission.json"
tmux ls
```

---

## Constraints

- `deepmd_prepare.py` requires `ase`, `dpdata`, and `numpy` in the local Python environment.
- All input structure files must contain labeled structures (energy + forces). Unlabeled
  structures will raise an error during dpdata export.
- For multi-task finetuning the base model must be a DPA-2 multi-task checkpoint.
- `deepmd/npy` systems are written per chemical formula; use `--mixed_type` to allow
  variable composition within a single directory.
- All `task_work_path` entries in `submission.json` must share the same `work_base` directory
  (dpdispatcher requirement — see `dpdisp` skill documentation).
- **CRITICAL**: All files in `forward_files` must be **actual files**, not symlinks. dpdispatcher silently skips symlinks, causing `FileNotFoundError` on remote.

---

## Troubleshooting

### FileNotFoundError for train_data/valid_data on remote

**Symptom**: Job fails with error like:
```
FileNotFoundError: train_data/Li12O24Co11... not found
```

**Cause**: `train_data/` or `valid_data/` contains symlinks instead of actual files.

**Diagnosis**:
```bash
ls -la train_data/ | head -5
# If first character is "l" (lrwxrwxrwx), these are symlinks
```

**Fix**:
1. Delete symlink directories: `rm -rf train_data valid_data`
2. Copy actual files using `shutil.copytree()` (see "Critical: No Symlinks" section above)
3. Verify: `find train_data valid_data -type l` should return nothing
4. Resubmit task