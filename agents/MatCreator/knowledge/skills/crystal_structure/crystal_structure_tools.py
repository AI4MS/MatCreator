#!/usr/bin/env python3
"""
Crystal Structure CLI tools — direct replacements for the quest MCP server tools.

All commands print a JSON object to stdout and exit 0 on success, 1 on error.

Usage:
  python crystal_structure_tools.py <command> [options]

Commands:
  build-bulk-crystal   Create a bulk crystal from a formula and prototype.
  build-supercell      Expand a structure into a supercell.
  perturb-atoms        Generate perturbed copies of a structure.
  inspect-structure    Read a structure file and report metadata.
  transform-lattice    Apply scaling, strain, rotation, or custom matrix to a lattice.
  filter-by-entropy    Select a diverse subset of structures via entropy-based filtering.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import re
import sys
import time
import traceback
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

import numpy as np
from ase.atoms import Atoms
from ase.build import bulk, make_supercell
from ase.io import read, write

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

SupercellType = Union[int, Sequence[int], Sequence[Sequence[int]]]

_EXTENSION_MAP = {
    "extxyz": "extxyz",
    "xyz": "xyz",
    "cif": "cif",
    "vasp": "vasp",
    "poscar": "vasp",
    "json": "json",
}


def _generate_work_path(create: bool = True) -> str:
    calling_function = traceback.extract_stack(limit=2)[-2].name
    current_time = time.strftime("%Y%m%d%H%M%S")
    random_string = str(uuid.uuid4())[:8]
    work_path = f"{current_time}.{calling_function}.{random_string}"
    if create:
        os.makedirs(work_path, exist_ok=True)
    return work_path


def _sanitize_token(token: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "-", token.strip())
    return re.sub(r"-+", "-", cleaned).strip("-") or "structure"


def _apply_supercell(atoms: Atoms, size: SupercellType) -> Atoms:
    if size in (None, 1):
        return atoms
    if isinstance(size, int):
        return atoms.repeat((size, size, size))
    if isinstance(size, Sequence):
        size_list = list(size)
        if len(size_list) == 3 and all(isinstance(v, int) for v in size_list):
            return atoms.repeat(tuple(size_list))
        if len(size_list) == 3 and all(
            isinstance(row, Sequence) and len(row) == 3 for row in size_list
        ):
            matrix = np.array(size_list, dtype=int)
            return make_supercell(atoms, matrix)
    raise ValueError(
        "size must be an int, length-3 sequence of ints, or a 3x3 integer matrix"
    )


def _resolve_output_path(
    output_path: Optional[Union[str, Path]],
    formula: str,
    suffix: str,
    file_extension: str,
) -> Path:
    if output_path:
        destination = Path(output_path).expanduser()
        destination.parent.mkdir(parents=True, exist_ok=True)
        return destination
    work_dir = Path(_generate_work_path())
    work_dir.mkdir(parents=True, exist_ok=True)
    formula_token = _sanitize_token(formula)
    suffix_token = _sanitize_token(suffix)
    filename = f"{formula_token}-{suffix_token}.{file_extension}"
    return (work_dir / filename).resolve()


# ---------------------------------------------------------------------------
# Implementation functions
# ---------------------------------------------------------------------------

def build_bulk_crystal_impl(
    formula: str,
    crystal_structure: str,
    a: Optional[float] = None,
    c: Optional[float] = None,
    covera: Optional[float] = None,
    u: Optional[float] = None,
    spacegroup: Optional[int] = None,
    basis: Optional[Sequence[Sequence[float]]] = None,
    orthorhombic: bool = False,
    cubic: bool = False,
    size: SupercellType = 1,
    vacuum: Optional[float] = None,
    output_format: str = "extxyz",
    output_path: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    """Create a bulk crystal with ASE and persist it to disk."""
    builder_kwargs: Dict[str, Any] = {
        "name": formula,
        "crystalstructure": crystal_structure,
    }
    if a is not None:
        builder_kwargs["a"] = a
    if c is not None:
        builder_kwargs["c"] = c
    if covera is not None:
        builder_kwargs["covera"] = covera
    if u is not None:
        builder_kwargs["u"] = u
    if spacegroup is not None:
        builder_kwargs["spacegroup"] = spacegroup
    if basis is not None:
        builder_kwargs["basis"] = basis
    if orthorhombic:
        builder_kwargs["orthorhombic"] = True
    if cubic:
        builder_kwargs["cubic"] = True

    fmt = output_format.lower()
    file_extension = _EXTENSION_MAP.get(fmt, fmt)

    try:
        atoms = bulk(**builder_kwargs)
        atoms = _apply_supercell(atoms, size)
        if vacuum is not None:
            atoms.center(vacuum=vacuum)

        destination = _resolve_output_path(
            output_path=output_path,
            formula=formula,
            suffix=crystal_structure,
            file_extension=file_extension,
        )
        write(destination, atoms, format=fmt)

        return {
            "status": "success",
            "message": "Bulk crystal generated successfully.",
            "structure_path": str(destination),
            "chemical_formula": atoms.get_chemical_formula(empirical=True),
            "num_atoms": len(atoms),
            "cell": atoms.cell.tolist(),
            "pbc": atoms.get_pbc().tolist(),
        }
    except Exception as exc:
        return {
            "status": "error",
            "message": f"Failed to build crystal: {exc}",
            "structure_path": "",
            "chemical_formula": "",
            "num_atoms": 0,
            "cell": [],
            "pbc": [],
        }


def build_supercell_impl(
    input_structure: Union[str, Path],
    size: SupercellType,
    output_format: str = "extxyz",
    output_path: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    """Build a supercell from a structure file and write it to disk."""
    fmt = output_format.lower()
    file_extension = _EXTENSION_MAP.get(fmt, fmt)

    try:
        atoms = read(str(input_structure))
        supercell = _apply_supercell(atoms, size=size)

        formula = supercell.get_chemical_formula(empirical=True)
        destination = _resolve_output_path(
            output_path=output_path,
            formula=formula,
            suffix="supercell",
            file_extension=file_extension,
        )
        write(destination, supercell, format=fmt)

        return {
            "status": "success",
            "message": "Supercell generated successfully.",
            "structure_path": str(destination),
            "chemical_formula": formula,
            "num_atoms": len(supercell),
            "cell": supercell.cell.tolist(),
            "pbc": supercell.get_pbc().tolist(),
        }
    except Exception as exc:
        return {
            "status": "error",
            "message": f"Failed to build supercell: {exc}",
            "structure_path": "",
            "chemical_formula": "",
            "num_atoms": 0,
            "cell": [],
            "pbc": [],
        }


def _get_cell_perturb_matrix(cell_pert_fraction: float) -> np.ndarray:
    """Return a random 3×3 cell-perturbation matrix."""
    if cell_pert_fraction < 0:
        raise RuntimeError("cell_pert_fraction cannot be negative")
    e0 = np.random.rand(6)
    e = e0 * 2 * cell_pert_fraction - cell_pert_fraction
    return np.array([
        [1 + e[0], 0.5 * e[5], 0.5 * e[4]],
        [0.5 * e[5], 1 + e[1], 0.5 * e[3]],
        [0.5 * e[4], 0.5 * e[3], 1 + e[2]],
    ])


def _get_atom_perturb_vector(
    atom_pert_distance: float,
    atom_pert_style: str = "normal",
) -> np.ndarray:
    """Return a random displacement vector for a single atom."""
    if atom_pert_distance < 0:
        raise RuntimeError("atom_pert_distance cannot be negative")

    if atom_pert_style == "normal":
        e = np.random.randn(3)
        return (atom_pert_distance / np.sqrt(3)) * e
    elif atom_pert_style == "uniform":
        e = np.random.randn(3)
        while np.linalg.norm(e) < 0.1:
            e = np.random.randn(3)
        random_unit = e / np.linalg.norm(e)
        v = np.power(np.random.rand(1), 1 / 3)
        return atom_pert_distance * v * random_unit
    elif atom_pert_style == "const":
        e = np.random.randn(3)
        while np.linalg.norm(e) < 0.1:
            e = np.random.randn(3)
        return atom_pert_distance * (e / np.linalg.norm(e))
    else:
        raise RuntimeError(f"unsupported atom_pert_style={atom_pert_style}")


def _perturb_atoms_single(
    atoms: Atoms,
    pert_num: int,
    cell_pert_fraction: float,
    atom_pert_distance: float,
    atom_pert_style: str = "normal",
    atom_pert_prob: float = 1.0,
) -> List[Atoms]:
    """Generate *pert_num* perturbed replicas of *atoms*."""
    pert_atoms_ls = []
    for _ in range(pert_num):
        pert_cell = np.matmul(
            atoms.get_cell().array, _get_cell_perturb_matrix(cell_pert_fraction)
        )
        pert_positions = atoms.get_positions().copy()
        pert_natoms = int(atom_pert_prob * len(atoms))
        pert_atom_id = sorted(
            np.random.choice(range(len(atoms)), pert_natoms, replace=False).tolist()
        )
        for kk in pert_atom_id:
            pert_positions[kk] += _get_atom_perturb_vector(
                atom_pert_distance, atom_pert_style
            )
        pert_atoms_ls.append(
            Atoms(
                symbols=atoms.get_chemical_symbols(),
                positions=pert_positions,
                cell=pert_cell,
                pbc=atoms.get_pbc(),
            )
        )
    return pert_atoms_ls


def perturb_atoms_impl(
    structure_path: Union[str, Path],
    pert_num: int,
    cell_pert_fraction: float,
    atom_pert_distance: float,
    atom_pert_style: str = "normal",
    atom_pert_prob: float = 1.0,
    output_format: str = "extxyz",
    output_path: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    """Read a structure file, generate perturbed replicas, write multi-frame output."""
    fmt = output_format.lower()
    file_extension = _EXTENSION_MAP.get(fmt, fmt)

    try:
        atoms = read(str(structure_path))
        perturbed = _perturb_atoms_single(
            atoms=atoms,
            pert_num=pert_num,
            cell_pert_fraction=cell_pert_fraction,
            atom_pert_distance=atom_pert_distance,
            atom_pert_style=atom_pert_style,
            atom_pert_prob=atom_pert_prob,
        )
        if not perturbed:
            raise RuntimeError("No perturbed structures were generated")

        formula = atoms.get_chemical_formula(empirical=True)
        destination = _resolve_output_path(
            output_path=output_path,
            formula=formula,
            suffix="perturbed",
            file_extension=file_extension,
        )
        write(destination, perturbed, format=fmt)

        return {
            "status": "success",
            "message": "Perturbed structures generated successfully.",
            "structure_path": str(destination),
            "num_structures": len(perturbed),
            "num_atoms_per_structure": [len(a) for a in perturbed],
        }
    except Exception as exc:
        return {
            "status": "error",
            "message": f"Failed to perturb atoms: {exc}",
            "structure_path": "",
            "num_structures": 0,
            "num_atoms_per_structure": [],
        }


def transform_lattice_impl(
    structure_path: Union[str, Path],
    scale: Optional[Union[float, List[float]]] = None,
    strain: Optional[Union[List[float], List[List[float]]]] = None,
    rotation: Optional[Union[List[float], List[List[float]]]] = None,
    transform_matrix: Optional[List[List[float]]] = None,
    scale_atoms: bool = True,
    output_format: str = "extxyz",
    output_path: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    """Apply lattice transformations (scale → strain → rotation → matrix) and write result."""
    fmt = output_format.lower()
    file_extension = _EXTENSION_MAP.get(fmt, fmt)

    try:
        from scipy.spatial.transform import Rotation as R

        atoms = read(str(structure_path))
        original_cell = atoms.get_cell().array.copy()
        new_cell = original_cell.copy()
        operations: List[str] = []

        if scale is not None:
            if isinstance(scale, (int, float)):
                new_cell = new_cell * scale
                operations.append(f"uniform_scale={scale}")
            else:
                scale_array = np.array(scale).reshape(3)
                new_cell = new_cell * scale_array.reshape(3, 1)
                operations.append(f"anisotropic_scale={list(scale)}")

        if strain is not None:
            strain_array = np.array(strain)
            if strain_array.shape == (6,):
                strain_tensor = np.array([
                    [strain_array[0], strain_array[5], strain_array[4]],
                    [strain_array[5], strain_array[1], strain_array[3]],
                    [strain_array[4], strain_array[3], strain_array[2]],
                ])
                operations.append(f"strain_voigt={strain_array.tolist()}")
            elif strain_array.shape == (3, 3):
                strain_tensor = strain_array
                operations.append("strain_tensor=3x3")
            else:
                raise ValueError("strain must be length-6 Voigt notation or 3x3 tensor")
            new_cell = (np.eye(3) + strain_tensor) @ new_cell

        if rotation is not None:
            rotation_array = np.array(rotation)
            if rotation_array.shape == (3,):
                rot_matrix = R.from_euler("ZYZ", rotation_array, degrees=True).as_matrix()
                operations.append(f"euler_angles={rotation_array.tolist()}")
            elif rotation_array.shape == (3, 3):
                rot_matrix = rotation_array
                operations.append("rotation_matrix=3x3")
            else:
                raise ValueError("rotation must be 3 Euler angles or a 3x3 rotation matrix")
            new_cell = rot_matrix @ new_cell

        if transform_matrix is not None:
            transform_array = np.array(transform_matrix)
            if transform_array.shape != (3, 3):
                raise ValueError("transform_matrix must be 3x3")
            new_cell = transform_array @ new_cell
            operations.append("custom_transform=3x3")

        atoms.set_cell(new_cell, scale_atoms=scale_atoms)

        formula = atoms.get_chemical_formula(empirical=True)
        destination = _resolve_output_path(
            output_path=output_path,
            formula=formula,
            suffix="transformed",
            file_extension=file_extension,
        )
        write(destination, atoms, format=fmt)

        return {
            "status": "success",
            "message": "Lattice transformation completed successfully.",
            "structure_path": str(destination),
            "original_cell": original_cell.tolist(),
            "transformed_cell": new_cell.tolist(),
            "operations_applied": operations,
            "scale_atoms": scale_atoms,
            "num_atoms": len(atoms),
        }
    except Exception as exc:
        return {
            "status": "error",
            "message": f"Failed to transform lattice: {exc}",
            "structure_path": "",
            "original_cell": [],
            "transformed_cell": [],
            "operations_applied": [],
            "scale_atoms": scale_atoms,
            "num_atoms": 0,
        }


def inspect_structure_impl(
    structure_path: Union[str, Path],
    export_volume: bool = False,
    export_cell_parameters: bool = False,
    export_density: bool = False,
    export_positions: bool = False,
    export_forces: bool = False,
    export_energy: bool = False,
    export_stress: bool = False,
    output_dir: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    """Inspect an ASE-readable structure file and summarize its metadata."""
    try:
        source = Path(structure_path).expanduser()
        if not source.exists():
            raise FileNotFoundError(f"Structure file not found: {source}")

        frames = read(str(source), index=":")
        if isinstance(frames, Atoms):
            frames = [frames]
        else:
            frames = list(frames)
        if not frames:
            raise RuntimeError("No frames could be read from the structure file")

        formulas = [frame.get_chemical_formula(empirical=True) for frame in frames]
        num_atoms_per_frame = [len(frame) for frame in frames]
        info_keys = sorted({key for frame in frames for key in frame.info.keys()})
        array_keys = sorted({key for frame in frames for key in frame.arrays.keys()})

        need_export = any([
            export_volume, export_cell_parameters, export_density, export_positions,
            export_forces, export_energy, export_stress,
        ])
        if need_export:
            work_dir = Path(output_dir).expanduser() if output_dir else Path(_generate_work_path())
            work_dir.mkdir(parents=True, exist_ok=True)

        result: Dict[str, Any] = {
            "status": "success",
            "message": f"Read {len(frames)} frame(s) from structure file.",
            "structure_path": str(source.resolve()),
            "num_frames": len(frames),
            "chemical_formulas": sorted(set(formulas)),
            "num_atoms": sorted(set(num_atoms_per_frame)),
            "info_keys": info_keys,
            "array_keys": array_keys,
        }

        if export_volume:
            volumes = np.array([frame.get_volume() for frame in frames])
            vfile = work_dir / "volumes.txt"
            np.savetxt(vfile, volumes, fmt="%.8f", header="Volume (Å³)")
            result["volume_file"] = str(vfile.resolve())
            result["volume_summary"] = {
                "mean": float(volumes.mean()),
                "std": float(volumes.std()),
                "min": float(volumes.min()),
                "max": float(volumes.max()),
            }

        if export_cell_parameters:
            cell_params = np.array([frame.cell.cellpar() for frame in frames])
            cpfile = work_dir / "cell_parameters.txt"
            np.savetxt(cpfile, cell_params, fmt="%.8f",
                       header="a(Å) b(Å) c(Å) alpha(°) beta(°) gamma(°)")
            result["cell_parameters_file"] = str(cpfile.resolve())

        if export_density:
            densities = []
            for frame in frames:
                try:
                    density = sum(frame.get_masses()) / frame.get_volume() * 1.66054
                    densities.append(density)
                except Exception:
                    densities.append(0.0)
            densities = np.array(densities)
            dfile = work_dir / "densities.txt"
            np.savetxt(dfile, densities, fmt="%.8f", header="Density (g/cm³)")
            result["density_file"] = str(dfile.resolve())
            if densities.max() > 0:
                result["density_summary"] = {
                    "mean": float(densities.mean()),
                    "std": float(densities.std()),
                    "min": float(densities.min()),
                    "max": float(densities.max()),
                }

        if export_positions:
            pfile = work_dir / "positions.extxyz"
            write(pfile, frames)
            result["positions_file"] = str(pfile.resolve())

        if export_forces and "forces" in array_keys:
            all_forces = [
                frame.arrays["forces"].flatten()
                for frame in frames
                if "forces" in frame.arrays
            ]
            if all_forces:
                forces_array = np.array(all_forces)
                ffile = work_dir / "forces.txt"
                np.savetxt(ffile, forces_array, fmt="%.8f")
                result["forces_file"] = str(ffile.resolve())
                force_norms = np.linalg.norm(
                    forces_array.reshape(len(frames), -1, 3), axis=2
                )
                result["forces_summary"] = {
                    "max_force_norm": float(force_norms.max()),
                    "mean_force_norm": float(force_norms.mean()),
                    "std_force_norm": float(force_norms.std()),
                }

        if export_energy:
            found_key = next(
                (k for k in ["energy", "total_energy", "potential_energy", "free_energy"]
                 if k in info_keys),
                None,
            )
            if found_key:
                energies = np.array([frame.info.get(found_key, 0.0) for frame in frames])
                efile = work_dir / "energies.txt"
                np.savetxt(efile, energies, fmt="%.8f",
                           header=f"Energy (eV) - from '{found_key}'")
                result["energy_file"] = str(efile.resolve())
                result["energy_key_used"] = found_key
                result["energy_summary"] = {
                    "mean": float(energies.mean()),
                    "std": float(energies.std()),
                    "min": float(energies.min()),
                    "max": float(energies.max()),
                }

        if export_stress:
            found_key = next(
                (k for k in ["stress", "virial"] if k in info_keys), None
            )
            if found_key:
                stresses = np.array([
                    frame.info.get(found_key, np.zeros(6)) for frame in frames
                ])
                sfile = work_dir / "stresses.txt"
                np.savetxt(sfile, stresses, fmt="%.8f",
                           header=f"Stress (eV/Å³) - from '{found_key}' [xx yy zz yz xz xy]")
                result["stress_file"] = str(sfile.resolve())
                result["stress_key_used"] = found_key

        return result

    except Exception as exc:
        return {
            "status": "error",
            "message": f"Failed to inspect structure: {exc}",
            "structure_path": "",
            "num_frames": 0,
            "chemical_formulas": [],
            "num_atoms": [],
            "info_keys": [],
            "array_keys": [],
        }


def _h_filter_cpu(
    iter_confs: List[Atoms],
    dset_confs: List[Atoms],
    chunk_size: int,
    max_sel: int,
    k: int,
    cutoff: float,
    batch_size: int,
    h: float,
    dtype: str,
):
    from quests.descriptor import get_descriptors
    from quests.entropy import delta_entropy, entropy

    num_ref = len(dset_confs)
    if len(dset_confs) == 0:
        if chunk_size >= len(iter_confs):
            return iter_confs, {"num_confs": len(iter_confs)}
        random.shuffle(iter_confs)
        dset_confs = iter_confs[:chunk_size]
        iter_confs = iter_confs[chunk_size:]
        num_ref = 0
        max_sel -= chunk_size

    max_iter = min(
        max_sel // chunk_size + (max_sel % chunk_size > 0),
        len(iter_confs) // chunk_size + (len(iter_confs) % chunk_size > 0),
    )
    iter_desc = get_descriptors(iter_confs, k=k, cutoff=cutoff, dtype=dtype)
    dset_desc = get_descriptors(dset_confs, k=k, cutoff=cutoff, dtype=dtype)

    atom_indices_iter: List[tuple] = []
    start = 0
    for n in [a.get_number_of_atoms() for a in iter_confs]:
        end = start + n
        atom_indices_iter.append((start, end))
        start = end

    H = entropy(dset_desc, h=h, batch_size=batch_size)
    H_list = [H]
    result: Dict[str, Any] = {"iter_00": H, "num_confs": len(dset_confs)}
    indices: List[int] = []

    for ii in range(max_iter):
        re_indices = [i for i in range(len(iter_confs)) if i not in indices]
        re_confs = [iter_confs[i] for i in re_indices]
        re_desc = [
            iter_desc[atom_indices_iter[i][0]: atom_indices_iter[i][1]]
            for i in re_indices
        ]
        x = np.vstack(re_desc)
        delta = delta_entropy(x, dset_desc, h=h, batch_size=batch_size)

        re_natoms = [a.get_number_of_atoms() for a in re_confs]
        atom_indices: List[tuple] = []
        s = 0
        for n in re_natoms:
            atom_indices.append((s, s + n))
            s += n
        delta_sums = [delta[s:e].sum() for s, e in atom_indices]
        sorted_re = [
            idx for idx, _ in sorted(
                zip(re_indices, delta_sums), key=lambda x: x[1], reverse=True
            )
        ]
        selected = sorted_re[:chunk_size]

        dset_desc_ls = [dset_desc]
        for idx in selected:
            indices.append(idx)
            dset_confs.append(iter_confs[idx])
            dset_desc_ls.append(iter_desc[atom_indices_iter[idx][0]: atom_indices_iter[idx][1]])
        dset_desc = np.vstack(dset_desc_ls)
        H = entropy(dset_desc, h=h, batch_size=batch_size)
        dH = H - H_list[-1]
        H_list.append(H)
        result.update({f"iter_{ii + 1:02d}": H, "num_confs": len(dset_confs)})
        if dH < 1e-2:
            break

    return dset_confs[num_ref:], result


def _h_filter_gpu(
    iter_confs: List[Atoms],
    dset_confs: List[Atoms],
    chunk_size: int,
    max_sel: int,
    k: int,
    cutoff: float,
    batch_size: int,
    h: float,
    dtype: str,
):
    import torch
    from quests.descriptor import get_descriptors
    from quests.gpu.entropy import delta_entropy, entropy

    device = "cuda" if torch.cuda.is_available() else "cpu"
    num_ref = len(dset_confs)
    if len(dset_confs) == 0:
        if chunk_size >= len(iter_confs):
            return iter_confs, {"num_confs": len(iter_confs)}
        random.shuffle(iter_confs)
        dset_confs = iter_confs[:chunk_size]
        iter_confs = iter_confs[chunk_size:]
        num_ref = 0
        max_sel -= chunk_size

    max_iter = min(
        max_sel // chunk_size + (max_sel % chunk_size > 0),
        len(iter_confs) // chunk_size + (len(iter_confs) % chunk_size > 0),
    )
    iter_desc = get_descriptors(iter_confs, k=k, cutoff=cutoff, dtype=dtype)
    dset_desc = get_descriptors(dset_confs, k=k, cutoff=cutoff, dtype=dtype)

    atom_indices_iter: List[tuple] = []
    start = 0
    for n in [a.get_number_of_atoms() for a in iter_confs]:
        end = start + n
        atom_indices_iter.append((start, end))
        start = end

    x = torch.tensor(dset_desc, device=device, dtype=torch.float32)
    H = float(entropy(x, h=h, batch_size=batch_size, device=device).cpu().numpy())
    H_list = [H]
    result: Dict[str, Any] = {"iter_00": H, "num_confs": len(dset_confs)}
    indices: List[int] = []

    for ii in range(max_iter):
        re_indices = [i for i in range(len(iter_confs)) if i not in indices]
        re_confs = [iter_confs[i] for i in re_indices]
        re_desc = [
            iter_desc[atom_indices_iter[i][0]: atom_indices_iter[i][1]]
            for i in re_indices
        ]
        x = torch.tensor(np.vstack(re_desc), device=device, dtype=torch.float32)
        y = torch.tensor(dset_desc, device=device, dtype=torch.float32)
        delta = delta_entropy(x, y, h=h, batch_size=batch_size, device=device).cpu().numpy()

        re_natoms = [a.get_number_of_atoms() for a in re_confs]
        atom_indices: List[tuple] = []
        s = 0
        for n in re_natoms:
            atom_indices.append((s, s + n))
            s += n
        delta_sums = [delta[s:e].sum() for s, e in atom_indices]
        sorted_re = [
            idx for idx, _ in sorted(
                zip(re_indices, delta_sums), key=lambda x: x[1], reverse=True
            )
        ]
        selected = sorted_re[:chunk_size]

        dset_desc_ls = [dset_desc]
        for idx in selected:
            indices.append(idx)
            dset_confs.append(iter_confs[idx])
            dset_desc_ls.append(iter_desc[atom_indices_iter[idx][0]: atom_indices_iter[idx][1]])
        dset_desc = np.vstack(dset_desc_ls)
        y = torch.tensor(dset_desc, device=device, dtype=torch.float32)
        H = float(entropy(y, h=h, batch_size=batch_size, device=device).cpu().numpy())
        dH = H - H_list[-1]
        H_list.append(H)
        result.update({f"iter_{ii + 1:02d}": H, "num_confs": len(dset_confs)})
        if dH < 1e-2:
            break

    return dset_confs[num_ref:], result


def filter_by_entropy_impl(
    iter_confs: Union[List[Union[Path, str]], Union[Path, str]],
    reference: Union[List[Union[Path, str]], Union[Path, str]] = [],
    chunk_size: int = 10,
    k: int = 32,
    cutoff: float = 5.0,
    batch_size: int = 1000,
    h: float = 0.015,
    max_sel: int = 50,
) -> Dict[str, Any]:
    """Entropy-based subset selection; tries GPU first, falls back to CPU."""
    try:
        if isinstance(iter_confs, list):
            loaded = [read(p, index=":") for p in iter_confs]
            iter_confs_atoms = [a for sub in loaded for a in (sub if isinstance(sub, list) else [sub])]
        else:
            raw = read(str(iter_confs), index=":")
            iter_confs_atoms = list(raw) if not isinstance(raw, Atoms) else [raw]

        if isinstance(reference, (Path, str)):
            raw = read(str(reference), index=":")
            dset_confs = list(raw) if not isinstance(raw, Atoms) else [raw]
        elif isinstance(reference, list):
            loaded = [read(p, index=":") for p in reference]
            dset_confs = [a for sub in loaded for a in (sub if isinstance(sub, list) else [sub])]
        else:
            dset_confs = []

        common_kwargs = dict(
            chunk_size=chunk_size, max_sel=max_sel, k=k,
            cutoff=cutoff, batch_size=batch_size, h=h, dtype="float32",
        )
        try:
            import torch  # noqa: F401
            select_atoms, select_result = _h_filter_gpu(
                iter_confs_atoms, dset_confs, **common_kwargs
            )
        except ImportError:
            select_atoms, select_result = _h_filter_cpu(
                iter_confs_atoms, dset_confs, **common_kwargs
            )

        work_path = Path(_generate_work_path())
        work_path.mkdir(parents=True, exist_ok=True)
        out_path = work_path / "selected.extxyz"
        write(out_path, select_atoms)

        return {
            "status": "success",
            "message": "Filter by entropy completed.",
            "selected_atoms": str(out_path.resolve()),
            "entropy": select_result,
        }
    except Exception as exc:
        return {
            "status": "error",
            "message": f"Filter by entropy failed: {exc}",
            "selected_atoms": "",
            "entropy": {},
        }


# ---------------------------------------------------------------------------
# CLI command handlers
# ---------------------------------------------------------------------------

def cmd_build_bulk_crystal(args: argparse.Namespace) -> Dict[str, Any]:
    size: SupercellType = args.size
    if args.size_matrix:
        size = json.loads(args.size_matrix)
    basis = json.loads(args.basis) if args.basis else None
    return build_bulk_crystal_impl(
        formula=args.formula,
        crystal_structure=args.crystal_structure,
        a=args.a,
        c=args.c,
        covera=args.covera,
        u=args.u,
        spacegroup=args.spacegroup,
        basis=basis,
        orthorhombic=args.orthorhombic,
        cubic=args.cubic,
        size=size,
        vacuum=args.vacuum,
        output_format=args.output_format,
        output_path=args.output,
    )


def cmd_build_supercell(args: argparse.Namespace) -> Dict[str, Any]:
    size: SupercellType = args.size
    if args.size_matrix:
        size = json.loads(args.size_matrix)
    return build_supercell_impl(
        input_structure=args.input,
        size=size,
        output_format=args.output_format,
        output_path=args.output,
    )


def cmd_perturb_atoms(args: argparse.Namespace) -> Dict[str, Any]:
    return perturb_atoms_impl(
        structure_path=args.input,
        pert_num=args.pert_num,
        cell_pert_fraction=args.cell_pert_fraction,
        atom_pert_distance=args.atom_pert_distance,
        atom_pert_style=args.atom_pert_style,
        atom_pert_prob=args.atom_pert_prob,
        output_format=args.output_format,
        output_path=args.output,
    )


def cmd_inspect_structure(args: argparse.Namespace) -> Dict[str, Any]:
    return inspect_structure_impl(
        structure_path=args.input,
        export_volume=args.export_volume,
        export_cell_parameters=args.export_cell_parameters,
        export_density=args.export_density,
        export_positions=args.export_positions,
        export_forces=args.export_forces,
        export_energy=args.export_energy,
        export_stress=args.export_stress,
        output_dir=args.output_dir,
    )


def cmd_transform_lattice(args: argparse.Namespace) -> Dict[str, Any]:
    scale = json.loads(args.scale) if args.scale else None
    strain = json.loads(args.strain) if args.strain else None
    rotation = json.loads(args.rotation) if args.rotation else None
    transform_matrix = json.loads(args.transform_matrix) if args.transform_matrix else None
    return transform_lattice_impl(
        structure_path=args.input,
        scale=scale,
        strain=strain,
        rotation=rotation,
        transform_matrix=transform_matrix,
        scale_atoms=not args.no_scale_atoms,
        output_format=args.output_format,
        output_path=args.output,
    )


def cmd_filter_by_entropy(args: argparse.Namespace) -> Dict[str, Any]:
    reference: Union[List[str], str] = args.reference if args.reference else []
    return filter_by_entropy_impl(
        iter_confs=args.iter_confs,
        reference=reference,
        chunk_size=args.chunk_size,
        k=args.k,
        cutoff=args.cutoff,
        batch_size=args.batch_size,
        h=args.h,
        max_sel=args.max_sel,
    )


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="crystal_structure_tools",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")
    sub.required = True

    # ------------------------------------------------------------------
    # build-bulk-crystal
    # ------------------------------------------------------------------
    p = sub.add_parser(
        "build-bulk-crystal",
        help="Create a bulk crystal from a chemical formula and prototype.",
    )
    p.add_argument("formula", help="Chemical formula, e.g. 'Al', 'NaCl'.")
    p.add_argument(
        "crystal_structure",
        help="Prototype name, e.g. 'fcc', 'bcc', 'hcp', 'rocksalt'.",
    )
    p.add_argument("--a", type=float, default=None, help="Lattice constant a (Å).")
    p.add_argument("--c", type=float, default=None, help="Lattice constant c (Å).")
    p.add_argument("--covera", type=float, default=None, help="c/a ratio.")
    p.add_argument("--u", type=float, default=None, help="Internal coordinate u.")
    p.add_argument("--spacegroup", type=int, default=None, help="Spacegroup number.")
    p.add_argument(
        "--basis",
        default=None,
        help="Fractional basis positions as a JSON list of [x,y,z] lists.",
    )
    p.add_argument("--orthorhombic", action="store_true", help="Use orthorhombic cell.")
    p.add_argument("--cubic", action="store_true", help="Use cubic cell.")
    p.add_argument(
        "--size",
        type=int,
        default=1,
        help="Uniform supercell repetition (integer). Ignored if --size-matrix is given.",
    )
    p.add_argument(
        "--size-matrix",
        default=None,
        help=(
            "Supercell size as a JSON list: [nx,ny,nz] or [[m00,m01,m02],[m10,m11,m12],[m20,m21,m22]]."
        ),
    )
    p.add_argument("--vacuum", type=float, default=None, help="Vacuum padding (Å).")
    p.add_argument(
        "--output-format", default="extxyz",
        help="Output format: extxyz (default), xyz, cif, vasp, json.",
    )
    p.add_argument("--output", default=None, help="Explicit output file path.")
    p.set_defaults(func=cmd_build_bulk_crystal)

    # ------------------------------------------------------------------
    # build-supercell
    # ------------------------------------------------------------------
    p = sub.add_parser(
        "build-supercell",
        help="Expand a structure file into a supercell.",
    )
    p.add_argument("input", help="Path to input structure file (any ASE-readable format).")
    p.add_argument(
        "--size",
        type=int,
        default=1,
        help="Uniform supercell repetition (integer). Ignored if --size-matrix is given.",
    )
    p.add_argument(
        "--size-matrix",
        default=None,
        help="Supercell size as a JSON list: [nx,ny,nz] or 3x3 integer matrix.",
    )
    p.add_argument("--output-format", default="extxyz", help="Output format.")
    p.add_argument("--output", default=None, help="Explicit output file path.")
    p.set_defaults(func=cmd_build_supercell)

    # ------------------------------------------------------------------
    # perturb-atoms
    # ------------------------------------------------------------------
    p = sub.add_parser(
        "perturb-atoms",
        help="Generate perturbed copies of a structure.",
    )
    p.add_argument("input", help="Path to input structure file.")
    p.add_argument("--pert-num", type=int, required=True, help="Number of perturbed structures.")
    p.add_argument(
        "--cell-pert-fraction", type=float, required=True,
        help="Fractional cell distortion magnitude (e.g. 0.03 for 3%%).",
    )
    p.add_argument(
        "--atom-pert-distance", type=float, required=True,
        help="Maximum atomic displacement magnitude (Å).",
    )
    p.add_argument(
        "--atom-pert-style", default="normal",
        choices=["normal", "uniform", "const"],
        help="Displacement style (default: normal).",
    )
    p.add_argument(
        "--atom-pert-prob", type=float, default=1.0,
        help="Fraction of atoms to perturb per frame (default: 1.0).",
    )
    p.add_argument("--output-format", default="extxyz", help="Output format.")
    p.add_argument("--output", default=None, help="Explicit output file path.")
    p.set_defaults(func=cmd_perturb_atoms)

    # ------------------------------------------------------------------
    # inspect-structure
    # ------------------------------------------------------------------
    p = sub.add_parser(
        "inspect-structure",
        help="Read a structure file and report metadata.",
    )
    p.add_argument("input", help="Path to input structure file.")
    p.add_argument("--export-volume", action="store_true", help="Export volumes to file.")
    p.add_argument("--export-cell-parameters", action="store_true",
                   help="Export cell parameters (a, b, c, alpha, beta, gamma) to file.")
    p.add_argument("--export-density", action="store_true", help="Export densities to file.")
    p.add_argument("--export-positions", action="store_true",
                   help="Export all frames as extxyz to file.")
    p.add_argument("--export-forces", action="store_true", help="Export forces to file.")
    p.add_argument("--export-energy", action="store_true", help="Export energies to file.")
    p.add_argument("--export-stress", action="store_true", help="Export stresses to file.")
    p.add_argument("--output-dir", default=None,
                   help="Directory for exported property files (auto-generated if omitted).")
    p.set_defaults(func=cmd_inspect_structure)

    # ------------------------------------------------------------------
    # transform-lattice
    # ------------------------------------------------------------------
    p = sub.add_parser(
        "transform-lattice",
        help="Apply scaling, strain, rotation, or a custom matrix to a lattice.",
    )
    p.add_argument("input", help="Path to input structure file.")
    p.add_argument(
        "--scale", default=None,
        help=(
            "Uniform scale factor (float) or anisotropic [sx,sy,sz] as JSON. "
            "Example: 0.97 or '[1.0,1.0,0.95]'."
        ),
    )
    p.add_argument(
        "--strain", default=None,
        help=(
            "Strain tensor: 6-element Voigt [exx,eyy,ezz,eyz,exz,exy] or 3x3 tensor as JSON."
        ),
    )
    p.add_argument(
        "--rotation", default=None,
        help="Euler angles [alpha,beta,gamma] in degrees (ZYZ) or 3x3 rotation matrix as JSON.",
    )
    p.add_argument(
        "--transform-matrix", default=None,
        help="Arbitrary 3x3 transformation matrix applied as new_cell = M @ cell, as JSON.",
    )
    p.add_argument(
        "--no-scale-atoms", action="store_true",
        help="Keep Cartesian positions fixed instead of scaling with the lattice.",
    )
    p.add_argument("--output-format", default="extxyz", help="Output format.")
    p.add_argument("--output", default=None, help="Explicit output file path.")
    p.set_defaults(func=cmd_transform_lattice)

    # ------------------------------------------------------------------
    # filter-by-entropy
    # ------------------------------------------------------------------
    p = sub.add_parser(
        "filter-by-entropy",
        help="Select a diverse subset of structures via entropy-based filtering.",
    )
    p.add_argument(
        "iter_confs", nargs="+",
        help="One or more candidate structure files (any ASE-readable format).",
    )
    p.add_argument(
        "--reference", nargs="*", default=[],
        help="Optional reference structure files already in the dataset.",
    )
    p.add_argument("--chunk-size", type=int, default=10,
                   help="Structures selected per iteration (default: 10).")
    p.add_argument("--k", type=int, default=32,
                   help="Number of nearest neighbours for descriptor (default: 32).")
    p.add_argument("--cutoff", type=float, default=5.0,
                   help="Descriptor cutoff radius in Å (default: 5.0).")
    p.add_argument("--batch-size", type=int, default=1000,
                   help="Batch size for entropy computation (default: 1000).")
    p.add_argument("--h", type=float, default=0.015,
                   help="Bandwidth parameter h (default: 0.015).")
    p.add_argument("--max-sel", type=int, default=50,
                   help="Maximum number of structures to select (default: 50).")
    p.set_defaults(func=cmd_filter_by_entropy)

    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    result = args.func(args)
    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("status") == "success" else 1)


if __name__ == "__main__":
    main()
