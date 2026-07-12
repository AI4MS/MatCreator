---
name: dpa4
description: DPA4 (SeZM) finetuning skill — finetuning and inference, remote on Bohrium. All training labels and benchmarks must come from DFT; the pretrained model is for MD exploration and inference only.
metadata:
  tools:
    - run_bash
  dependent_skills:
    - bohrium
    - dpdisp
    - vasp
    - vasp-pymatgen
    - abacus
    - atomic-structure
    - ase-deepmd
    - quests
    - lammps
  tags:
    - deepmd
    - dpa4
    - sezm
    - finetuning
    - machine-learning-potential
    - dft-labeling
---

# DPA4 Skill

DPA4 (SeZM-type descriptor) skill for **finetuning and inference**, built on the
[OMat24](https://www.aissquare.com/models/detail?pageType=models&name=DPA4-OMat24&id=423)
pretrained models. PyTorch only; the freeze output is `.pt2` (AOTInductor), not
TorchScript `.pb`.

> **All DPA4 training, inference (dp test / dp freeze), and LAMMPS MD tasks MUST
> run on Bohrium (remote).** Local execution is not supported.

**Two phases:**

| Phase | Tool | Where |
|---|---|---|
| **Prepare** | `dpa4_prepare.py` | always local |
| **Execute** | `dp` CLI / `lmp` | **remote only** via Bohrium (`bohrium` skill preferred, `dpdisp` fallback) |

Run the prepare script via `run_skill_script(skill_name="dpa4", script_name="dpa4_prepare.py", args="...")`.

---

## Model Variants

DPA4-OMat24 provides five model sizes. Each variant has its own checkpoint (`.pt`)
and training configuration (`.json`). **Do NOT mix parameters across variants.**

| Variant | Parameters | lmax | channels | n_blocks | n_focus |
|---|---|---|---|---|---|
| **DPA4-Nano** | 337,861 (0.3 M) | 1 | 32 | 1 | 1 |
| **DPA4-Mini** | 655,504 (0.7 M) | 2 | 32 | 2 | 1 |
| **DPA4-Neo** | 1,125,372 (1.1 M) | 3 | 32 | 2 | 2 |
| **DPA4-Air** | 5,148,611 (5.1 M) | 3 | 64 | 3 | 1 |
| **DPA4-Plus** | 8,849,376 (8.8 M) | 4 | 64 | 4 | 1 |

Shared settings across all variants:

| Parameter | Value |
|---|---|
| Backend | PyTorch only |
| Precision | float32 |
| Elements | Full periodic table (H–Og) |
| Cutoff radius | 6.0 Å |
| Radial basis | `bessel`, 16 functions |
| Loss | MAE: `pref_e=20`, `pref_f=20`, `pref_v=5` |
| Optimizer | HybridMuon (`weight_decay=0.001`) |

**Released files** (per variant):

| File | Description |
|---|---|
| `DPA4-<Variant>-OMat24-<version>.pt` | Model checkpoint (base for finetune/freeze) |
| `DPA4-<Variant>-OMat24-<version>.json` | Training configuration (input.json template) |

### Variant selection guidance

| Use case | Recommended variant |
|---|---|
| Quick exploration / prototyping | Nano or Mini |
| General-purpose finetuning | **Neo** (default) |
| Higher accuracy, larger systems | Air |
| Best accuracy, production runs | Plus |

> **Note:** `dpa4_prepare.py` ships templates for all five variants (nano, mini, neo,
> air, plus). The `--version` flag selects the template. Default is `neo`.

---

## Model Download

Set `BOHRIUM_DPA4_MODEL` to the path of a pre-downloaded `.pt` checkpoint. If the
variable is **not set**, the agent should download the appropriate checkpoint from
the links below.

**Latest release (v20260704):**

| File | Link |
|---|---|
| `DPA4-Nano-OMat24-v20260704.pt` | [Download](https://store.aissquare.com/models/9293690b-6758-425b-ac8c-74a6cb53235a/DPA4-Nano-OMat24-v20260704.pt) |
| `DPA4-Mini-OMat24-v20260704.pt` | [Download](https://store.aissquare.com/models/9293690b-6758-425b-ac8c-74a6cb53235a/DPA4-Mini-OMat24-v20260704.pt) |
| `DPA4-Neo-OMat24-v20260704.pt` | [Download](https://store.aissquare.com/models/9293690b-6758-425b-ac8c-74a6cb53235a/DPA4-Neo-OMat24-v20260704.pt) |
| `DPA4-Air-OMat24-v20260704.pt` | [Download](https://store.aissquare.com/models/9293690b-6758-425b-ac8c-74a6cb53235a/DPA4-Air-OMat24-v20260704.pt) |
| `DPA4-Plus-OMat24-v20260704.pt` | [Download](https://store.aissquare.com/models/9293690b-6758-425b-ac8c-74a6cb53235a/DPA4-Plus-OMat24-v20260704.pt) |

Corresponding JSON configs:

| File | Link |
|---|---|
| `DPA4-Nano-OMat24-v20260704.json` | [Download](https://store.aissquare.com/models/9293690b-6758-425b-ac8c-74a6cb53235a/DPA4-Nano-OMat24-v20260704.json) |
| `DPA4-Mini-OMat24-v20260704.json` | [Download](https://store.aissquare.com/models/9293690b-6758-425b-ac8c-74a6cb53235a/DPA4-Mini-OMat24-v20260704.json) |
| `DPA4-Neo-OMat24-v20260704.json` | [Download](https://store.aissquare.com/models/9293690b-6758-425b-ac8c-74a6cb53235a/DPA4-Neo-OMat24-v20260704.json) |
| `DPA4-Air-OMat24-v20260704.json` | [Download](https://store.aissquare.com/models/9293690b-6758-425b-ac8c-74a6cb53235a/DPA4-Air-OMat24-v20260704.json) |
| `DPA4-Plus-OMat24-v20260704.json` | [Download](https://store.aissquare.com/models/9293690b-6758-425b-ac8c-74a6cb53235a/DPA4-Plus-OMat24-v20260704.json) |

Model page: <https://www.aissquare.com/models/detail?pageType=models&name=DPA4-OMat24&id=423>

When downloading, use the **Neo** variant by default unless the user specifies otherwise.

---

## Recommended Workflow — Generate a force field with DPA4

When a user asks to generate or finetune a DPA4 force field, follow this decision tree.

> **Core principle:** The pretrained model is a **tool for exploration** (MD to discover
> candidate structures), **not a source of truth**. All training labels and evaluation
> benchmarks must come from **DFT calculations**. This is DFT-based fine-tuning,
> not distillation.

### Step 0 — Ask the user: Do you have a DFT-labelled dataset?

A "DFT-labelled dataset" means structures whose energy, forces, and virial
were computed by DFT (VASP, ABACUS, etc.), **not** by a pretrained ML model.

- **Bench mode** (`agent_mode == "bench"`): skip this question — assume NO dataset and
  proceed directly to the "NO dataset" path below.

**If the user HAS a DFT-labelled dataset:**

1. **Entropy-based structure selection (MANDATORY):**
   Use entropy-based filtering to select **100 diverse structures** for training:
   ```
   run_skill_script(
       skill_name="quests",
       script_name="active_learning.py",
       args="filter-by-entropy user_dataset.extxyz --max-sel 100 --chunk-size 10"
   )
   ```
   Remaining structures become the test set for evaluation.

2. Finetune DPA4, then run `dp test` on the test set with **both** the pretrained model
   and the finetuned model. Compare and report the improvement (energy/force MAE reduction).

3. No EOS benchmark needed — the test set already provides direct comparison.

**If the user has NO DFT-labelled dataset:**

Follow Phases A–D below. EOS benchmark is used for evaluation (no test set available).

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
   - Use the `atomic-structure` skill to build and supercell structures (NOT for MD).
   - **MD sampling tool priority:** `ase-deepmd` > `lammps`. Try `ase-deepmd` first;
     if it fails repeatedly, switch to `lammps`. Never use `atomic-structure` for MD.

4. **MD sampling parameters (NPT ensemble):**
   | Parameter | Default value | Description |
   |---|---|---|
   | Ensemble | **NPT** | NPT ensemble is mandatory for MD sampling |
   | Temperature | **500 K** | Target temperature |
   | Pressure | **1 bar** | Target pressure |
   | Duration | **5 ps** | Total simulation time |
   | Output frames | **100** | Number of structures to retain |

   > **CRITICAL:** Always use **NPT ensemble** for MD sampling. If the NPT simulation
   > fails or encounters errors, the agent MUST attempt to fix the simulation code to make
   > NPT work. **NEVER switch to a different ensemble** (e.g., NVT, NVE) without explicit
   > user approval. The agent should debug and resolve NPT issues, not avoid them.

5. **Entropy-based structure selection:**
   After MD sampling, use entropy-based filtering to select **30 diverse structures**
   from the 100 MD frames before DFT labeling:
   ```
   run_skill_script(
       skill_name="quests",
       script_name="active_learning.py",
       args="filter-by-entropy md_trajectory.extxyz --max-sel 30 --chunk-size 10"
   )
   ```
   This reduces DFT computational cost while maintaining structural diversity.

6. **Atom count rules for DFT calculations:**
   | System type | Supercell? | Target atoms |
   |---|---|---|
   | Simple (bulk, alloy) | Yes, if needed | ~50 atoms |
   | Complex (defect, surface, …) | No | original cell size |

   > Keep each DFT structure at roughly **50 atoms** when possible. For complex systems,
   > do NOT supercell — use the original cell as-is.

#### Phase B — Entropy-based structure selection & DFT labeling

**Step 1: Entropy-based structure selection (MANDATORY)**

Before DFT labeling, use entropy-based filtering to select **30 diverse structures**
from the 100 MD frames. This reduces DFT computational cost while maintaining
structural diversity.

```
run_skill_script(
    skill_name="quests",
    script_name="active_learning.py",
    args="filter-by-entropy md_trajectory.extxyz --max-sel 30 --chunk-size 10"
)
```

> **CRITICAL:** Always run entropy-based selection BEFORE DFT labeling. Never send
> all 100 MD frames directly to DFT — use the selected 30 structures instead.

**Step 2: DFT labeling**

Run DFT single-point calculations on the **selected 30 structures** to obtain energy,
force, and virial labels.

- Use the `vasp` or `abacus` skill for DFT input preparation and execution.
- See `concepts/dft-calculation` for guidance on choosing a DFT code.
- Job submission is handled by the `bohrium` skill (preferred) or `dpdisp` skill (fallback).

**Frame budget & training epochs (no user dataset):**

| System type | Max DFT frames | Epochs | Train/Test split |
|---|---|---|---|
| Simple | **30** | **50** | All for training |
| Complex | **100** | **50** | **9:1** (90 train / 10 test) |

> Training steps are computed automatically:
> `numb_steps = epochs × n_train_frames` (batch_size=1).
> 30 frames × 50 epochs = 1500 steps; 90 frames × 50 epochs = 4500 steps.

> **Simple systems (no dataset):** All 30 frames go to training — no test phase.
> Evaluation is done via EOS benchmark (Phase C).
>
> **Complex systems (no dataset):** Split 100 DFT frames into 90 training / 10 test
> using a 9:1 ratio. Run `dp test` on the test set to evaluate.
>
> **User has dataset:** Use entropy-selected 100 frames for training; excess frames
> become the test set.

#### Phase C — EOS benchmark (no-dataset path, simple systems only)

When the user has **no dataset**, there is no test set to evaluate against. Instead,
run an EOS benchmark to compare pretrained vs finetuned models against DFT ground truth.

> **Only for simple systems and only when the user has no dataset.**
> If the user has a dataset, skip this phase — the test set provides direct comparison.

1. **DFT relaxation** — relax the unit cell to find the ground-state structure.
2. **Generate deformed structures** — create 11 structures with volumes from −5% to +5%
   of the equilibrium volume (uniform spacing).
3. **DFT single-point** — compute energy for all 11 structures.
4. **Model prediction** — predict energies for the same 11 structures using both the
   pretrained model and the finetuned model.
5. **Compare** — plot E(V) curves: DFT (ground truth) vs pretrained vs finetuned.

> Steps 1–3 can run **in parallel** with Phase B (dataset DFT labeling) to save time.

#### Phase D — Finetune & evaluate

> **Do NOT reuse any existing workdir.** Always run `dpa4_prepare.py` to create a fresh
> workdir with the correct input.json, train/test split, and model copy.

1. Prepare the finetune workdir:
   ```
   # No user dataset (simple): 30 frames → all for training, no test
   run_skill_script(
       skill_name="dpa4",
       script_name="dpa4_prepare.py",
       args="prepare-finetune --workdir ./finetune_001 --train_data dft_data.extxyz --base_model /path/to/dpa4_model.pt --epochs 50"
   )

   # No user dataset (complex): 100 frames → 90 train / 10 test (9:1 split)
   run_skill_script(
       skill_name="dpa4",
       script_name="dpa4_prepare.py",
       args="prepare-finetune --workdir ./finetune_001 --train_data dft_data.extxyz --base_model /path/to/dpa4_model.pt --epochs 50 --max_train_frames 90"
   )

   # User has dataset: entropy-selected 100 frames for training, rest for test
   run_skill_script(
       skill_name="dpa4",
       script_name="dpa4_prepare.py",
       args="prepare-finetune --workdir ./finetune_001 --train_data selected_100.extxyz --base_model /path/to/dpa4_model.pt --epochs 50"
   )
   ```

2. Submit finetune job on Bohrium via the `bohrium` skill (preferred) or `dpdisp` skill (fallback).
   - If test data exists: include `test_data` in `forward_files`, run `dp test` after training.
   - If no test data: only `train_data` in `forward_files`, skip `dp test`.

3. **Evaluate:**
   - **User has dataset:** Run `dp test` on the test set with **both** the pretrained
     model and the finetuned model. Report the comparison:
     - Pretrained: energy MAE = X, force MAE = Y
     - Finetuned: energy MAE = X', force MAE = Y'
     - Improvement: energy MAE reduced by Z%, force MAE reduced by W%
   - **No dataset (simple):** Compare EOS curves (Phase C). Report DFT vs pretrained vs finetuned.
   - **No dataset (complex):** Run `dp test` on the 10-frame test set. Report pretrained vs finetuned MAE.

---

## Energy Bias Adjustment

DFT energy labels differ between datasets by an arbitrary per-element constant. Before
evaluating or simulating a system whose energy reference differs from OMat24, the
per-element energy bias can be refit **without retraining** any network weights:

```
dp --pt change-bias DPA4-<Variant>-OMat24-<version>.pt -s /path/to/system
```

This updates only the energy shift and writes an adjusted checkpoint; the descriptor
and fitting-net weights are unchanged. Use this when:
- The user's DFT data uses a different pseudopotential or energy reference than OMat24.
- You want to improve energy accuracy for a specific system before finetuning.
- You need a quick energy correction without running a full finetune.

---

## Fine-tuning Guidance

The released checkpoints serve as pretrained initializations for downstream tasks.

### Key rules

1. **Keep the `model` section unchanged** — descriptor, fitting net, and the
   full-periodic-table `type_map` must not be modified. The type embeddings are
   indexed by `type_map` and changing it breaks the model.

2. **Replace only the training/validation data** with the downstream dataset.

3. **Use a small learning rate** for finetuning — significantly lower than the
   pretraining LR. The script applies variant-specific defaults automatically
   (override with `--start_lr` if needed):

   | Variant | Default start_lr (finetune) |
   |---|---|
   | Nano | 5e-3 |
   | Mini | 1e-3 |
   | Neo | 5e-4 |
   | Air | 4e-4 |
   | Plus | 3e-4 |

   > The script's `--start_lr` default is `None`, which keeps the variant template's
   > value. The LR schedule is WSD with `warmup_ratio=0.003`, `decay_phase_ratio=0.65`.

4. **Finetune command:**
   ```
   dp --pt train input_finetune.json --finetune DPA4-<Variant>-OMat24-<version>.pt
   ```

### LoRA adapters

DPA4/SeZM supports LoRA adapters for single-task fine-tuning. The best checkpoints
fold the LoRA deltas back into the base weights, producing a plain DPA4/SeZM
checkpoint suitable for deployment without any adapter overhead.

---

## Inference Deployment

### Freeze to `.pt2`

The frozen `.pt2` is an AOTInductor archive used for inference (ASE, LAMMPS).

```
dp --pt freeze -c DPA4-<Variant>-OMat24-<version>.pt -o frozen_model
```

The PyTorch backend detects DPA4/SeZM and writes `frozen_model.pt2`.

> **The `.pt2` is target-specific** — it depends on host CPU/GPU, GPU compute
> capability, and libtorch version. Freeze on the target machine rather than
> reusing a `.pt2` across different hardware.

### Inference precision environment variables

**Precision is fixed at freeze time.** Set these before running `dp --pt freeze`:

| Variable | Default | Effect |
|---|---|---|
| `DP_TF32_INFER` | `0` (highest) | float32 matmul precision: `0` highest, `1` high, `2` medium. Keep `0` for MD and PES-smoothness-sensitive workflows. |
| `DP_TRITON_INFER` | `0` | Fused Triton inference kernels (CUDA), cumulative: `0` off; `1` universal kernels; `2` adds table-tuned SO(2) value-path kernels; `3` adds fp16 tensor-core mixing GEMMs. Levels `0`–`2` keep full float32 accumulation; `3` gives large speedup with negligible accuracy impact. |

Accepted boolean values: `1`/`true`/`yes`/`on` and `0`/`false`/`no`/`off`.

### Run with ASE

```python
from ase.io import read
from deepmd.calculator import DP

atoms = read("structure.cif")
atoms.calc = DP(model="frozen_model.pt2")

energy = atoms.get_potential_energy()
forces = atoms.get_forces()
stress = atoms.get_stress()
```

### Run in LAMMPS

The frozen `.pt2` is used through `pair_style deepmd`:

```
units           metal
atom_style      atomic
atom_modify     map yes

neighbor        2.0 bin
read_data       system.lmp

pair_style      deepmd frozen_model.pt2
pair_coeff      * * O H
```

> **`atom_modify map yes` is required.** The `.pt2` graph inference relies on an
> explicit ghost/periodic-image to local-atom map; the model fails fast without it.

The element names after `pair_coeff * *` bind LAMMPS atom types to entries of the
model's `type_map` in order (here types 1 and 2 to `O` and `H`). If omitted, the
mapping falls back to the `type_map` stored in the `.pt2` metadata.

**Multi-GPU (MPI) inference** uses the same `.pt2`. Launch one MPI rank per GPU:

```
CUDA_VISIBLE_DEVICES=0,1,2,3 mpirun -np 4 lmp -in in.lammps
```

---

## Environment variables

DPA4 requires **all** standard Bohrium variables plus two DPA4-specific variables:

| Variable | Description |
|---|---|
| `BOHRIUM_EMAIL` | Bohrium account e-mail |
| `BOHRIUM_PASSWORD` | Bohrium account password |
| `BOHRIUM_PROJECT_ID` | Bohrium project ID (integer) |
| `BOHRIUM_DPA4_MACHINE` | Machine/scass type for training, e.g. `1 * NVIDIA V100_32g` |
| `BOHRIUM_DPA4_IMAGE` | Container image URI with DPA4-compatible deepmd-kit. **Required.** |
| `BOHRIUM_DPA4_MODEL` | Path to the DPA4 pretrained model checkpoint (`.pt` file) |

> **If `BOHRIUM_DPA4_IMAGE` is not set**, use the default image:
> `registry.dp.tech/dptech/dp/native/prod-25997/deepmd-kit:3.2.0-dev`
>
> **If `BOHRIUM_DPA4_MODEL` is not set**, download the checkpoint from the
> [Model Download](#model-download) section above. Default to the **Neo** variant.

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
    args="prepare-finetune --workdir <workdir> --train_data file1.xyz [file2.xyz ...] --base_model /path/to/dpa4_model.pt [--version neo] [--epochs 50] [--max_train_frames 100] [--copy_model]"
)
```

The `--version` flag selects the matching input.json template. Default is `neo`.
The `--max_train_frames` flag caps training frames; excess goes to `test_data/` (0 = all for training, no test).

**Contents of `<workdir>` after preparation:**

| Path | Description |
|---|---|
| `input.json` | Training configuration for `dp --pt train` (version-specific format) |
| `train_data/` | deepmd/npy training split |
| `test_data/` | deepmd/npy test split (only when `--max_train_frames` is set and data exceeds it) |
| `<model>` | Copy of the DPA4 pretrained model checkpoint (`.pt` file) |

> **Remote submission:** Include `test_data` in `forward_files` only when it exists.
> The prepare script copies the model file into the workdir.

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

> Use the **fresh workdir** generated by `dpa4_prepare.py`. Do NOT point to old directories.

### Step 2a — Submit via bohrium skill (preferred)

Create a job group and submit via `bohr` CLI. Adjust `--command` and `--backward_files`
for your job type (finetune only vs finetune + test).

```bash
JOB_GROUP_ID=$(bohr job_group create -n "dpa4-finetune" -p "$BOHRIUM_PROJECT_ID" | grep -oP '\d+')
echo "$JOB_GROUP_ID" > .bohrium_job_group_id

# Finetune only (no test data)
bohr job submit \
    --project_id "$BOHRIUM_PROJECT_ID" \
    --job_name "dpa4-finetune-001" \
    --machine_type "$BOHRIUM_DPA4_MACHINE" \
    --image_address "$BOHRIUM_DPA4_IMAGE" \
    --input_directory "./finetune_001/" \
    --job_group_id "$JOB_GROUP_ID" \
    --backward_files "model.ckpt.pt,frozen.pt2,lcurve.out,train_log" \
    --command "dp --pt train input.json --finetune <model> > train_log 2>&1 && dp --pt freeze -c model.ckpt.pt -o frozen"
```

**Finetune + test (user has dataset):**

```bash
bohr job submit \
    --project_id "$BOHRIUM_PROJECT_ID" \
    --job_name "dpa4-finetune-test" \
    --machine_type "$BOHRIUM_DPA4_MACHINE" \
    --image_address "$BOHRIUM_DPA4_IMAGE" \
    --input_directory "./finetune_001/" \
    --job_group_id "$JOB_GROUP_ID" \
    --backward_files "model.ckpt.pt,frozen.pt2,lcurve.out,train_log,log-test,result-test*" \
    --command "dp --pt train input.json --finetune <model> > train_log 2>&1 && dp --pt freeze -c model.ckpt.pt -o frozen && dp --pt test -m frozen.pt2 -s test_data -d result-test -l log-test"
```

### Step 2b — Submit via dpdisp skill (fallback)

Use this method only when `bohr` CLI is unavailable or when targeting non-Bohrium backends.

**Finetune only (no test data):**

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
      "command": "dp --pt train input.json --finetune <model> > train_log 2>&1 && dp --pt freeze -c model.ckpt.pt -o frozen",
      "task_work_path": "./finetune_001",
      "forward_files": ["input.json", "train_data", "<model>"],
      "backward_files": ["model.ckpt.pt", "frozen.pt2", "lcurve.out", "train_log"]
    }
  ]
}
```

**Finetune + test (user has dataset):**

```json
{
  "work_base": ".",
  "machine": { "..." : "..." },
  "resources": { "group_size": 1 },
  "task_list": [
    {
      "command": "dp --pt train input.json --finetune <model> > train_log 2>&1 && dp --pt freeze -c model.ckpt.pt -o frozen && dp --pt test -m frozen.pt2 -s test_data -d result-test -l log-test",
      "task_work_path": "./finetune_001",
      "forward_files": ["input.json", "train_data", "test_data", "<model>"],
      "backward_files": ["model.ckpt.pt", "frozen.pt2", "lcurve.out", "train_log", "log-test", "result-test*"]
    }
  ]
}
```

> `<model>` is the base model name inside the workdir — the prepare script prints it as
> `model_name` in its JSON output.

> **CRITICAL — backward_files must include ALL outputs from the command chain.**
> The `dp --pt freeze -c model.ckpt.pt -o frozen` step produces `frozen.pt2`.
> **If `frozen.pt2` is missing from `backward_files`, the trained model will NOT be
> downloaded from Bohrium — the finetuning result is permanently lost.**
> Always verify `backward_files` contains at least: `model.ckpt.pt`, `frozen.pt2`,
> `lcurve.out`, `train_log` (plus test outputs when applicable).

### Step 3 — Substitute, validate, and submit (dpdisp only)

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

## Output files

| File | Description |
|---|---|
| `model.ckpt.pt` | Saved PyTorch checkpoint |
| `frozen.pt2` | Frozen AOTInductor model for inference |
| `lcurve.out` | Training loss curve (step, energy MAE, force MAE, …) |
| `train_log` | Training stdout/stderr |
| `result-test*` | Test result files (per-frame energies, forces, virials) |
| `log-test` | Test evaluation log |

---

## Constraints

**Environment & dependencies:**
- `dpa4_prepare.py` requires `ase`, `dpdata`, and `numpy` in the local Python environment.
- All `task_work_path` entries must share the same `work_base` (dpdispatcher requirement).

**Data & model:**
- All input structures must be **DFT-labelled** (energy + forces + virial). Unlabeled
  structures raise an error during dpdata export.
- Base model must be a `.pt` checkpoint file. Model variant and input parameters must
  match exactly — do not mix across variants.
- **`type_map` is fixed to the full periodic table (H–Og).** DPA4 uses type embeddings
  indexed by this map; do NOT restrict to dataset elements.
- `deepmd/npy` systems are written per chemical formula; use `--mixed_type` for variable
  composition within a single directory.

**Workflow rules (details in the corresponding workflow sections):**
- **MD sampling MUST use NPT ensemble** (Phase A step 4). Never switch to NVT/NVE
  without explicit user approval.
- **Entropy-based structure selection is MANDATORY** before DFT labeling (Phase A step 5 / Phase B).
- **Frame budget:** Simple systems use 30 frames; complex systems use 100 frames
  with 9:1 train/test split. All paths default to 50 epochs (Phase B).
  Steps are auto-computed: `numb_steps = epochs × n_train` (batch_size=1).
- **Atom count:** ~50 atoms/DFT structure. Complex systems must NOT be supercelled (Phase A step 6).
- **EOS benchmark** is for no-dataset simple-system path only (Phase C).
- **Evaluation always compares pretrained vs finetuned** (Phase D step 3).

**Deployment:**
- Frozen `.pt2` is target-specific — depends on host hardware and libtorch version.
  Always freeze on the deployment target machine.
- `atom_modify map yes` is required in LAMMPS input scripts when using `.pt2` models.
