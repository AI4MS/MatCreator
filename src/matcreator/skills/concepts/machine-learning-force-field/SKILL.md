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

# MLFF workflows

The detailed, stage-by-stage procedures for generating a force field live in two dedicated reference files.
**Load the relevant reference before executing either workflow:**

| Workflow | When to use | Reference |
|----------|-------------|-----------|
| **Fine-tuning** | Fine-tune a pre-trained model on DFT-labeled data of a target system (the common case). | [references/fine-tuning.md](references/fine-tuning.md) |
| **Distillation** | Train a smaller DPA-4c student model from scratch on teacher-labeled data, for large-scale / long-timescale production MD. | [references/distillation.md](references/distillation.md) |

Both procedures follow the same three-stage shape — **Stage A** (candidate structure generation via NPT MD),
**Stage B** (labeling: DFT for fine-tuning, teacher inference for distillation) and **Stage C** (training +
evaluation). Distillation additionally requires a valid **teacher model** (a fine-tuned, single-task model on
the target system — **never** a pretrained model) and a much larger dataset (~100× the fine-tuning scale).

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
