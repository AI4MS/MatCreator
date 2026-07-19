---
name: deepmd
description: Deep potential models finetuning and testing using the DeePMD-kit.
  Use this skill whenever finetuning a Deep Potential (DPA-1 / DPA-2 / DPA-3 / DPA-4) model
  or running model tests on a dataset. The oldest DP descriptors such as se_e2_a, se_e2_r,
  and se_e3 are no longer supported. 
  Training from scratch is NEVER advised unless distilling a student model from a teacher model.
  Multitask fine-tuning is NOT supported.
metadata:
  tools:
    - run_bash
    - run_skill_script
  dependent_skills:
    - bohrium
    - dpdisp
  tags:
    - deepmd
    - dpa
    - finetuning
    - testing
    - machine-learning-potential
---

# DeePMD-kit CLI (fine-tuning, testing)

Every operation to be performed with the deepmd-kit CLI, including fine-tuning and `dp test` testing,
should be divided into:
1. a **preparation** stage (including data conversion + input.json generation, should be performed locally);
2. an **execution** stage (executing dp CLI commands, local or via bohrium skill on Bohrium cloud).

| Stage       | Tool | Where                                              |
|-------------|---|----------------------------------------------------|
| **Prepare** | `deepmd_prepare.py` | always run locally                                 |
| **Execute** | `dp` CLI | run locally **or** submit remotelly via bohr skill |

Script: `deepmd_prepare.py` ([scripts/deepmd_prepare.py](scripts/deepmd_prepare.py]).
Use the `run_skill_script` tool to execute:
- `skill_name`: `"deepmd"`
- `script_name`: `"deepmd_prepare.py"`
- `args`: the sub-command and flags as a single string

The tool will resolve the script from the skill directory and runs it with `cwd` set to the
session working directory, so relative paths in arguments resolve correctly.


## Phase 1 — Preparation

`deepmd_prepare.py` converts raw structure files into `deepmd/npy` format. In preparation of fine-tuning tasks,
it also copies (or symlinks) the base model and writes `input.json` ready for `dp train`.

It always runs locally and requires `ase`, `dpdata`, and `numpy`.

Check reference [references/supported_deepmd_models.md](references/supported_deepmd_models.md) of this skill
carefully for details of available DPA models, variants and their usage.

Each sub-command prints a summary message in json format through logging.logger that includes the exact `dp` execution
command to use in Phase 2. The message will start with something like `"CLI execution summary:..."`.

Use subcommand `prepare-finetune` for fine-tuning, `prepare-test` for testing.

If you are not sure about the arguments, try `deepmd_prepare.py --help` or `deepmd_prepare.py <subcommand> --help`
before running.

> **Notice:**
> 1. Unless absolutely necessary, **keep all optional arguments at their default values**. Default model parameters
> and training parameters, etc.
> Set only the required ones, including choice of model and variant, path to model file, specification of data, etc.
> 2. **Remote submission:** The base model must be a regular file (not a symlink) inside
> `<workdir>` for dpdispatcher to upload it. NEVER use symlinks when performing remote submission!

Expected contents of `<workdir>` after preparation can be found in the docs and help message of `deepmd_prepare.py`.


## Phase 2 — Execution

### Local execution
All commands must run from **inside the workdir** (`cd <workdir>`).
Run the command as returned in Phase 1's summary message.


### Remote submission to Bohrium (**preferred**)

The primary submission method uses the `bohrium` skill (`bohr` CLI). Refer to the skill `bohrium` for details.

### Handling long jobs
For time-consuming fine-tuning jobs, you can wrap the submission + polling in `tmux`:
```bash
tmux new-session -d -s deepmd_train "bash -c '...submit+poll commands...'"
tmux ls
```


### Restarting an interrupted run

```bash
dp --pt train input.json --restart model.ckpt.pt
```

> **Notice:** 
> 1. The restart command must be run from the workdir.
> 2. When resuming a Bohrium run, you must first download the bohrium output, copy model.ckpt.pt to the workdir,
>    and then resubmit with the restart command.

### Expected output files

#### Fine-tuning tasks

| File                                    | Description                                                                                                                                                                                                                                     |
|-----------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `model.ckpt.pt`                         | Saved PyTorch checkpoint                                                                                                                                                                                                                        |
| `frozen.pt2`(DPA-4)/`frozen.pth`(other) | (Optional) Frozen AOTInductor model or TorchScript for fast inference                                                                                                                                                                           |
| `lcurve.out`                            | Training loss curve (step, energy MAE, force MAE, etc…)                                                                                                                                                                                         |
| `train_log`                             | Training logfile, expect stdout/stderr                                                                                                                                                                                                          |
| `result-train*`                         | Result files of `dp test` on training set (per-frame energies `results-train.e.out`, energy-per-atom `results-train.e_peratom.out`, forces `results-train.f.out`, virials `results-train.v.out`, virial-per-atom `results-train.v_peratom.out`) |
| `result-test*`                          | Result files of `dp test` on testing set                                                                                                                                                                                                        |
| `log-train`                             | Logfile of `dp test` evaluation on training set, expect stdout/stderr                                                                                                                                                                           |
| `log-test`                              | Logfile of `dp test` evaluation on testing set, expect stdout/stderr                                                                                                                                                                            |

#### Testing tasks

| File            | Description                                                          |
|-----------------|----------------------------------------------------------------------|
| `result-infer*` | Result files of `dp test` on the provided dataset                    |
| `log-infer`     | Logfile of `dp test` evaluation on testing set, expect stdout/stderr |


## Model inspection
You can inspect the available model heads/branches and descriptor parameters using `dp show`, either before
or after usage, if necessary.

```bash
# List available heads/branches (multi-task model)
dp show <model_file> model-branch

# Inspect descriptor parameters
dp show <model_file> descriptor
```

---
# DeePMD-kit python interface (ASE calculator)

Deepmd-kit provides a Python interface, which can act as an ASE calculator, further enabling any calculation task
supported by ASE. A quick example:

```python
from ase import Atoms
from deepmd.calculator import DP

calc = DP(model="<model_file>", head="<some_head>")
water = Atoms(
    "H2O",
    positions=[(0.7601, 1.9270, 1), (1.9575, 1, 1), (1.0, 1.0, 1.0)],
    cell=[100, 100, 100],
    calculator=,
)
print(water.get_potential_energy())
print(water.get_forces())
print(water.get_stress())
```

Here, the `model_file` can be both a `.pt` checkpoint file or a `.pth`/`.pt2` frozen model. Choose head
only for multi-head pretrained models (DPA-2, DPA-3). No need to specify head for models with no head or
fine-tuned model.

Details about this interface is documented in the
[Deepmd-kit documentation (ASE integration)](https://docs.deepmodeling.com/projects/deepmd/en/stable/third-party/ase.html).


---

# DeePMD-kit lammps interface

Deepmd-kit provides a LAMMPS interface, which can be used in LAMMPS simulations. A quick example:

```bash
pair_style deepmd <model_file>
pair_coeff * * <element_1> <element_2> ...
```

Here, the `model_file` must be a `.pth`/`.pt2` frozen model. No checkpoint `.pt` file allowed.
The order of `element_1`, `element_2`, ... must strictly match the atom types used in the
LAMMPS simulation input file and the simulation box. For example, if the lammps simulation box
has `type 1 = H`, `type 2 = O`, then the pair_coeff line should be `pair_coeff * * H O`.

> For DPA-4 models: **`atom_modify map yes` is required at the very beginning of lammps input file.**
> The `.pt2` graph inference relies on an explicit ghost/periodic-image to local-atom map;
> the model fails fast without it!

Multi-GPU (MPI) inference: launch one MPI rank per GPU. An example on a 4-GPU machine:
```bash
CUDA_VISIBLE_DEVICES=0,1,2,3 mpirun -np 4 lmp -in in.lammps
```

Details about this interface is documented in the
[Deepmd-kit documentation (lammps integration)](https://docs.deepmodeling.com/projects/deepmd/en/stable/third-party/lammps-command.html).

---

# Fast Inference techniques

## Freeze to `.pt2` (for DPA-4)

The frozen `.pt2` is an AOTInductor archive used for inference (ASE, LAMMPS).

```
dp --pt freeze -c <model_file> -o frozen
```

The PyTorch backend detects DPA4/SeZM and writes `frozen.pt2`.

> **The `.pt2` is target-specific** — it depends on host CPU/GPU, GPU compute
> capability, and libtorch version. Freeze on the target machine rather than
> reusing a `.pt2` across different hardware.

## Inference precision environment variables

**Precision is fixed at freeze time.** Set these before running `dp --pt freeze`:

| Variable          | Default       | Effect                                                                                                                                                                                                                                                                            |
|-------------------|---------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `DP_TF32_INFER`   | `0` (highest) | float32 matmul precision: `0` highest, `1` high, `2` medium. Keep `0` for MD and PES-smoothness-sensitive workflows.                                                                                                                                                              |
| `DP_TRITON_INFER` | `0`           | Fused Triton inference kernels (CUDA), cumulative: `0` off; `1` universal kernels; `2` adds table-tuned SO(2) value-path kernels; `3` adds fp16 tensor-core mixing GEMMs. Levels `0`–`2` keep full float32 accumulation; `3` gives large speedup with negligible accuracy impact. |

Accepted boolean values: `1`/`true`/`yes`/`on` and `0`/`false`/`no`/`off`.

The script [scripts/deepmd_prepare.py](scripts/deepmd_prepare.py) will automatically freeze with the above defaults after fine-tuning,
when `--freeze` argument is passed.

If not satisfied with the defaults, do not use the resulting `.pt2` file after fine-tuning,
but instead freeze your own model with the desired settings from `model.ckpt.pt`. 

---


## Constraints

**Environment & dependencies:** 
- `dpa4_prepare.py` requires `ase`, `dpdata`, and `numpy` in the local Python environment.

**Data & model:**
- All input structures must be **labelled** (having energy + forces + virial, either by DFT or by a teacher model). 
  Unlabeled structures raise an error during dpdata export.
- Base model for finetuning must be a `.pt` checkpoint file. **Frozen models cannot be fine-tuned.**
- Model variant and input parameters must match exactly — do not mix across variants.
- **`type_map` is fixed to the full periodic table (H–Og).** DPA models uses type embeddings
  indexed by this map; do NOT attempt to change it, and do not restrict it to dataset elements.
- `deepmd/npy` systems are written per chemical formula; use `--mixed_type` for variable
  composition within a single directory (but often not necessary).

**Backend limitations:**
- **No support beyond pytorch implementation**: the tensorflow, jax and paddle-paddle backends are not supported.
- **Check GPU and image compatibility carefully**: as documented in reference
    [refereces/supported_deepmd_models.md](references/supported_deepmd_models.md).
    Choosing wrong GPU or image may lead to unexpected errors.