---
name: machine-learning-force-field
description: Concept skill for Machine Learning Force Fields (MLFFs). Describes what MLFFs are, the distinction between fine-tuning and distillation, and which tool skills to use. Load this before selecting a specific MLFF framework (DeePMD, MatterSim, etc.).
metadata:
  dependent_skills:
    - deepmd
    - mattersim
    - ase
    - lammps
  tags:
    - MLFF
    - machine-learned-force-fields
    - deep-potential
    - force-field
    - training
---

# Machine Learning Force Field (MLFF)

A Machine Learning Force Field (MLFF) is a surrogate model trained on DFT reference data that predicts atomic energies and forces at a fraction of the computational cost.
MLFFs enable large-scale and long-timescale molecular dynamics simulations that would be prohibitively expensive with DFT directly.

## MLFF-related tasks

| Task name                 | Description                                                                                                   |
|---------------------------|---------------------------------------------------------------------------------------------------------------|
| **Fine-tuning**           | Fine-tune a pre-trained model to a target system using specific DFT-labeled data in that system.              |
| **Training from scratch** | Used only in distillation, i.e., train a lightweight student model on data labeled by a larger teacher model. |
| **Inference / MD**        | Deploy the MLFF model for structure relaxation or molecular dynamics.                                         |

## When to Use

- Fine-tuning a pre-trained model is the most common use case.
- Distillation is only used when the pre-trained or fine-tuned model is too large to use in productive MD simulations.
- Act as the energy, force and stress provider for the majority of atomistic property calculations
   (after confirmed accuracy on DFT-labeled testing set.)


## Related Skills

| Skill       | Use case of related skills                                                                                                 |
|-------------|----------------------------------------------------------------------------------------------------------------------------|
| `deepmd`    | Fine-tuning, evaluating and inferencing with all deepmd models (including DPA-1, DPA-2, DPA-3 and DPA-4)                   |
| `mattersim` | Fine-tuning, evaluating and inferencing with the Mattersim model                                                           |
| `ase`       | Running MD with a pre-trained or fine-tuned MLFF model via ASE interface to yield samples; or light productive simulations |
| `lammps`    | Running MD with a frozen deepmd model via LAMMPS, only for heavy, large-scale simulations                                  |

Load the appropriate tool skill when needing detailed instructions (e.g., `load_skill("deepmd")`).

> **Note:** The `deepmd` skill is the recommended over `mattersim` for most use cases as deepmd models provide better accuracy.

---

# MLFF fine-tuning instructions

When a user asks to generate a MLFF, they often imply fine-tuning a pretrained model, rather than training from scratch,
because the latter is way more computationally expensive. 

When performing fine-tuning, the following procedure is preferred.

## Recommended Procedure — Generate a force field via fine-tuning pretrained-model


### Phase Zero — Ask the user: Do you have a DFT-labelled dataset?

A "DFT-labeled dataset" means structures whose energy, forces, and virial
were computed by DFT (VASP, ABACUS, etc.), **not** by a pretrained machine-learning model.

> **Key principle:** The pretrained model is only a **surrogate for structural-space exploration**
> via molecular dynamics (MD), **not a ground truth**. The target that fine-tuning aims to match must be DFT data.
> All ground-truth labels used for fine-tuning and evaluation must come from **DFT calculations**.

- **Bench mode** (`agent_mode == "bench"`): skip this question — assume NO dataset and
  proceed directly to the "NO dataset" branch below.

- **If the user HAS a DFT-labeled dataset:**
Proceed directly to Phase B below.

- **If the user has NO DFT-labeled dataset:**

Follow Phases A–C below.


### Phase A — Generate candidate structures for labeling via structure exploration

1. **Classify the problem complexity:**
   - **Simple systems** — bulk crystals, random alloys, simple compounds.
   - **Complex systems** — defects, dopants, surfaces, interfaces, transition states,
     high-entropy alloys, amorphous structures, etc.

2. **For simple systems:** proceed directly to step 4 below.

3. **For complex systems: ask the user if they already have initial structure files.**
   If yes, use the user's structures as the starting point. If no, generate an intial structure
   (or multiple initial structures, if needed) using the `atomic-structure` skill
   (or `matcraft-kit` for surfaces/defects).

4. **Choose simulation cell size for MD**: According to the following rules, determine whether the
   initial structures need to be replicated into supercells. Do supercell operations only if needed,
   and perform it only **ONCE** in the entire workflow.

   > **Rules for judging MD simulation cell size:**
   > Keep each structure at roughly **50 atoms** when possible.
   > For systems exceeding this size,
   > do NOT perform supercell operations — use the original cell as-is.

5. **Generate candidate structures** for MD exploration:
   - Refer to the `ase` skill for details of using ASE.
   - Use the resulting structure (or structures) from step 4 as the starting simulation cell (ase.Atoms).
   - Use the pretrained model to set the simulation cell's calculator.
   - Relax the structure (optimize both atomic coordinates and lattice vectors) first to avoid MD collapse.
   - Explore configuration space via **NPT-ensamble MD**.
   - **MD sampling skill choice:** `ase` >> `lammps`. Try `ase` first;
    if it fails repeatedly, switch to `lammps`. Never use `atomic-structure` for MD.

   - **MD sampling parameters (NPT ensemble):**
   
    Adjust the following parameters according to the table below and the specific needs of the system.

   | Parameter                 | Default value           | Description                                                                                                                                             |
   |---------------------------|-------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------|
   | Ensemble                  | **NPT**                 | NPT ensemble is mandatory for structure exploration                                                                                                     |
   | Temperature               | **300 K, 600 K, 900 K** | Target temperatures. Use 300K, 600K, 900K as default. Adjust to user needs. For solid-state materials, **approach but never exceed the melting point**! |                                     |
   | Pressure                  | **1 bar, 10 Gpa**       | Target pressure. For regular conditions, try from 1 bar and 10 GPa; adjust to user needs.                                                               |
   | Step size                 | **2 fs**                | Highest safe step size, decrease to 1 fs above 2000 K or when unstable (volume explosion)                                                               |
   | Structure saving interval | Every **5** steps       | Recommend to have at least **10 fs** spacing between two saved frames to have enough variation between structures.                                      |
   | Duration                  | **10 ps**               | Total simulation time per temperature and per pressure                                                                                                  |
   | Output frames             | **100**                 | Number of MD frames to retain from all temperatures and pressure samples. 100 is default. For more complex systems, use up to 500.                      |

    > Output frames recommendation:
    > - **100** for simple systems (bulk crystals, random alloys, simple compounds)
    > - **200** for complex systems (defects, dopants, surfaces, interfaces, transition states, etc.)
    > - **500** for very complex systems (e.g., high-entropy alloys, amorphous structures, etc.)

6. **Entropy-based structure selection (MANDATORY)**
   After MD sampling, use entropy-based filtering to select a subset of 50% of the structures **with diversity**
   from the obtained MD frames before DFT labeling to reduce DFT cost. For example:
   ```
   run_skill_script(
       skill_name="quests",
       script_name="active_learning.py",
       args="filter-by-entropy md_trajectory.extxyz --max-sel 50 --chunk-size 10"
   )
   ```
   `chunk-size` had better be 1/50 of the total number of MD frames, but never below 10.

   > **CRITICAL:** Always run entropy-based selection BEFORE DFT labeling. Never send
   > all sampled frames directly to DFT — use the selected structures instead.

   
### Phase B — DFT labeling

Run DFT single-point calculations on the **selected structures** to obtain energy,
force, and virial labels.

- Use the `vasp` or `abacus` skill for DFT input preparation and execution (`vasp` preferred).
- See `concepts/dft-calculation` for guidance on choosing a DFT code.
- Job submission is handled by the `bohrium` skill.


### Phase C — Fine-tuning & Evaluation

> Note: Do NOT reuse any existing workdir. **Always create a fresh workdir**.

1. Create the fresh workdir, and prepare input files in the fine-tuning workdir. For example,
   for DPA models, you may run the script [deepmd/scripts/deepmd_prepare.py](deepmd/scripts/deepmd_prepare.py)
   under the `deepmd` skill. 
   In this preparation stage, train/test split is performed. 
   Recommended train vs test split ratio is **4:1** for all DFT-labeled frames.

2. Submit finetune job on Bohrium via the `bohrium` skill .

   3. **Evaluate:**
      Perform testing to obtain predicted energy (and per-atom energy), forces, virials (and per-atom virials) or
      stress, then compute MAE errors. Also, perform such evaluation with the original pretrained model for comparison
      with the fine-tuned model. 
    >    For **DPA models**, the evaluation of both the pretrained and fine-tuned models are already taken care of
    >    by the commands generated
    >    with script [deepmd/scripts/deepmd_prepare.py](deepmd/scripts/deepmd_prepare.py), therefore the evaluation
    >    results will come back together with the fine-tuned model.
   
    > For **other MLFF models**, you may need to manually run the evaluation through the MLFF's native ase calculator interface.
    > Refer to `ase` skill for guidance.

4. When the system of your study used very different first-principle computation settings from the training set
   of your pretrained model, energy MAE may not be comparable between the pretrained and fine-tuned models as
   the zero point of energy may be different. In this case, you may need to adjust the energy bias of the pretrained
   model for rational comparison. You may perform a quick adjustment like the following:
   ```python
      e_shift = np.mean(all_e_peratom_dft - all_e_peratom_predicted)
   ```
   Then do:
   ```python
            get_mae(
                all_e_peratom_dft, 
                all_e_peratom_predicted + e_shift
            ),
   ```
   to get comparable energy MAE.

5. **Report and compare the results:**
     - Pretrained: energy per atom MAE = X, force MAE = Y
     - Finetuned: energy per atom MAE = X', force MAE = Y'
     - Improvement: energy per atom MAE reduced by Z%, force MAE reduced by W%

---

# MLFF distillation instructions

Distillation is the process of training a smaller MLFF model **from scratch** using labels from a larger MLFF model.
The smaller MLFF model is called the **student model**, and the larger MLFF model is called the **teacher model**.

MLFF must be appropriately distilled before applying the MLFF model to a large-scale simulation (> 100 K atoms, > 1 ns).

The distillation workflow mirrors the fine-tuning workflow phase by phase, except that the labels are
generated by the teacher model instead of DFT, and the student model is trained from scratch on a much
larger dataset. The concept-level procedure below is the direct counterpart of the fine-tuning procedure above.

## Recommended Procedure — Generate a force field via distillation

> **Strict phase ordering — Phase A → Phase B → Phase C.**
> These three phases must be executed **in order, with no phase skipped, no phase
> reordered, and no phase omitted because it "seems unnecessary" for the structure at
> hand.** The only allowed variation is tuning *parameters within each phase*
> (temperature, pressure, frame count, training steps, etc.); the phase *sequence and
> presence* are fixed.
>
> **If you believe a phase can be skipped or shortened for your particular structure,
> that belief is precisely the error this rule exists to prevent.** Skipping Phase A
> (teacher MD) or Phase B (proper labeling) and jumping straight to Phase C (student
> training) produces a student that reproduces the teacher on MD-sampled frames but
> degrades by an order of magnitude on DFT-relaxed structures — the classic
> distillation failure mode.
### Phase Zero — Gate: Is a valid teacher model available?

A valid **teacher model** must satisfy BOTH conditions:

1. It is a **fine-tuned** model on the target system, i.e., it has already gone through the
   fine-tuning procedure above on DFT-labeled data of that system.
2. It is a **single-task** model (dedicated to the target system).

- **If no valid teacher exists** (the user only has a pretrained model, or only a multi-task model):
  do NOT proceed with distillation — a pretrained model must **NEVER** be used as a teacher.
  Return to the fine-tuning workflow above first, produce a fine-tuned single-task model for the
  target system, and only then come back to distillation.
- **Student model:** the student is restricted to **DPA-4c**.

> All DPA-4c-specific CLI commands, scripts and environment setup are owned by the `deepmd` skill.
> Load the `deepmd` skill when executing any phase below; this concept file deliberately does not
> repeat concrete commands.

### Phase A — Teacher MD exploration (candidate structure generation)

Generate candidate structures using the **fine-tuned teacher model** as the MD calculator,
**reusing the fine-tuning Phase A sampling rules**:

- Same structure preparation and cell-size rules (~50 atoms per structure; supercell replication at
  most once and never for large systems).
- Explore configuration space via **NPT-ensemble MD** with the same parameter conventions as
  fine-tuning (NPT ensemble is mandatory; same temperature/pressure/step-size/saving-interval rules).
- **Entropy-based structure selection is MANDATORY** after MD sampling and before labeling,
  exactly as in fine-tuning Phase A.

The only systematic difference is the sampling scale: because the student is trained from scratch,
distillation needs roughly **20 times** the number of frames used in fine-tuning.

> Output frames recommendation (distillation):
> - **20000** for simple systems (bulk crystals, random alloys, simple compounds)
> - **40000** for complex systems (defects, dopants, surfaces, interfaces, transition states, etc.)
> - **100000** for very complex systems (e.g., high-entropy alloys, amorphous structures, etc.)

> **Seed ≠ training set.** The POSCAR / cif structure you start with is a **seed** for
> MD exploration, not a training frame. You MUST first run teacher **NPT-ensemble MD**
> on the seed (Phase A) to generate diverse configurations, then label those
> configurations with the teacher (Phase B), and only then train the student on the
> labeled MD-sampled frames (Phase C). **Directly labeling the static seed structure
> (or a handful of manually-built structures) and training on it is a typical
> distillation failure and is strictly forbidden.**
>
> Recall the three non-negotiable Phase A rules that the seed-based workflow depends on:
> **(1) NPT ensemble** — mandatory for strain diversity; never switch to NVT/NVE without
> explicit user approval. **(2) Entropy-based structure selection** — mandatory after
> MD sampling and before labeling. **(3) ~100× frame scale** — distillation needs
> roughly 100× the frames of fine-tuning (≥ 20000 for simple systems).

### Phase B — Teacher inference labeling (replaces DFT)

Label the entropy-selected structures by **teacher model inference** (single-point energy, force and
virial predictions) instead of DFT:

- The teacher's predictions fully replace DFT single-point calculations in this phase; no DFT jobs
  are launched.
- The teacher is guaranteed to be a fine-tuned **single-task** model by the Phase Zero gate, so there
  is **no model head selection** involved — run inference with the teacher model as-is.
- MD/inference skill choice follows the same rules as fine-tuning (`ase` first, `lammps` as fallback).

### Phase C — Student training from scratch & Evaluation

> Note: Do NOT reuse any existing workdir. **Always create a fresh workdir**, as in fine-tuning.

> **GATE — verify training-set provenance and size before proceeding to student
> training.** Before step 1 below, confirm ALL of the following:
> 1. The training frames were **produced by Phase A → Phase B** (teacher NPT MD
>    sampling + entropy selection + teacher inference labeling), **not** statically
>    labeled from the seed structure.
> 2. The total labeled frame count is **≥ ~20000** (simple systems) / **≥ ~40000**
>    (complex systems) / **≥ ~100000** (very complex systems), consistent with the
>    Phase A output recommendation above.
> 3. The frames span a **diverse configuration space** (different cell shapes, strains,
>    and atomic positions from the NPT trajectory), not a single relaxed geometry.
>
> **If any check fails, STOP and return to Phase A** — regenerate/expand the MD
> trajectory and re-label. Do not proceed to student training on insufficient or
> non-MD-sourced data.

1. Prepare the teacher-labeled data in a fresh workdir. The data preparation and **domain split must
   follow exactly the same rules as fine-tuning Phase C**: train vs test split ratio is **4:1** for
   all teacher-labeled frames.

2. Train the DPA-4c student model **from scratch** (no pretrained initialization). Because the student
   starts from scratch, training is much longer than fine-tuning: use about **1,000,000 (1M) training
   steps**. For the concrete DPA-4c training commands and environment, load and follow the `deepmd` skill.

3. **Evaluate on two levels (both are required):**
   - **Level 1 — student vs teacher:** compare the student's predictions against the teacher's labels
     on the held-out test set (the 1/5 test split from step 1). This checks that the student faithfully
     reproduces the teacher.
   - **Level 2 — student vs DFT ground truth:** compare the student's predictions against DFT.
     **Prefer reusing the DFT-labeled test set from the corresponding fine-tuning workflow.** If that
     is not available, choose an appropriate subset of the distillation testing set and re-compute it
     with DFT (subset number of frames times number of atoms in each frame should not exceed 10000
     total atoms).

4. Distillation is a **single-round** procedure: train the student once and evaluate. Do NOT iterate
   (no repeated teacher-relabeling rounds) within this workflow.

---

# MLFF inference instructions

Inference means to use MLFF to calculate the energy, force, and stress of given structures.

MLFFs can be applied as fast calculators, and further be used to calculate and simulate
any atomistic properties unrelated to electronic structure.

Most MLFFs support ASE calculator interface, which can be used to perform any type of calculations that
the `ase` skill support, such as MD simulations, structure optimization, and so on. See the `ase` skill for
details.

Currently, only deepmd models are bundled with support to LAMMPS. A deepmd model must be **frozen** before
being used in a LAMMPS simulation input file. See the `deepmd` skill and references for details.

> In deepmd models, LAMMPS often outperforms ASE in terms of simulation speed. When performing large-scale
> simulations, it is recommended to use `lammps` over `ase`.


# Constraints

- When sampling data in order to construct a training set, MUST use **NPT ensemble**. 
  Never switch to NVT/NVE without explicit user approval as they often lack diversity in strain variation.
  When NPT simulation fails, you must attempt to fix the simulation code, rather than switching to NVT/NVE
  as detours.
- **Entropy-based structure selection is MANDATORY** before DFT labeling.
- **Structure size:** ~50 atoms/structure. Large systems must NOT be extended into supercells.
- **Evaluation always compares pretrained vs finetuned**.
