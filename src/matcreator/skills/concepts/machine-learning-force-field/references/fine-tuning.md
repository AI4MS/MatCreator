# MLFF fine-tuning instructions

When a user asks to generate a MLFF, they often imply fine-tuning a pretrained model, rather than training from scratch,
because the latter is way more computationally expensive. 

When performing fine-tuning, the following procedure is preferred.

## Recommended Procedure — Generate a force field via fine-tuning pretrained-model


### Stage Zero — Ask the user: Do you have a DFT-labelled dataset?

A "DFT-labeled dataset" means structures whose energy, forces, and virial
were computed by DFT (VASP, ABACUS, etc.), **not** by a pretrained machine-learning model.

> **Key principle:** The pretrained model is only a **surrogate for structural-space exploration**
> via molecular dynamics (MD), **not a ground truth**. The target that fine-tuning aims to match must be DFT data.
> All ground-truth labels used for fine-tuning and evaluation must come from **DFT calculations**.

- **Bench mode** (`agent_mode == "bench"`): skip this question — assume NO dataset and
  proceed directly to the "NO dataset" branch below.

- **If the user HAS a DFT-labeled dataset:**
Proceed directly to Stage B below.

- **If the user has NO DFT-labeled dataset:**

Follow Stages A–C below.


### Stage A — Generate candidate structures for labeling via structure exploration

1. **Classify the problem complexity:**
   - **Simple systems** — bulk crystals, random alloys, simple compounds.
   - **Complex systems** — defects, dopants, surfaces, interfaces, transition states,
     high-entropy alloys, amorphous structures, etc. Also treat the system as complex when
     the provided initial structures span **multiple distinct cell types** (e.g. different
     Bravais lattices or coordination environments).

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

   
### Stage B — DFT labeling

Run DFT single-point calculations on the **selected structures** to obtain energy,
force, and virial labels.

- Use the `vasp` or `abacus` skill for DFT input preparation and execution (`vasp` preferred).
- See `concepts/dft-calculation` for guidance on choosing a DFT code.
- Job submission is handled by the `bohrium` skill.


### Stage C — Fine-tuning & Evaluation

> Note: Do NOT reuse any existing workdir. **Always create a fresh workdir**.

1. Create the fresh workdir, and prepare input files in the fine-tuning workdir. For example,
   for DPA models, you may run the script [deepmd/scripts/deepmd_prepare.py](../../../deepmd/scripts/deepmd_prepare.py)
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
     >    with script [deepmd/scripts/deepmd_prepare.py](../../../deepmd/scripts/deepmd_prepare.py), therefore the evaluation
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
