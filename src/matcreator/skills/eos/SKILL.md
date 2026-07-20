---
name: eos
description: Equation-of-State (EOS) benchmark skill — compare DFT, pretrained, and finetuned model E(V) curves to evaluate force-field quality for bulk crystals and simple systems.
metadata:
  tools:
    - run_bash
    - run_python
  dependent_skills:
    - bohrium
    - vasp-pymatgen
    - abacus
    - atomic-structure
    - ase
    - plot
  tags:
    - eos
    - benchmark
    - equation-of-state
    - bulk
---

# EOS Skill

Equation-of-State (EOS) benchmark to evaluate force-field quality for bulk crystals
and simple systems. Compares DFT, pretrained model, and finetuned model E(V) curves.

> **Only for bulk crystals and simple systems.**
> Complex systems (defects, surfaces, etc.) should use `dp test` with a test dataset instead.

---

## Workflow

1. **DFT relaxation** — relax the unit cell to find the ground-state structure.

2. **Generate deformed structures** — create 11 structures with volumes from −5% to +5%
   of the equilibrium volume (uniform spacing).

3. **DFT single-point** — compute energy for all 11 structures.

4. **Model prediction** — predict energies for the same 11 structures using both the
   pretrained model and the finetuned model.

5. **Compare** — plot E(V) curves: DFT (ground truth) vs pretrained vs finetuned.

---

## Integration with DPA4 finetuning

When running DPA4 finetuning for a simple system without a DFT-labelled dataset,
the EOS benchmark can be used as an auxiliary evaluation alongside the primary
diagonal parity plots:

- DFT relaxation and single-point calculations can run **in parallel** with the
  main DPA4 dataset DFT labeling to save time.
- Submit DFT jobs via the `bohrium` skill for the EOS deformed structures.
