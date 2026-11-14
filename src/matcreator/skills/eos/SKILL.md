---
name: eos
description: Equation-of-State (EOS) skill — compute E(V) curves with Birch-Murnaghan fitting and benchmark DFT, pretrained, and finetuned model E(V) curves to evaluate force-field quality for bulk crystals and simple systems.
metadata:
  tools:
    - run_bash
    - run_python
    - submit_eos
    - collect_eos
    - resolve_eos_member
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

## Tracked VASP EOS

When the user requests VASP, prepare the initial cell relaxation with
`MPRelaxSet` and a static template with `MPStaticSet` using the same composition
and site order. Use the desired shared SCF settings for every volume. Prepare
both for a fresh start without restart inputs. The static template POSCAR only
checks site order. The execution tool derives each point's POSCAR by isotropically
scaling the lattice of the verified relaxation and retaining fractional positions.

Use the image, machine and job submission authorization in the
`vasp-pymatgen` Batch Job reference. State the maximum job count (one
relaxation plus the requested static points), machine and estimated core-hours.
Call `submit_eos` with both prepared directories and remember its EOS ID. Use
`collect_eos` on status updates and user follow-ups. On a human turn after
authorization, pass `advance=true` to submit unmatched volume points once the
relaxation has been verified. A status notification only collects existing jobs.

Report the relaxation or point ID for every exact match. On an explicit later
choice, use `resolve_eos_member` for that member. When all available results
have been collected, deliver the CSV, curve and member manifest. Report
missing points and any archive failure. Fit Birch–Murnaghan only if the
sampling is complete and the fit can be verified.

## Workflow for model benchmarking

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
- Use the tracked VASP EOS route when the DFT reference uses VASP.
