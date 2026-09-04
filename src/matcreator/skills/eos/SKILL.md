---
name: eos
description: Equation-of-State (EOS) skill — compute E(V) curves with Birch-Murnaghan fitting and benchmark DFT, pretrained, and finetuned model E(V) curves to evaluate force-field quality for bulk crystals and simple systems.
metadata:
  tools:
    - run_bash
    - run_python
  dependent_skills:
    - ase
    - deepmd
    - mattersim
    - bohrium
    - vasp-pymatgen
    - abacus
    - atomic-structure
    - plot
    - concepts/machine-learning-force-field
    - concepts/dft-calculation
  tags:
    - eos
    - benchmark
    - equation-of-state
    - bulk
---

# EOS Skill

Compute the equation of states (energy-volume curve) for bulk crystals and simple
systems, and benchmark force-field quality by comparing DFT, pretrained, and
finetuned model E(V) curves.

> **Only for bulk crystals and simple systems.**
> Complex systems (defects, surfaces, etc.) should use `dp test` with a test dataset instead.

---

## Workflow

1. **Relaxation** — relax the unit cell to find the ground-state structure.

2. **Generate deformed structures** — create 11 structures with volumes from −5% to +5%
   of the equilibrium volume (uniform spacing).

3. **Single-point calculations** — compute the energy for all 11 structures.

4. **Equation-of-states fit** — fit the energy-volume data to the third-order
   Birch-Murnaghan equation of states:

   ```text
   E(V) = E0 + (9 V0 B0 / 16) * { [ (V0/V)^(2/3) − 1 ]^3 * B0'
                                  + [ (V0/V)^(2/3) − 1 ]^2 * [ 6 − 4 (V0/V)^(2/3) ] }
   ```

   where `E0` is the equilibrium energy, `V0` the equilibrium volume, `B0` the
   bulk modulus, and `B0'` its pressure derivative. The fit yields `E0`, `V0`,
   `B0`, and `B0'` for each energy source (DFT, pretrained, finetuned).

5. **Model prediction** — predict energies for the same 11 structures using both the
   pretrained model and the finetuned model.

6. **Compare** — plot E(V) curves: DFT (ground truth) vs pretrained vs finetuned.

When calculating energies, prefer machine-learning force fields (MLFF) over DFT;
use DFT only when a ground-truth reference is required for benchmarking.

---

## Integration with DPA4 finetuning

When running DPA4 finetuning for a simple system without a DFT-labelled dataset,
the EOS benchmark can be used as an auxiliary evaluation alongside the primary
diagonal parity plots:

- DFT relaxation and single-point calculations can run **in parallel** with the
  main DPA4 dataset DFT labeling to save time.
- Submit DFT jobs via the `bohrium` skill for the EOS deformed structures.
