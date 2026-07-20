---
name: ase
description: Skill for running ASE calculations, including energy/force/stress evaluation, molecular dynamics (MD) and structure optimization. Now only use machine-learned force fields (MLFFs) as calculators.
metadata:
  tools:
    - run_bash
    - run_python_file
  dependent_skills:
    - bohrium
    - concepts/machine-learning-force-field
  tags:
    - ase
    - calculator
    - md
    - molecular-dynamics
    - structure-optimization
---

# ASE Skill

ASE is a Python module that can set up and perform energy/force/stress calculations, molecular dynamics (MD) and structure optimization calculations.

## When to Use

- Run MD exploration to sample the configuration space of a new material (for generation of a training/tesing set).
- Relax generated structures before DFT validation.
- Screen large candidate sets efficiently before expensive DFT runs.
- Replace DFT in high-throughput workflows after validating accuracy against DFT.
- Calculating any atomistic properties that can be derived or simulated from the energy, force and stress
   without knowledge of the electronic structure 

> **Notice:** only for relatively small-scale calculations, < 100K atoms, and simulation time < 1 ns.
> For large-scale calculations, prefer `deepmd` skill and `lammps` skill.


## Related Skills

| Skill                                   | Use case of related skills                                              |
|-----------------------------------------|-------------------------------------------------------------------------|
| `bohrium`                               | For submitting to calculation to Bohrium Cloud.                         |
| `concepts/machine-learning-force-field` | When using machine-learning force fields (MLFFs) as the ASE calculator. |

Load the appropriate tool skill when needing detailed instructions (e.g., `load_skill("bohrium")`).

> If the local environment has no GPU, `torch.cuda.is_avaible()` is False, then submitting to bohrium is compulsory.

---
## General working pipeline with ASE

1. Read the input structure file for all the structures contained. Example:
```python
from ase.io import read

all_atoms = read("structures.extxyz", index=":")
```
ASE supports reading and writing many file formats, including `extxyz`, `cif`, `poscar`, `lammps-data`, `pdb`, etc. `extxyz`
is the most recommended modern read and write format for structural data, as it allows easy storage of atomic coordinates,
cell matrix, energy, force, stress and any other properties via intuitive textual format.

  > **Note:** `index=":"` represents reading all structures in the file to a list of `ase.Atoms`. Without this argument,
  > will only read the first structure in the file and return a single `ase.Atoms` object.

2. Set up the ASE calculator:
```python
from deepmd.calculator import DP

calc = DP(model="<model_file>")
atoms.calc = calc
```
Here we use the `deepmd` calculator as an example. For other MLFFs, please refer to the corresponding skill and documentation.

> **Notice:** the calculator must be set before any calculation can be performed!

3. Initializing calculation conditions, including logs (not required by energy/force/stress evaluations):

Refer to section `Structure relaxation pipeline` and `MD Pipeline` for details.`

4. Perform the calculation:

**For relaxation or MD**:

Usually just do:
```python
dyn.run()
```

For energy/forces/stress evaluations, just do:
```python
energy = atoms.get_potential_energy()
forces = atoms.get_forces()
stress = atoms.get_stress(voigt=True)
```
Then the energy, forces and stress will be stored in the `atoms` object, and will be written when saving
these `atoms` objects into files as long as you write `extxyz` format. As a result, if you read a previously
calculated structure file, the energy, forces and stress will be automatically loaded into the `atoms` object,
attached to an abstract `SinglePointCalculator` class, and can be accessed again by the `get_<some_property>`
methods above.

> **Notice:** in `get_stress`, the `voigt=True` argument is required to get the stress in Voigt notation,
> which is the default format for stress output in ASE. The voigt notation: (vxx, vyy, vzz, vyx, vxz, vyz).
> Set voigt=False to get the full, 3-by-3 stress tensor.

5. Save the results:

Use `ase.io.write` to save the results. Example:
```python
from ase.io import write

write("results.extxyz", atoms)
```

Relaxation trajectory and MD simulation trajectory are often specified and saved during the simulation into `.traj` files,
therefore no need for extra saving step. (See `MD Pipeline` section)

6. Preparation and running:
- Write a python script containing all the steps above.
- Create a **fresh working directory**. DO NOT use any existing directory!
- Copy the input structure file and the model file to the working directory, and run the script. For remote submission,
  refer to the `bohrium` skill, and the corresponding skill of the MLFF, such as `deepmd`.

7. Post-processing:
- Check the log file to see if the calculation is successful.
- Check the output files.

For reading output structures, use `ase.io.read`. Example:
```python
from ase.io import read

atoms = read("results.extxyz", index=":")
```

For reading output trajectory, use `ase.io.Trajectory`. Example:
```python
from ase.io import Trajectory

traj = Trajectory("results.traj")
for atoms in traj:
    print(atoms.get_potential_energy())
```
while `Trajectory` can be used as a list of `ase.Atoms`.

## Structure relaxation pipeline

1. After setting up the calculator, must set the cell filter to indicate relaxing cell parameters together with
atomic coordinates. The recommended filter is FrechetCellFilter. Always add filter to allow relaxation of cell
parameters, unless in very specific situations, such as when you want to fix the cell parameters during phonon
calculations under varied volumes, or when calculating the stress-strain relationship, etc.
2. Use FIRE optimizer for coarse relaxation, then LBFGS optimizer for fine relaxation. The final convergence criteria
is usually set to 0.02 eV/A for forces. For most applications, this is sufficient. Writing trajectory is optional, and
actually not recommended if relaxation is very long, but writing logfile is recommended.
3. After relaxation, must save the relaxed structure to a file for future read.

An example script of this pipeline:
```python
from ase.optimize import FIRE, LBFGS
from ase.io import read, write
from ase.filters import FrechetCellFilter

atoms = read("input.extxyz")
calc = ... # initialize calculator
atoms.calc = calc

# Set cell filter to allow relaxation of cell parameters together with atomic coordinates.
filt = FrechetCellFilter(atoms)

# Preset number of steps each stage. Default values, adjust to your needs, but do NOT exceed 3000 steps in total.
n_steps_fire = 500
n_steps_lbfgs = 1000

# Coarse relax with FIRE optimizer first. Saving trajectory is optional, saving logfile is recommended.
dyn = FIRE(filt, trajectory="relaxation_fire.traj", logfile="relaxation_fire.log")
dyn.run(steps=n_steps_fire, fmax=0.06)

# Fine relax with LBFGS optimizer. Saving trajectory is optional, saving logfile is recommended.
dyn = LBFGS(filt, trajectory="relaxation_lbfgs.traj", logfile="relaxation_lbfgs.log")
dyn.run(steps=n_steps_lbfgs, fmax=0.02)

# MUST save the relaxed structure to a file.
write("relaxed.extxyz", atoms)
```

For bohrium submission:
- **Required forward files:** input.extxyz, script.py, and the corresponding model file
- **Required backward files:** relaxed.extxyz, *.log, *.traj


## MD Pipeline
1. Always perform a relaxation before MD to prevent structure collapse.
2. When running NPT simulation, IsotropicMTKNPT is recommended. For NVT simulation,
   use `ase.md.nose_hoover_chain.NoseHooverChainNVT`.
3. Attach trajectory write actions and monitor actions to the Ensemble dynamics object via `dyn.attach()` method,
   where the interval of performing write actions can be specified via setting keyword `interval`.

ASE does not use common units, but ASE time, energy and length units. For example, the conversion between ASE time units
and fs should be performed by quantity times `units.fs`. Refer to the
[official documentation](https://ase.gitlab.io/ase/ase/units.html) for more details.

Critical arguments for IsotropicMTKNPT:
- timestep (float) – The time step in ASE time units.
- temperature_K (float) – The target temperature in K.
- pressure_au (float) – The external pressure in eV/Ang^3.
- tdamp (float) – The characteristic time scale for the thermostat in ASE time units. Typically, it is set to 100 times of timestep.
- pdamp (float) – The characteristic time scale for the barostat in ASE time units. Typically, it is set to 1000 times of timestep.

Critical arguments for NoseHooverChainNVT:
- timestep (float) – The time step in ASE time units.
- temperature_K (float) – The target temperature in K.
- tdamp (float) – The characteristic time scale for the thermostat in ASE time units.
  Typically, it is set to 100 times of timestep.

Refer to the [official documentation](https://ase.gitlab.io/ase/ase/md.html#id9) for more details in using ensembles.

An example script of NPT MD pipeline with Deepmd models:

```python
import argparse
import csv
from pathlib import Path

import numpy as np

from ase import units
from ase.io import read, write
from ase.io.trajectory import Trajectory
from ase.md.nose_hoover_chain import IsotropicMTKNPT
from ase.md.velocitydistribution import MaxwellBoltzmannDistribution, Stationary, ZeroRotation
from ase.optimize import FIRE, LBFGS
from ase.filters import FrechetCellFilter
from ase.neighborlist import neighbor_list

from deepmd.calculator import DP


def max_force(atoms):
    return float(np.linalg.norm(atoms.get_forces(), axis=1).max())


def min_pair_distance(atoms, cutoff=6.0):
    try:
        _, _, d = neighbor_list("ijd", atoms, cutoff)
        return float(np.min(d)) if len(d) else float("nan")
    except Exception:
        return float("nan")


# Convert stress from eV/A^3 to GPa. ASE default is eV/A^3.
def stress_gpa(atoms):
    try:
        return atoms.get_stress(voigt=True) / units.GPa
    except Exception:
        return np.full(6, np.nan)


def current_temperature(atoms):
    return float(2.0 * atoms.get_kinetic_energy() / (3 * len(atoms) * units.kB))


# Attach a monitor to the atoms object to log the simulation progress. Optional.
def make_monitor(atoms, csv_path: Path, v0: float):
    if not csv_path.exists():
        with open(csv_path, "w", newline="") as f:
            csv.writer(f).writerow([
                "stage", "step", "temperature_K",
                "epot_eV", "epot_per_atom_eV",
                "ekin_eV", "etot_eV",
                "volume_A3", "volume_ratio",
                "max_force_eV_A",
                "min_pair_distance_A",
                "density_atom_A3",
                "stress_xx_GPa", "stress_yy_GPa", "stress_zz_GPa",
                "stress_yz_GPa", "stress_xz_GPa", "stress_xy_GPa",
            ])

    def log(stage: str, step: int):
        epot = atoms.get_potential_energy()
        ekin = atoms.get_kinetic_energy()
        vol = atoms.get_volume()
        temp = current_temperature(atoms)
        fmax = max_force(atoms)
        dmin = min_pair_distance(atoms)
        density = len(atoms) / vol
        stress = stress_gpa(atoms)

        with open(csv_path, "a", newline="") as f:
            csv.writer(f).writerow([
                stage, step, temp,
                epot, epot / len(atoms),
                ekin, epot + ekin,
                vol, vol / v0,
                fmax,
                dmin,
                density,
                *stress.tolist(),
            ])

        print(
            f"[{stage:>9s}] step={step:7d} "
            f"T={temp:8.1f}K "
            f"E/N={epot / len(atoms): .6f}eV "
            f"V/V0={vol / v0: .4f} "
            f"Fmax={fmax: .4f}eV/A "
            f"dmin={dmin: .3f}A "
            f"P~={-np.nanmean(stress[:3]): .3f}GPa"
        )

    return log


def relax_structure(atoms, outdir: Path, args):
    print("\n=== FIRE rough relaxation ===")
    fire_traj = Trajectory(outdir / "relax_fire.traj", "w", atoms)
    fcf = FrechetCellFilter(atoms)
    fire = FIRE(fcf, logfile=str(outdir / "relax_fire.log"))
    fire.attach(fire_traj.write, interval=args.relax_traj_interval)
    fire.run(fmax=args.fire_fmax, steps=args.fire_steps)
    fire_traj.close()

    print("\n=== LBFGS fine relaxation ===")
    lbfgs_traj = Trajectory(outdir / "relax_lbfgs.traj", "w", atoms)
    fcf = FrechetCellFilter(atoms)
    lbfgs = LBFGS(fcf, logfile=str(outdir / "relax_lbfgs.log"))
    lbfgs.attach(lbfgs_traj.write, interval=args.relax_traj_interval)
    lbfgs.run(fmax=args.lbfgs_fmax, steps=args.lbfgs_steps)
    lbfgs_traj.close()

    write(outdir / "relaxed.extxyz", atoms)


def run_npt_simulations(atoms, outdir: Path, v0: float, args):
    """Perform NPT MD simulations at different temperatures and pressures.
    
    
    """
    temps = args.temps_k
    pressures = args.pressures_gpa
    monitor = make_monitor(atoms, outdir / f"md_monitor_{int(args.pressure_gpa)}Gpa.csv", v0)

    pressure_au = args.pressure_gpa * units.GPa

    print(f"NPT class: IsotropicMTKNPT")
    print(f"NPT pressure: {args.pressure_gpa:.3f} GPa")
    print(f"tdamp: {args.tdamp_fs:.1f} fs")
    print(f"pdamp: {args.pdamp_fs:.1f} fs")

    rng = np.random.default_rng(args.seed)

    for temp in temps:
        for press in pressures:
            print(f"\n=== Isotropic MTK NPT MD at {temp:.0f} K, {press:.0f} GPa ===")
    
            # Set initial velocities.
            MaxwellBoltzmannDistribution(atoms, temperature_K=temp, rng=rng, force_temp=True)

            # Remove total momentum and angular momentum to prevent flying ice-cube effect.
            Stationary(atoms)
            ZeroRotation(atoms)
    
            # Set up trajectory file.
            traj = Trajectory(outdir / f"npt_{int(temp)}K_{int(args.pressure_gpa)}Gpa.traj", "w", atoms)
    
            ase_logfile = None
            ase_loginterval = 1
            if args.ase_log_interval > 0:
                ase_logfile = str(outdir / f"npt_{int(temp)}K_{int(args.pressure_gpa)}Gpa.ase.log")
                ase_loginterval = args.ase_log_interval
    
            dyn = IsotropicMTKNPT(
                atoms,
                timestep=args.timestep_fs * units.fs,  # Time unit in ASE is not fs. Need conversion!
                temperature_K=temp,
                pressure_au=pressure_au,
                tdamp=args.tdamp_fs * units.fs,
                pdamp=args.pdamp_fs * units.fs,
                logfile=ase_logfile,
                loginterval=ase_loginterval,
            )
            
            # Attach trajectory writer action and monitor action to dynamics, so they will be called at preset intervals.
            dyn.attach(traj.write, interval=args.traj_interval)
            dyn.attach(
                lambda dyn=dyn, t=temp: monitor(f"NPT_{int(t)}K_{int(args.pressure_gpa)}Gpa", dyn.nsteps),
                interval=args.monitor_interval,
            )
    
            dyn.run(args.steps_per_temp)
            traj.close()
    
            write(outdir / f"final_{int(temp)}K_{int(args.pressure_gpa)}Gpa.extxyz", atoms)


def parse_args():
    p = argparse.ArgumentParser()

    p.add_argument("--model", required=True, help="Path to model file.")
    p.add_argument("--structure", required=True, help="Path to structure file.")
    p.add_argument("--outdir", default="./results", help="Output directory containing all logs and trajectory files.")
    p.add_argument("--head", default=None, help="Head name (for pretrained deepmd model only).")

    p.add_argument("--timestep-fs", type=float, default=1.0)
    p.add_argument("--steps-per-temp", type=int, default=10000, help="Number of steps to run per temperature-pressure pair.")

    p.add_argument("--traj-interval", type=int, default=100, help="Number of steps between MD trajectory saves.")
    p.add_argument("--monitor-interval", type=int, default=20, help="Number of steps between MD monitor prints.")
    p.add_argument(
        "--ase-log-interval", type=int, default=0, help="Number of steps between ASE log prints. Set to 0 to disable."
    )

    p.add_argument(
        "--relax-traj-interval", type=int, default=10, help="Number of steps between relaxation trajectory saves."
    )

    # Adjust thermo conditions to your need.
    p.add_argument("--pressures-gpa", type=float, nargs="+", default=[0.0], help="Pressure in GPa")
    p.add_argument("--temps-k", type=float, nargs="+", default=[300.0], help="Temperature in Kelvin")

    p.add_argument(
        "--tdamp-fs", type=float, default=100.0,
        help="Time constant for temperature coupling in fs. Better use default."
    )
    p.add_argument(
        "--pdamp-fs", type=float, default=1000.0,
        help="Time constant for pressure coupling in fs. Better use default."
    )

    p.add_argument("--fire-fmax", type=float, default=0.08)
    p.add_argument("--lbfgs-fmax", type=float, default=0.02)
    p.add_argument("--fire-steps", type=int, default=1000)
    p.add_argument("--lbfgs-steps", type=int, default=2000)

    p.add_argument("--seed", type=int, default=42)

    return p.parse_args()


def main():
    args = parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    atoms = read(args.structure)
    atoms.pbc = True

    calc_kwargs = {"model": args.model}
    if args.head is not None:
        calc_kwargs["head"] = args.head

    atoms.calc = DP(**calc_kwargs)
    # Must check if model provides stress calculator for NPT!
    try:
        stress = atoms.get_stress()
        print("Stress available.")
    except Exception as exc:
        raise RuntimeError(
            "Current model does not provide stress/virial. "
            "NPT simulation cannot run."
        ) from exc

    v0 = atoms.get_volume()

    print("\nInitial structure:")
    print(f"  formula    = {atoms.get_chemical_formula()}")
    print(f"  natoms     = {len(atoms)}")
    print(f"  volume     = {v0:.6f} A^3")
    print(f"  Epot/N     = {atoms.get_potential_energy() / len(atoms):.6f} eV/atom")
    print(f"  Fmax       = {max_force(atoms):.6f} eV/A")
    print(f"  stress GPa = {stress_gpa(atoms)}")

    relax_structure(atoms, outdir, v0, args)
    run_npt_simulations(atoms, outdir, v0, args)

    print("\nDone.")


if __name__ == "__main__":
    main()
```

For bohrium submission:
- Required forward files: **script.py**, **structure.xyz**, **
- Required backward files:

---

# References

[1] ASE documentation: [main documentation page](https://ase.gitlab.io/ase/)
