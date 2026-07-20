
# Example python code for MSD, diffusivity, and conductivity analysis with a manual MSD workflow

```python
from pathlib import Path
import os

import numpy as np
from ase.io import read


SCRIPT_DIR = Path(__file__).resolve().parent
os.environ.setdefault("MPLCONFIGDIR", str(SCRIPT_DIR / ".matplotlib"))

import matplotlib.pyplot as plt


STRUCTURE_LABELS = ["0", "25", "50", "75", "100"]
TRAJECTORY_NAME = "300.0_nvt.traj"

mobile_species = "Li"
mobile_ion_charge = 1.0
temperature_K = 300.0
time_step_fs = 2.0
step_skip = 100
analysis_start_fraction = 0.0
analysis_end_fraction = 1.0
msd_fit_start_fraction = 0.5
msd_fit_end_fraction = 1.0

ELEMENTARY_CHARGE_C = 1.602176634e-19
BOLTZMANN_J_K = 1.380649e-23


def analyze_trajectory(label):
    trajectory_path = SCRIPT_DIR / label / TRAJECTORY_NAME
    if not trajectory_path.exists():
        raise FileNotFoundError(f"Trajectory not found: {trajectory_path}")

    frames = read(trajectory_path, index=":")
    n_total = len(frames)
    if n_total < 10:
        raise ValueError(f"Trajectory frames too few for {label}: {n_total}")

    start_index = int(n_total * analysis_start_fraction)
    end_index = int(n_total * analysis_end_fraction)
    selected_frames = frames[start_index:end_index]
    if len(selected_frames) < 10:
        raise ValueError(
            f"Selected trajectory frames too few for {label}: {len(selected_frames)}"
        )

    symbols = selected_frames[0].get_chemical_symbols()
    mobile_indices = [i for i, symbol in enumerate(symbols) if symbol == mobile_species]
    n_mobile = len(mobile_indices)
    if n_mobile == 0:
        raise ValueError(f"No {mobile_species!r} atoms found in {label}.")

    frame_interval_fs = time_step_fs * step_skip
    dt_ps = frame_interval_fs / 1000.0
    start_time_ps = start_index * dt_ps
    relative_time_ps = np.arange(len(selected_frames)) * dt_ps
    absolute_time_ps = start_time_ps + relative_time_ps
    msd = calculate_msd(selected_frames, mobile_indices)
    diffusivity, diffusivity_std_dev, slope = calculate_diffusivity(
        relative_time_ps, msd
    )
    conductivity = calculate_conductivity(selected_frames, n_mobile, diffusivity)
    conductivity_std_dev = calculate_conductivity(
        selected_frames, n_mobile, diffusivity_std_dev
    )

    msd_data_path = SCRIPT_DIR / f"{mobile_species.lower()}_msd_{label}.dat"
    np.savetxt(
        msd_data_path,
        np.column_stack([relative_time_ps, absolute_time_ps, msd]),
        header="time_ps_relative time_ps_absolute msd_A2",
    )

    return {
        "label": label,
        "n_frames": n_total,
        "n_selected_frames": len(selected_frames),
        "n_mobile": n_mobile,
        "start_time_ps": start_time_ps,
        "end_time_ps": absolute_time_ps[-1],
        "time_ps": relative_time_ps,
        "msd": msd,
        "msd_slope": slope,
        "diffusivity": diffusivity,
        "diffusivity_std_dev": diffusivity_std_dev,
        "conductivity": conductivity,
        "conductivity_std_dev": conductivity_std_dev,
        "msd_data_path": msd_data_path,
    }


def calculate_msd(frames, mobile_indices):
    mobile_indices = np.array(mobile_indices)
    n_frames = len(frames)
    first = frames[0]
    previous_scaled = first.get_scaled_positions(wrap=True)[mobile_indices]
    unwrapped_positions = np.empty((n_frames, len(mobile_indices), 3))
    unwrapped_positions[0] = first.get_positions()[mobile_indices]

    for i, atoms in enumerate(frames[1:], start=1):
        scaled = atoms.get_scaled_positions(wrap=True)[mobile_indices]
        delta_scaled = scaled - previous_scaled
        delta_scaled -= np.round(delta_scaled)
        delta_cart = np.dot(delta_scaled, atoms.get_cell().array)
        unwrapped_positions[i] = unwrapped_positions[i - 1] + delta_cart
        previous_scaled = scaled

    displacements = unwrapped_positions - unwrapped_positions[0]
    squared_displacements = np.sum(displacements**2, axis=2)
    return np.mean(squared_displacements, axis=1)


def calculate_diffusivity(time_ps, msd):
    fit_start = int(len(msd) * msd_fit_start_fraction)
    fit_end = int(len(msd) * msd_fit_end_fraction)
    fit_start = max(0, min(fit_start, len(msd) - 2))
    fit_end = max(fit_start + 2, min(fit_end, len(msd)))

    fit_time = time_ps[fit_start:fit_end]
    fit_msd = msd[fit_start:fit_end]
    coeffs, covariance = np.polyfit(fit_time, fit_msd, 1, cov=True)
    slope = coeffs[0]
    slope_std_dev = np.sqrt(covariance[0, 0])
    diffusivity_cm2_s = slope * 1.0e-4 / 6.0
    diffusivity_std_dev_cm2_s = slope_std_dev * 1.0e-4 / 6.0
    return diffusivity_cm2_s, diffusivity_std_dev_cm2_s, slope


def calculate_conductivity(frames, n_mobile, diffusivity_cm2_s):
    volumes_a3 = np.array([atoms.get_volume() for atoms in frames])
    mean_volume_m3 = np.mean(volumes_a3) * 1.0e-30
    number_density_m3 = n_mobile / mean_volume_m3
    diffusivity_m2_s = diffusivity_cm2_s * 1.0e-4

    conductivity_s_m = (
        number_density_m3
        * (mobile_ion_charge * ELEMENTARY_CHARGE_C) ** 2
        * diffusivity_m2_s
        / (BOLTZMANN_J_K * temperature_K)
    )
    return conductivity_s_m * 10.0


def save_summary(results):
    summary_path = SCRIPT_DIR / f"{mobile_species.lower()}_diffusion_summary.dat"
    header = (
        "label n_frames n_selected_frames n_mobile start_time_ps end_time_ps "
        "diffusivity_cm2_s diffusivity_std_dev_cm2_s "
        "conductivity_mS_cm conductivity_std_dev_mS_cm"
    )
    with summary_path.open("w", encoding="utf-8") as file:
        file.write(f"# {header}\n")
        for result in results:
            file.write(
                f"{result['label']} "
                f"{result['n_frames']:d} "
                f"{result['n_selected_frames']:d} "
                f"{result['n_mobile']:d} "
                f"{result['start_time_ps']:.8f} "
                f"{result['end_time_ps']:.8f} "
                f"{result['diffusivity']:.8e} "
                f"{result['diffusivity_std_dev']:.8e} "
                f"{result['conductivity']:.8e} "
                f"{result['conductivity_std_dev']:.8e}\n"
            )
    return summary_path


def plot_msd(results):
    plt.figure(figsize=(7, 4.5))
    for result in results:
        plt.plot(
            result["time_ps"],
            result["msd"],
            linewidth=2,
            label=f"{result['label']}",
        )

    plt.xlabel("Time / ps")
    plt.ylabel(r"MSD / $\mathrm{\AA}^2$")
    plt.title(f"{mobile_species} MSD comparison")
    plt.legend(title="Structure")
    plt.tight_layout()

    plot_path = SCRIPT_DIR / f"{mobile_species.lower()}_msd_comparison.png"
    plt.savefig(plot_path, dpi=300)
    plt.close()
    return plot_path


def plot_conductivity(results):
    labels = [result["label"] for result in results]
    conductivities = [result["conductivity"] for result in results]
    conductivity_std_devs = [result["conductivity_std_dev"] for result in results]

    plt.figure(figsize=(7, 4.5))
    x = np.arange(len(labels))
    plt.bar(
        x,
        conductivities,
        yerr=conductivity_std_devs,
        capsize=5,
        color="#4C78A8",
        edgecolor="black",
        linewidth=0.8,
    )
    plt.xticks(x, labels)
    plt.xlabel("Structure")
    plt.ylabel("Conductivity / mS cm$^{-1}$")
    plt.title(f"{mobile_species} conductivity comparison")
    plt.tight_layout()

    plot_path = SCRIPT_DIR / f"{mobile_species.lower()}_conductivity_comparison.png"
    plt.savefig(plot_path, dpi=300)
    plt.close()
    return plot_path


def main():
    results = [analyze_trajectory(label) for label in STRUCTURE_LABELS]

    for result in results:
        print(f"Structure {result['label']}")
        print(
            f"  Window: {result['start_time_ps']:.3f} to "
            f"{result['end_time_ps']:.3f} ps"
        )
        print(f"  Diffusivity: {result['diffusivity']:.6e} cm^2/s")
        print(
            f"  Diffusivity std dev: "
            f"{result['diffusivity_std_dev']:.6e} cm^2/s"
        )
        print(f"  Conductivity: {result['conductivity']:.6e} mS/cm")
        print(
            f"  Conductivity std dev: "
            f"{result['conductivity_std_dev']:.6e} mS/cm"
        )

    summary_path = save_summary(results)
    msd_plot_path = plot_msd(results)
    conductivity_plot_path = plot_conductivity(results)

    print(f"Saved summary: {summary_path}")
    print(f"Saved MSD comparison plot: {msd_plot_path}")
    print(f"Saved conductivity comparison plot: {conductivity_plot_path}")


if __name__ == "__main__":
    main()
```


Parameter notes:

- `time_step_fs` and `step_skip` must match the actual MD integration step and trajectory write interval.
- `analysis_start_fraction` / `analysis_end_fraction` define which part of the trajectory is analyzed.
- `msd_fit_start_fraction` / `msd_fit_end_fraction` define the MSD fitting window and can strongly affect the extracted diffusivity and conductivity.
- `mobile_species` selects the diffusing ion species.
- `mobile_ion_charge` and `temperature_K` are used in the Nernst-Einstein conductivity conversion.