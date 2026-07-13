---
name: deepmd
description: DeePMD-kit training, finetuning, testing, and model inspection skill. Use this skill whenever training or finetuning a Deep Potential (DP / DPA-1 / DPA-2) model, running model tests, or inspecting model parameters. Training is split into a preparation phase (data conversion + input.json generation, always local) and an execution phase (dp CLI commands, local or via bohrium skill on Bohrium cloud).
metadata:
  tools:
    - run_bash
  dependent_skills:
    - bohrium
  tags:
    - deepmd
    - dpa
    - training
    - finetuning
    - machine-learning-potential
---

# DeePMD-kit Skill

Training and evaluation are split into two decoupled phases:

| Phase | Tool | Where |
|---|---|---|
| **Prepare** | `deepmd_prepare.py` | always local |
| **Execute** | `dp` CLI | local **or** remote via bohr skill |

Script: `deepmd_prepare.py` (in the skill's `scripts/` directory).

Use the `run_skill_script` tool to execute it:
- `skill_name`: `"deepmd"`
- `script_name`: `"deepmd_prepare.py"`
- `args`: the sub-command and flags as a single string

The tool resolves the script from the skill directory and runs it with `cwd` set to the
session working directory, so relative paths in arguments resolve correctly.

---

## Phase 1 — Preparation

`deepmd_prepare.py` converts raw structure files into `deepmd/npy` format and writes
`input.json` ready for `dp train`. It always runs locally and requires `ase`, `dpdata`,
and `numpy`.

Check env variable `DEEPMD_MODEL_PATH` for default pre-trained model, or submit explicit model path.

Each sub-command prints a JSON summary to stdout that includes the exact `dp` execution
command to use in Phase 2.

### 1a. Train from scratch

```
run_skill_script(
    skill_name="deepmd",
    script_name="deepmd_prepare.py",
    args="prepare-training --workdir <workdir> --train_data file1.xyz [file2.xyz ...] [--numb_steps 1000] [--rcut 6.0] [--rcut_smth 0.5] [--descriptor_neuron 25 50 100] [--neuron 240 240 240] [--split_ratio 0.1] [--type_map Fe Ni Cu ...] [--impl pytorch] [--mixed_type] [--seed 42]"
)
```

### 1b. Finetune a DPA model (single-task)

```
run_skill_script(
    skill_name="deepmd",
    script_name="deepmd_prepare.py",
    args="prepare-finetune --workdir <workdir> --train_data file1.xyz [...] --base_model /path/to/model.pt [--head <branch_name>] [--numb_steps 500] [--split_ratio 0.1] [--type_map Fe Ni ...] [--copy_model]"
)
```

By default `--head` is `Omat24`, so the generated command includes `--model-branch Omat24`.
Pass `--head none` to reinitialise the fitting net instead.

### 1c. Finetune a DPA model (multi-task)

```
run_skill_script(
    skill_name="deepmd",
    script_name="deepmd_prepare.py",
    args="prepare-finetune-multitask --workdir <workdir> --base_model /path/to/model.pt --task_data task1:file1.xyz,file2.xyz task2:file3.xyz [--numb_steps 500] [--neuron 240 240 240] [--model_prob 1.0] [--copy_model]"
)
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
# Default (head=Omat24) → continue from the Omat24 branch
dp --pt train input.json --finetune <model>.pt --use-pretrain-script \
    --model-branch Omat24

# head=none → reinitialise fitting network
dp --pt train input.json --finetune <model>.pt --use-pretrain-script
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

```
run_skill_script(
    skill_name="deepmd",
    script_name="deepmd_prepare.py",
    args="convert-data --data test.extxyz [test2.extxyz ...] --outdir ./test_data [--mixed_type] [--head <head_name>] [--nframes 200]"
)
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

## Remote execution via the bohrium skill (preferred)

The primary submission method uses the `bohrium` skill (`bohr` CLI).

### Environment variables

| Variable | Description |
|---|---|
| `BOHRIUM_PROJECT_ID` | Bohrium project ID (integer) |
| `BOHRIUM_DEEPMD_MACHINE` | Machine type for training, e.g. `1 * NVIDIA V100_32g` |
| `BOHRIUM_DEEPMD_IMAGE` | Container image URI with deepmd-kit installed, e.g. `registry.dp.tech/dptech/deepmd-kit:3.1.3` |

Authentication is handled via `bohr login` (credentials stored in `~/.bohrium/credentials.yaml`).

### Step 1 — Prepare locally

Always use `--copy_model` for finetune jobs so the model file is a regular file inside `<workdir>`.

```
run_skill_script(
    skill_name="deepmd",
    script_name="deepmd_prepare.py",
    args="prepare-finetune --workdir ./train_001 --train_data data.extxyz --base_model /models/DPA2.pt --numb_steps 2000 --copy_model"
)
```

### Step 2 — Create a job group and submit

```bash
# Create a job group for tracking
JOB_GROUP_ID=$(bohr job_group create -n "deepmd-train" -p "$BOHRIUM_PROJECT_ID" | grep -oP '\d+')
echo "$JOB_GROUP_ID" > .bohrium_job_group_id

# Submit — adjust --command and --backward_files for your job type
bohr job submit \
    --project_id "$BOHRIUM_PROJECT_ID" \
    --job_name "deepmd-train-001" \
    --machine_type "$BOHRIUM_DEEPMD_MACHINE" \
    --image_address "$BOHRIUM_DEEPMD_IMAGE" \
    --input_directory "./train_001/" \
    --job_group_id "$JOB_GROUP_ID" \
    --backward_files "model.ckpt.pt,lcurve.out,log,err" \
    --command "dp --pt train input.json"
```

**Finetuning (single-task):**

```bash
bohr job submit \
    --project_id "$BOHRIUM_PROJECT_ID" \
    --job_name "deepmd-finetune-001" \
    --machine_type "$BOHRIUM_DEEPMD_MACHINE" \
    --image_address "$BOHRIUM_DEEPMD_IMAGE" \
    --input_directory "./train_001/" \
    --job_group_id "$JOB_GROUP_ID" \
    --backward_files "model.ckpt.pt,input.json,lcurve.out,log,err" \
    --command "dp --pt train input.json --finetune DPA2.pt --use-pretrain-script --model-branch Omat24"
```

**Finetuning (multi-task):**

```bash
bohr job submit \
    --project_id "$BOHRIUM_PROJECT_ID" \
    --job_name "deepmd-finetune-mt" \
    --machine_type "$BOHRIUM_DEEPMD_MACHINE" \
    --image_address "$BOHRIUM_DEEPMD_IMAGE" \
    --input_directory "./train_001/" \
    --job_group_id "$JOB_GROUP_ID" \
    --backward_files "model.ckpt.pt,input.json,lcurve.out,log,err" \
    --command "dp --pt train input.json --finetune DPA2.pt --use-pretrain-script"
```

### Step 3 — Monitor and download results

```bash
# Persist job group ID (already done in step 2, but re-read if session restarted)
GROUP_ID=$(cat .bohrium_job_group_id)

# Poll until all jobs reach terminal state
while true; do
    OUTPUT=$(timeout 120 bohr job list -j "$GROUP_ID" --json 2>/dev/null)
    TOTAL=$(echo "$OUTPUT" | jq 'length')
    DONE=$(echo "$OUTPUT" | jq '[.[] | select(.status == "Finished" or .status == "Failed" or .status == "Cancelled" or .status == "Terminated")] | length')
    FAILED=$(echo "$OUTPUT" | jq '[.[] | select(.status == "Failed" or .status == "Cancelled" or .status == "Terminated")] | length')
    echo "[$(date '+%H:%M:%S')] $DONE/$TOTAL jobs done, $FAILED failed"
    if [ "$DONE" -eq "$TOTAL" ] && [ "$TOTAL" -gt 0 ]; then
        [ "$FAILED" -gt 0 ] && echo "$FAILED job(s) failed!" && break
        echo "All $TOTAL jobs finished successfully!" && break
    fi
    sleep 60
done

# Download all results
bohr job_group download -j "$GROUP_ID" -o ./output/
```

For long-running training jobs, wrap the submission + polling in `tmux`:

```bash
tmux new-session -d -s deepmd_train "bash -c '...submit+poll commands...'"
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
  (dpdispatcher requirement).
