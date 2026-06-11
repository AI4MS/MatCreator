---
name: dpa4
description: DPA4 (SeZM) finetuning skill — finetuning only, remote on Bohrium. All training labels and benchmarks must come from DFT; the pretrained model is for MD exploration only.
metadata:
  tools:
    - run_bash
  dependent_skills:
    - dpdisp
    - vasp
    - abacus
    - atomic-structure
  tags:
    - deepmd
    - dpa4
    - sezm
    - finetuning
    - machine-learning-potential
    - dft-labeling
---

# DPA4 Skill

DPA4 (SeZM-type descriptor) **finetuning only** skill, targeting the **neo** version.
Future versions (air, plus, pro) will ship their own model and parameters — do not mix
across versions.

**Two phases:**

| Phase | Tool | Where |
|---|---|---|
| **Prepare** | `dpa4_prepare.py` | always local |
| **Execute** | `dp` CLI | **remote only** via Bohrium (`dpdisp` skill) |

Run the prepare script via `run_skill_script(skill_name="dpa4", script_name="dpa4_prepare.py", args="...")`.

---

## Recommended Workflow — Generate a force field with DPA4

When a user asks to generate or finetune a DPA4 force field, follow this decision tree.

> **Core principle:** The pretrained model is a **tool for exploration** (MD to discover
> candidate structures), **not a source of truth**. All training labels and evaluation
> benchmarks must come from **DFT calculations**. This is DFT-based fine-tuning,
> not distillation.

### Step 0 — Ask the user: Do you have a DFT-labelled dataset?

A "DFT-labelled dataset" means structures whose energy, forces, and (optionally) virial
were computed by DFT (VASP, ABACUS, etc.), **not** by a pretrained ML model.

- **Bench mode** (`agent_mode == "bench"`): skip this question — assume NO dataset and
  proceed directly to the "NO dataset" path below.

**If the user HAS a DFT-labelled dataset:**

1. Use the dataset directly for finetuning (sample at most **100 frames**).

2. Run DFT benchmarks (EOS, elastic constants, etc.) on the equilibrium structure as
   the ground-truth reference. Use the `vasp` or `abacus` skill — see
   `concepts/dft-calculation` for choosing between them.

3. Finetune DPA4, then evaluate against the DFT benchmarks. Report the comparison
   (model predictions vs DFT ground truth).

**If the user has NO DFT-labelled dataset:**

Follow Phases A–D below.

---

#### Phase A — Determine system complexity & generate candidate structures

1. **Classify the system:**
   - **Simple systems** — bulk crystals, random alloys, simple compounds.
   - **Complex systems** — defects, dopants, surfaces, interfaces, transition states,
     high-entropy alloys, amorphous structures, etc.

2. **For complex systems: ask the user if they already have structure files.**
   If yes, use the user's structures as the starting point. If no, generate them
   using the `atomic-structure` skill (or `matcraft-kit` for surfaces/defects).

3. **Generate candidate structures** for MD exploration:
   - Use the pretrained model **only for MD** to explore configuration space.
   - Use the `atomic-structure` skill to build, supercell, and perturb structures.

4. **Atom count rules for DFT calculations:**
   | System type | Supercell? | Target atoms |
   |---|---|---|
   | Simple (bulk, alloy) | Yes, if needed | ~50 atoms |
   | Complex (defect, surface, …) | No | original cell size |

   > Keep each DFT structure at roughly **50 atoms** when possible. For complex systems,
   > do NOT supercell — use the original cell as-is.

#### Phase B — DFT labeling

Run DFT single-point calculations on all candidate structures to obtain energy, force,
and virial labels.

- Use the `vasp` or `abacus` skill for DFT input preparation and execution.
- See `concepts/dft-calculation` for guidance on choosing a DFT code.
- Job submission is handled by the `dpdisp` skill (Bohrium).

**Frame budget:**

| System type | Max DFT frames |
|---|---|
| Simple | **30** |
| Complex | **100** |

#### Phase C — DFT benchmarks

Run DFT calculations for property benchmarks for evaluation. These DFT values are the
**absolute ground truth** — the finetuned model is evaluated against them.

| Property | What to compute |
|---|---|
| EOS | Energy vs. volume curve around equilibrium |
| Elastic constants | Cij matrix from strain-energy relations |
| Other | Formation energy, surface energy, defect energy — as relevant |

- Use the equilibrium structure. Do NOT supercell unless the system is simple and
  supercelling is appropriate for the property.
- Reference the `vasp` or `abacus` skill for setup.

#### Phase D — Finetune & evaluate

1. Convert the DFT-labelled dataset to `deepmd/npy` format:
   ```
   run_skill_script(
       skill_name="dpa4",
       script_name="dpa4_prepare.py",
       args="prepare-finetune --workdir ./finetune_001 --train_data dft_data.extxyz --base_model /path/to/dpa4_model --numb_steps 10000"
   )
   ```

2. Submit finetune + test job on Bohrium via the `dpdisp` skill.

3. Evaluate the finetuned model against DFT benchmarks (Phase C). Report the comparison.

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

`dpa4_prepare.py` converts raw structures to `deepmd/npy` and writes `input.json`.
Each sub-command prints a JSON summary with the exact `dp` command for Phase 2.

Check `BOHRIUM_DPA4_MODEL` for the default pretrained model, or pass `--base_model` explicitly.

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

For standalone testing or benchmark evaluation:

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

### Step 1 — Prepare locally (see Phase 1)

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

> `<model>` is the base model name inside the workdir — the prepare script prints it as
> `model_name` in its JSON output.

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

# Test (pretrained model directory — for standalone evaluation)
dp --pt test -m <model> -s <test_data_dir> -d result-test -l log-test
```

Key differences from DPA-1/DPA-2:
- `--skip-neighbor-stat` required for training only (not test/freeze)
- No `--use-pretrain-script` or `--model-branch` flags
- Freeze produces `frozen.pt2` (not `frozen_model.pb`)
- Base model is a **file** (e.g. zip archive), not a directory

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

- `dpa4_prepare.py` requires `ase`, `dpdata`, and `numpy` in the local Python environment.
- All input structures must be **DFT-labelled** (energy + forces). Unlabeled structures
  raise an error during dpdata export.
- Base model must be a **file** (not a directory). Model version and input parameters must
  match exactly — do not mix across versions.
- `deepmd/npy` systems are written per chemical formula; use `--mixed_type` for variable
  composition within a single directory.
- All `task_work_path` entries must share the same `work_base` (dpdispatcher requirement).
- **Frame budget:** simple systems ≤30 DFT frames; complex systems ≤100 DFT frames.
- **Atom count:** ~50 atoms/DFT structure. Simple systems may supercell; complex systems
  (defects, dopants, surfaces, interfaces, transition states, high-entropy alloys) must NOT.
- **DFT benchmarks are mandatory** — EOS, elastic constants, etc. computed with DFT as the
  absolute ground truth.
