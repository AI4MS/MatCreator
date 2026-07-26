---
name: equation-of-states
description: Skill for computing equation of states (energy-volume curve).
metadata:
  dependent_skills:
    - ase
    - deepmd
    - mattersim
    - concepts/machine-learning-force-field
    - concepts/dft-calculation
  tags:
    - eos
---

Use the following procedure:

1. **Relaxation** — relax the unit cell to find the ground-state structure.
2. **Generate deformed structures** — create 11 structures with volumes from −5% to +5%
   from the equilibrium volume (uniform spacing).
3. **Single-point** — compute the energy for all 11 structures.
4. **Equation of states** — fit the energy-volume data to the Birch-Murnaghan equation of states.

When calculating energies, prefer machine-learning force fields (MLFF) over DFT.


