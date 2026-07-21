---
name: machine-learning-force-field
description: Concept skill for Machine Learning Force Fields (MLFFs). Describes what MLFFs are, the distinction between training and inference, and which tool skills to use. Load this before selecting a specific MLFF framework (DeePMD, MatterSim, etc.).
metadata:
  dependent_skills:
    - deepmd
    - mattersim
    - ase-deepmd
  tags:
    - MLFF
    - machine-learned-force-fields
    - deep-potential
    - force-field
    - training
---

# Machine Learning Force Field (MLFF)

A Machine Learning Force Field (MLFF) is a surrogate model trained on DFT reference data that predicts atomic energies and forces at a fraction of the computational cost. MLFFs enable large-scale and long-timescale molecular dynamics simulations that would be prohibitively expensive with DFT directly.

## Workflow Phases

| Phase                     | Description                                                                      |
|---------------------------|----------------------------------------------------------------------------------|
| **Pre-trained model**     | A general-purpose model trained on large, diverse datasets. Try this first.      |
| **Fine-tuning**           | Adapts a pre-trained model to a target system using domain-specific DFT data.    |
| **Training from scratch** | Used in distillation: train a lightweight student model on teacher-labeled data. |
| **Inference / MD**        | Deploy the trained model for structure relaxation or molecular dynamics.         |

## When to Use

- Run MD exploration to sample the configuration space of a new material.
- Relax generated structures before DFT validation.
- Screen large candidate sets efficiently before expensive DFT runs.
- Replace DFT in high-throughput workflows after validating accuracy.

## Available Tool Skills

| Skill        | Framework          | Best For                                                     |
|--------------|--------------------|--------------------------------------------------------------|
| `deepmd`     | DeePMD-kit         | Training and fine-tuning DPA-1/DPA-2/DPA-3/DPA-4 models; PFD workflow    |
| `mattersim`  | MatterSim          | Pre-trained universal MLFF; structure relaxation and MD      |
| `ase-deepmd` | ASE + DeePMD       | Running MD with a trained DeePMD model via ASE interface     |

Load the appropriate tool skill for detailed instructions (e.g., `load_skill("deepmd")`).

## Choosing a Framework

- Use `deepmd` when you need to **train or fine-tune** a DPA-1/DPA-2/DPA-3/DPA-4 model on your own DFT dataset.
- Use `mattersim` when you want a **pre-trained universal model** without retraining.
- Use `ase-deepmd` when you need to run **MD simulations** with a trained DeePMD potential.


# MLFF generation instructions

When a user asks to generate a MLFF, the following fine-tuning procedure is preferred.

## Recommended Procedure — Generate a force field via fine-tuning pretrained-model

> **Key principle:** The pretrained model is only a **surrogate for structural-space exploration**
> via molecular dynamics (MD), **not a ground truth**. All ground-truth labels used for fine-tuning 
> and evaluation must come from **DFT calculations**.

### Phase Zero — Ask the user: Do you have a DFT-labelled dataset?

A "DFT-labeled dataset" means structures whose energy, forces, and virial
were computed by DFT (VASP, ABACUS, etc.), **not** by a pretrained machine-learning model.

- **Bench mode** (`agent_mode == "bench"`): skip this question — assume NO dataset and
  proceed directly to the "NO dataset" path below.

**If the user HAS a DFT-labeled dataset:**
Proceed directly to Phase B below.

**If the user has NO DFT-labeled dataset:**

Follow Phases A–C below.


### Phase A — Generate candidate structures via structure exploration

1. **Classify the system:**
   - **Simple systems** — bulk crystals, random alloys, simple compounds.
   - **Complex systems** — defects, dopants, surfaces, interfaces, transition states,
     high-entropy alloys, amorphous structures, etc.

2. **For complex systems: ask the user if they already have structure files.**
   If yes, use the user's structures as the starting point. If no, generate them
   using the `atomic-structure` skill (or `matcraft-kit` for surfaces/defects).

3. **Generate candidate structures** for MD exploration:
   - Use the pretrained model to explore configuration space via **NPT-ensamble MD**.
   - Use the `atomic-structure` skill to build make supercells of structures as
     starting points of MD.

   > **Rules for judging MD simulation cell size:**
   > Keep each DFT structure at roughly **50 atoms** when possible.
   > For systems exceeding this size,
   > do NOT supercell — use the original cell as-is.

  - Use `ase-deepmd` skill to perform structure optimization with the pretrained model,
    before running MD. Both the atomic coordinates and the lattice vectors should be optimized.
    This helps prevent the MD simulation from collapsing due to unreasonable initial structures.
  - **MD sampling skill priority:** `ase-deepmd` > `lammps`. Try `ase-deepmd` first;
    if it fails repeatedly, switch to `lammps`. Never use `atomic-structure` for MD.

4. **MD sampling parameters (NPT ensemble):**
 
   | Parameter     | Default value           | Description                                                                                                                                |
   |---------------|-------------------------|--------------------------------------------------------------------------------------------------------------------------------------------|
   | Ensemble      | **NPT**                 | NPT ensemble is mandatory for structure exploration                                                                                        |
   | Temperature   | **300 K, 600 K, 900 K** | Target temperatures. Use 300K, 600K, 900K as default. Adjust to user needs. For solid-state materials, **never exceed the melting point**! |                                     |
   | Pressure      | **1 bar, 1 Gpa**        | Target pressure. For regular conditions, try from 1 bar and 1 GPa; adjust to user needs.                                                   |
   | Step size     | **2 fs**                | Highest safe step size, decrease to 1 fs above 2000 K or when unstable (volume explosion)                                                  |
   | Duration      | **10 ps**               | Total simulation time per temperature and per pressure                                                                                     |
   | Output frames | **100**                 | Number of MD frames to retain from all temperatures and pressure samples. 100 is default. For more complex systems, use up to 500.         |

    > Output frames recommendation:
    > - **100** for simple systems (bulk crystals, random alloys, simple compounds)
    > - **200** for complex systems (defects, dopants, surfaces, interfaces, transition states, etc.)
    > - **500** for very complex systems (e.g., high-entropy alloys, amorphous structures, etc.)

5. **Entropy-based structure selection (MANDATORY)**
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
   > For DPA models, the evaluation of both the pretrained and fine-tuned models are already taken care of
   > by the commands generated
   > with script [deepmd/scripts/deepmd_prepare.py](deepmd/scripts/deepmd_prepare.py), therefore the evaluation
   > results will come back together with the fine-tuned model.

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


## Constraints

- When sampling data in order to construct a training set, MUST use **NPT ensemble**. 
  Never switch to NVT/NVE without explicit user approval as they often lack diversity in strain variation.
  When NPT simulation fails, you must attempt to fix the simulation code, rather than switching to NVT/NVE
  as detours.
- **Entropy-based structure selection is MANDATORY** before DFT labeling.
- **Structure size:** ~50 atoms/structure. Large systems must NOT be extended into supercells.
- **Evaluation always compares pretrained vs finetuned**.
