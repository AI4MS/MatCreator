---
name: dpa4
description: DPA4 model finetuning and testing skill (currently neo version). Use this skill whenever finetuning a DPA-4 (SeZM) model or running model tests. DPA4 only supports finetuning (not training from scratch) and jobs run exclusively on the Bohrium platform via the dpdisp skill.
metadata:
  tools:
    - run_bash
  dependent_skills:
    - dpdisp
  tags:
    - deepmd
    - dpa4
    - sezm
    - finetuning
    - machine-learning-potential
---

# DPA4 Skill

DPA4 (SeZM-type descriptor) **finetuning only** skill. Unlike the generic `deepmd` skill,
DPA4 jobs run **exclusively on the Bohrium platform** — there is no local execution path.
DPA4 does **not** support training from scratch; it always finetunes from a pretrained model.

> **DPA4 is currently in early stage.** This skill targets the **neo** version of DPA4.
> Future versions (air, plus, pro, …) will require their own matching model and parameters.
> Each model version has a **one-to-one correspondence** with its input.json configuration —
> do not mix parameters across versions.

| Model version | Status | Description |
|---|---|---|
| **neo** | ✅ Current | Default version, supported now |
| air | 🔜 Planned | — |
| plus | 🔜 Planned | — |
| pro | 🔜 Planned | — |

---

## Version–parameter correspondence

Each DPA4 model version ships with its own pretrained model directory **and** its own
`input.json` template. The prepare script selects the correct template based on the
`--version` flag (default: `neo`).

| Version | Descriptor | Optimizer | LR schedule | Loss |
|---|---|---|---|---|
| **neo** | SeZM | HybridMuon | wsd | mae |
| air | _(TBD)_ | _(TBD)_ | _(TBD)_ | _(TBD)_ |
| plus | _(TBD)_ | _(TBD)_ | _(TBD)_ | _(TBD)_ |
| pro | _(TBD)_ | _(TBD)_ | _(TBD)_ | _(TBD)_ |

> **Rule:** The model directory and the `--version` flag must match. Using a neo model
> with air parameters (or vice versa) will produce incorrect results or fail.

---

Training and evaluation are split into two decoupled phases:

| Phase | Tool | Where |
|---|---|---|
| **Prepare** | `dpa4_prepare.py` | always local |
| **Execute** | `dp` CLI | **remote only** via Bohrium (dpdisp skill) |

Script: `dpa4_prepare.py` (in the skill's `scripts/` directory).

Use the `run_skill_script` tool to execute it:
- `skill_name`: `"dpa4"`
- `script_name`: `"dpa4_prepare.py"`
- `args`: the sub-command and flags as a single string

The tool resolves the script from the skill directory and runs it with `cwd` set to the
session working directory, so relative paths in arguments resolve correctly.

---

## Recommended Workflow — Generate a force field with DPA4

When a user asks to generate or finetune a DPA4 force field, follow this decision tree:

### Step 0 — Ask the user: Do you have a labelled dataset?

**If the user HAS a dataset (with energy + force labels):**

1. **Zero-shot test first** — freeze the pretrained model and test it on the user's data
   to check baseline energy/force accuracy without any finetuning:

   ```
   run_skill_script(
       skill_name="dpa4",
       script_name="dpa4_prepare.py",
       args="convert-data --data user_data.extxyz --outdir ./test_data"
   )
   ```

   Then submit a test-only job on Bohrium:

   ```json
   {
     "command": "dp --pt test -m <model> -s test -d result-test -l log-test",
     "forward_files": ["test_data", "<model>"],
     "backward_files": ["log-test", "result-test*"]
   }
   ```

   Report per-atom energy MAE and atomic force MAE to the user.

2. **If zero-shot results are satisfactory** → done, no finetuning needed.

3. **If zero-shot results are unsatisfactory** → sample **100 frames** from the dataset
   for finetuning, then evaluate:

   ```
   run_skill_script(
       skill_name="dpa4",
       script_name="dpa4_prepare.py",
       args="prepare-finetune --workdir ./finetune_001 --train_data user_data.extxyz --base_model /path/to/dpa4_model --numb_steps 10000"
   )
   ```

   Submit finetune + test job, then compare with zero-shot baseline.

**If the user has NO dataset:**

1. Generate a small training set (**no more than 100 frames total**) from the user's
   structures (unlabelled is fine — DFT labels will be computed if needed, or the user
   can provide them later).

2. Proceed with finetuning using the generated set, then evaluate.

> **Key principle:** Always start with zero-shot evaluation when data exists. Only finetune
> when the pretrained model is insufficient. Keep finetuning sets small (≤100 frames) to
> minimize cost.

---

## Environment variables

DPA4 requires **all** standard Bohrium variables plus two DPA4-specific variables:

| Variable | Description |
|---|---|
| `BOHRIUM_EMAIL` | Bohrium account e-mail |
| `BOHRIUM_PASSWORD` | Bohrium account password |
| `BOHRIUM_PROJECT_ID` | Bohrium project ID (integer) |
| `BOHRIUM_DPA4_MACHINE` | Machine/scass type for training, e.g. `1 * NVIDIA V100_32g` |
| `BOHRIUM_DPA4_IMAGE` | Container image URI with DPA4-compatible deepmd-kit (e.g. `registry.dp.tech/dptech/dp/native/hub/custom_images/dpa4:260522-1779446700`) |
| `BOHRIUM_DPA4_MODEL` | Path to the DPA4 pretrained model file |

> **Note:** The base model for DPA4 must be a **file** (not a directory).
> The prepare script copies it into the workdir for remote submission.

---

## Phase 1 — Preparation

`dpa4_prepare.py` converts raw structure files into `deepmd/npy` format and writes
`input.json` ready for `dp --pt train` with version-specific DPA4 configuration.
It always runs locally and requires `ase`, `dpdata`, and `numpy`.

Check env variable `BOHRIUM_DPA4_MODEL` for default pre-trained model, or submit explicit model path.

Each sub-command prints a JSON summary to stdout that includes the exact `dp` execution
command to use in Phase 2.

### 1a. Finetune a DPA4 model (single-task)

```
run_skill_script(
    skill_name="dpa4",
    script_name="dpa4_prepare.py",
    args="prepare-finetune --workdir <workdir> --train_data file1.xyz [file2.xyz ...] --base_model /path/to/dpa4_model [--version neo] [--numb_steps 10000] [--split_ratio 0.1] [--type_map Fe Ni Cu ...] [--copy_model]"
)
```

The `--version` flag selects the matching input.json template. Default is `neo`.

**Contents of `<workdir>` after preparation:**

| Path | Description |
|---|---|
| `input.json` | Training configuration for `dp --pt train` (version-specific format) |
| `train_data/` | deepmd/npy training split |
| `valid_data/` | deepmd/npy validation split (when `split_ratio > 0`) |
| `<model>` | Copy of the DPA4 pretrained model file |

> **Remote submission:** The base model must be a regular directory (not symlinks) inside
> `<workdir>` for dpdispatcher to upload it. The prepare script always copies the model
> directory for DPA4 jobs.

### 1b. Convert test data to deepmd/npy

For zero-shot evaluation or standalone testing:

```
run_skill_script(
    skill_name="dpa4",
    script_name="dpa4_prepare.py",
    args="convert-data --data test.extxyz [--outdir ./test_data] [--mixed_type] [--nframes 200]"
)
```

The command prints a JSON result with `system_dirs` and `dp_test_commands`.

---

## Phase 2 — Execution (remote on Bohrium)

All DPA4 jobs are submitted to Bohrium via the `dpdisp` skill. There is no local execution.

### Step 1 — Prepare locally

```
run_skill_script(
    skill_name="dpa4",
    script_name="dpa4_prepare.py",
    args="prepare-finetune --workdir ./finetune_001 --train_data data.extxyz --base_model /models/dpa4 --numb_steps 10000 --copy_model"
)
```

### Step 2 — Generate submission.template.json

Use `remote_profile` with an `input_data` sub-object for Bohrium.

**Finetune + test (default workflow):**

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
        "log_file": "train_log",
        "scass_type": "${BOHRIUM_DPA4_MACHINE}",
        "platform": "ali",
        "image_name": "${BOHRIUM_DPA4_IMAGE}"
      }
    }
  },
  "resources": { "group_size": 1 },
  "task_list": [
    {
      "command": "dp --pt train input.json --skip-neighbor-stat --finetune <model> > train_log 2>&1 && dp --pt freeze -c model.ckpt.pt -o frozen && dp --pt test -m frozen.pt2 -s test -d result-test -l log-test",
      "task_work_path": "./finetune_001",
      "forward_files": ["input.json", "train_data", "valid_data", "<model>"],
      "backward_files": ["model.ckpt.pt", "frozen.pt2", "lcurve.out", "train_log", "log-test", "result-test*"]
    }
  ]
}
```

**Zero-shot test only (no finetuning):**

```json
{
  "work_base": ".",
  "machine": { "..." : "..." },
  "resources": { "group_size": 1 },
  "task_list": [
    {
      "command": "dp --pt test -m <model> -s test -d result-test -l log-test",
      "task_work_path": "./zeroshot_test",
      "forward_files": ["test_data", "<model>"],
      "backward_files": ["log-test", "result-test*"]
    }
  ]
}
```

> **Note:** `<model>` is the name of the base model (file or directory) inside the workdir.
> The prepare script prints this as `model_name` in its JSON output.

### Step 3 — Substitute, validate, and submit

```bash
envsubst '${BOHRIUM_EMAIL} ${BOHRIUM_PASSWORD} ${BOHRIUM_PROJECT_ID} ${BOHRIUM_DPA4_MACHINE} ${BOHRIUM_DPA4_IMAGE}' \
    < submission.template.json > submission.json

uv run -m json.tool submission.json >/dev/null
uvx --with dpdispatcher dargs check -f dpdispatcher.entrypoints.submit.submission_args submission.json

# Always use --with oss2 for Bohrium jobs
uvx --from dpdispatcher --with oss2 dpdisp submit submission.json
```

For long-running training jobs, wrap in `tmux` to survive SSH disconnects:

```bash
tmux new-session -d -s dpa4_train \
    "uvx --from dpdispatcher --with oss2 dpdisp submit submission.json"
tmux ls
```

---

## DPA4 Command Reference

DPA4 uses different flags compared to DPA-1/DPA-2.

```bash
# Finetune from a pretrained DPA4 model (--skip-neighbor-stat is required for train only)
dp --pt train input.json --skip-neighbor-stat --finetune <model> > train_log 2>&1

# Freeze the trained model
dp --pt freeze -c model.ckpt.pt -o frozen

# Test (frozen model)
dp --pt test -m frozen.pt2 -s <test_data_dir> -d result-test -l log-test

# Test (pretrained model directory — for zero-shot evaluation)
dp --pt test -m <model> -s <test_data_dir> -d result-test -l log-test
```

Key differences from DPA-1/DPA-2:
- `--skip-neighbor-stat` flag is **required for training only** (not needed for test/freeze)
- No `--use-pretrain-script` or `--model-branch` flags
- Freeze produces `frozen.pt2` (not `frozen_model.pb`)
- Base model is a **file** (e.g. zip archive), not a directory
- **Fintuning only** — no training from scratch
- **Version-locked** — model version and input parameters must match exactly

---

## Output files

| File | Description |
|---|---|
| `model.ckpt.pt` | Saved PyTorch checkpoint |
| `frozen.pt2` | Frozen model for inference |
| `lcurve.out` | Training loss curve (step, energy MAE, force MAE, …) |
| `train_log` | Training stdout/stderr |
| `result-test*` | Test result files (per-frame energies, forces, virials) |
| `log-test` | Test evaluation log |

---

## Constraints

- DPA4 **only supports finetuning** — there is no training-from-scratch option.
- DPA4 jobs run **exclusively on Bohrium** — there is no local execution path.
- `dpa4_prepare.py` requires `ase`, `dpdata`, and `numpy` in the local Python environment.
- All input structure files must contain labeled structures (energy + forces). Unlabeled
  structures will raise an error during dpdata export.
- The base model for DPA4 must be a **file** (not a directory).
- **Model version and input parameters must match exactly** — do not mix across versions.
- `deepmd/npy` systems are written per chemical formula; use `--mixed_type` to allow
  variable composition within a single directory.
- All `task_work_path` entries in `submission.json` must share the same `work_base` directory
  (dpdispatcher requirement — see `dpdisp` skill documentation).
- When finetuning, keep training set small (≤100 frames) unless the user has a specific need.
