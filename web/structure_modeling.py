"""Server-side atomic modeling operations used by the structure workbench."""
from __future__ import annotations

import re
from io import StringIO
from pathlib import Path
from typing import Any, Callable

import numpy as np
from ase import Atoms
from ase.build import molecule
from ase.io import read, write


class ModelingError(ValueError):
    """Raised when a modeling request has invalid or unsupported parameters."""


def load_working_structure(
    structure_string: str,
    fallback: Callable[[], Atoms],
) -> Atoms:
    """Load the current editor snapshot, falling back to the source artifact."""
    if structure_string:
        return read(StringIO(structure_string), format="extxyz")
    return fallback()


def _vec3(value: Any, name: str, cast: Callable = float) -> tuple:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ModelingError(f"{name} must contain exactly 3 values")
    try:
        return tuple(cast(item) for item in value)
    except (TypeError, ValueError) as exc:
        raise ModelingError(f"{name} contains invalid values") from exc


def _positive_int(value: Any, name: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ModelingError(f"{name} must be an integer") from exc
    if result < 1:
        raise ModelingError(f"{name} must be at least 1")
    return result


def _molecule(name: str) -> Atoms:
    try:
        return molecule(name)
    except Exception as exc:
        raise ModelingError(
            f"Unknown ASE molecule '{name}'. Examples: H2O, NH3, CH4, C6H6, CO2"
        ) from exc


def molecule_from_smiles(
    smiles: str,
    *,
    random_seed: int = 61453,
    optimize: bool = True,
) -> Atoms:
    """Generate an explicit-hydrogen 3D molecule from a SMILES string."""
    smiles = str(smiles).strip()
    if not smiles:
        raise ModelingError("A SMILES string is required")
    try:
        from rdkit import Chem
        from rdkit.Chem import AllChem
    except ImportError as exc:
        raise ModelingError("RDKit is required for SMILES molecule generation") from exc

    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise ModelingError("Invalid SMILES string")
    mol = Chem.AddHs(mol)
    params = AllChem.ETKDGv3()
    params.randomSeed = int(random_seed)
    if AllChem.EmbedMolecule(mol, params) != 0:
        raise ModelingError("Could not generate 3D coordinates for this SMILES")
    if optimize:
        if AllChem.MMFFHasAllMoleculeParams(mol):
            AllChem.MMFFOptimizeMolecule(mol)
        else:
            AllChem.UFFOptimizeMolecule(mol)

    conformer = mol.GetConformer()
    symbols = [atom.GetSymbol() for atom in mol.GetAtoms()]
    positions = []
    for index in range(mol.GetNumAtoms()):
        point = conformer.GetAtomPosition(index)
        positions.append([point.x, point.y, point.z])
    return Atoms(symbols=symbols, positions=positions)


def generate_coherent_interfaces(
    film: Atoms,
    substrate: Atoms,
    params: dict[str, Any],
) -> list[dict[str, Any]]:
    """Generate coherent-interface candidates through MatCraft Kit's ZSL builder."""
    try:
        from pymatgen.io.ase import AseAtomsAdaptor
        from mckit.operate.interface import InterfaceBuilder
    except ImportError as exc:
        raise ModelingError("MatCraft Kit and pymatgen are required for coherent interfaces") from exc

    film_miller = _vec3(params.get("film_miller", [1, 0, 0]), "film_miller", int)
    substrate_miller = _vec3(
        params.get("substrate_miller", [1, 0, 0]), "substrate_miller", int
    )
    if not any(film_miller) or not any(substrate_miller):
        raise ModelingError("Miller indices cannot all be zero")

    gap = float(params.get("gap", 2.0))
    vacuum = float(params.get("vacuum_over_film", 10.0))
    film_thickness = _positive_int(params.get("film_thickness", 3), "film_thickness")
    substrate_thickness = _positive_int(
        params.get("substrate_thickness", 3), "substrate_thickness"
    )
    max_interfaces = _positive_int(params.get("max_interfaces", 10), "max_interfaces")
    max_interfaces = min(max_interfaces, 100)
    max_area = float(params.get("max_area", 400.0))
    max_length_tol = float(params.get("max_length_tol", 0.03))
    max_angle_tol = float(params.get("max_angle_tol", 0.01))
    max_area_ratio_tol = float(params.get("max_area_ratio_tol", 0.09))
    if min(gap, vacuum, max_area, max_length_tol, max_angle_tol, max_area_ratio_tol) < 0:
        raise ModelingError("Interface distances and matching tolerances must be non-negative")
    if max_area == 0:
        raise ModelingError("max_area must be positive")

    try:
        builder = InterfaceBuilder()
        listed = builder.list_terminations(
            film=film,
            substrate=substrate,
            miller_film=film_miller,
            miller_substrate=substrate_miller,
            max_area=max_area,
            max_length_tol=max_length_tol,
            max_angle_tol=max_angle_tol,
        )
    except Exception as exc:
        raise ModelingError(f"Could not initialize MatCraft Kit interface builder: {exc}") from exc

    candidates: list[dict[str, Any]] = []
    try:
        adaptor = AseAtomsAdaptor()
        for termination in listed["terminations"][:max_interfaces]:
            interface = builder.apply(
                film=film,
                substrate=substrate,
                miller_film=film_miller,
                miller_substrate=substrate_miller,
                termination=termination.index,
                max_area=max_area,
                max_length_tol=max_length_tol,
                max_angle_tol=max_angle_tol,
                gap=gap,
                vacuum_between=vacuum,
                thickness_film=film_thickness,
                thickness_substrate=substrate_thickness,
                in_layers=bool(params.get("in_layers", True)),
            )
            interface_atoms = adaptor.get_atoms(interface)
            interface_atoms.set_pbc((True, True, False))
            candidates.append(
                {
                    "id": f"iface_{len(candidates)}",
                    "atoms": interface_atoms,
                    "von_mises_strain": float(listed["von_mises_strain"]),
                    "termination_index": termination.index,
                    "termination": [termination.film_label, termination.substrate_label],
                    "interface_index": termination.index,
                    "area": float(listed["match_area"]),
                    "n_atoms": len(interface_atoms),
                }
            )
    except Exception as exc:
        raise ModelingError(f"MatCraft Kit interface matching failed: {exc}") from exc
    return candidates


def _available_path(directory: Path, stem: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    safe_stem = re.sub(r"[^A-Za-z0-9_.-]+", "-", stem).strip("-.") or "model"
    candidate = directory / f"{safe_stem}.extxyz"
    for index in range(1, 10_000):
        if not candidate.exists():
            return candidate
        candidate = directory / f"{safe_stem}-{index}.extxyz"
    raise ModelingError("Too many generated structures with the same name")


def apply_modeling_operation(
    source: Atoms,
    operation: str,
    params: dict[str, Any],
    *,
    secondary: Atoms | None = None,
) -> Atoms:
    """Apply an ASE-backed modeling operation without mutating the inputs."""
    atoms = source.copy()

    if operation == "surface":
        miller = _vec3(params.get("miller", [1, 0, 0]), "miller", int)
        layers = _positive_int(params.get("layers", 4), "layers")
        vacuum = float(params.get("vacuum", 10.0))
        if vacuum < 0:
            raise ModelingError("vacuum must be non-negative")
        try:
            from mckit.operate.surface import SurfaceBuilder
            from pymatgen.io.ase import AseAtomsAdaptor

            builder = SurfaceBuilder(n_layers=layers, vacuum=vacuum)
            terminations = builder.find_terminations(
                AseAtomsAdaptor().get_structure(atoms), miller=miller
            )
            if not terminations:
                raise ModelingError("MatCraft Kit could not find a surface termination")
            result = terminations[0].slab.copy()
        except (ImportError, TypeError, ValueError, RuntimeError) as exc:
            raise ModelingError(f"Could not build surface with MatCraft Kit: {exc}") from exc

    elif operation == "supercell":
        matrix = params.get("matrix", [[2, 0, 0], [0, 2, 0], [0, 0, 1]])
        try:
            matrix_array = np.asarray(matrix, dtype=int)
        except (TypeError, ValueError) as exc:
            raise ModelingError("matrix must be a 3x3 integer array") from exc
        if matrix_array.shape != (3, 3) or abs(np.linalg.det(matrix_array)) < 0.5:
            raise ModelingError("matrix must be a non-singular 3x3 integer array")
        try:
            from mckit.operate.supercell import SupercellBuilder

            result = SupercellBuilder().apply(
                structure=atoms, repeat=matrix_array.tolist()
            ).to_ase_atoms()
        except (ImportError, TypeError, ValueError) as exc:
            raise ModelingError(f"Could not build supercell with MatCraft Kit: {exc}") from exc

    elif operation == "molecule":
        smiles = str(params.get("smiles", "")).strip()
        try:
            from mckit.operate.molecule_creation import (
                ASEMoleculeBuilder,
                SMILESMoleculeBuilder,
            )

            built = (
                SMILESMoleculeBuilder().apply(
                    smiles=smiles,
                    optimize=bool(params.get("optimize", True)),
                    vacuum=0,
                )
                if smiles
                else ASEMoleculeBuilder().apply(
                    name=str(params.get("name", "H2O")).strip()
                )
            )
            fragment = built.to_ase_atoms()
        except (ImportError, TypeError, ValueError, RuntimeError) as exc:
            raise ModelingError(f"Could not create molecule with MatCraft Kit: {exc}") from exc
        position = np.asarray(_vec3(params.get("position", [0, 0, 0]), "position"))
        fragment.translate(position - fragment.get_center_of_mass())
        if bool(params.get("append", True)) and len(atoms):
            result = atoms + fragment
            if atoms.cell.rank == 3:
                result.set_cell(atoms.cell)
                result.set_pbc(atoms.pbc)
        else:
            result = fragment

    elif operation == "crystal":
        symbol = str(params.get("symbol", "Cu")).strip()
        crystal_structure = str(params.get("crystal_structure", "fcc")).strip()
        a = params.get("a")
        cubic = bool(params.get("cubic", True))
        try:
            from mckit.operate.bulk import BulkBuilder

            if a is None or str(a).strip() == "":
                # MatCraft Kit requires an explicit lattice parameter whereas
                # the existing UI historically allowed ASE reference-state
                # inference. Preserve that input contract, then hand the actual
                # construction to MatCraft Kit.
                from ase.build import bulk as ase_bulk

                a = ase_bulk(
                    symbol,
                    crystalstructure=crystal_structure,
                    cubic=cubic,
                ).cell.lengths()[0]

            result = BulkBuilder().apply(
                element=symbol,
                structure_type=crystal_structure,
                a=float(a),
                conventional_unit_cell=cubic,
            ).to_ase_atoms()
        except (ImportError, TypeError, ValueError, RuntimeError) as exc:
            raise ModelingError(
                f"Could not create {symbol} {crystal_structure} crystal with MatCraft Kit: {exc}"
            ) from exc

    elif operation == "perturb":
        magnitude = float(params.get("magnitude", 0.1))
        if magnitude < 0:
            raise ModelingError("magnitude must be non-negative")
        mode = str(params.get("mode", "random"))
        try:
            from mckit.operate.perturbation import PerturbationBuilder

            result = PerturbationBuilder().apply(
                structure=atoms,
                magnitude=magnitude,
                mode=mode,
                seed=int(params.get("seed", 0)),
            ).to_ase_atoms()
        except (ImportError, TypeError, ValueError, IndexError) as exc:
            raise ModelingError(f"Could not perturb structure with MatCraft Kit: {exc}") from exc

    elif operation == "interface":
        if secondary is None:
            raise ModelingError("A second structure is required for an interface")
        axis = int(params.get("axis", 2))
        if axis not in (0, 1, 2):
            raise ModelingError("axis must be 0, 1, or 2")
        maxstrain = float(params.get("maxstrain", 0.05))
        distance = float(params.get("distance", 2.0))
        if maxstrain < 0 or distance < 0:
            raise ModelingError("maxstrain and distance must be non-negative")
        try:
            from mckit.operate.interface import InterfaceBuilder
            from pymatgen.io.ase import AseAtomsAdaptor

            result = AseAtomsAdaptor().get_atoms(
                InterfaceBuilder().apply(
                    film=atoms,
                    substrate=secondary.copy(),
                    miller_film=_vec3(params.get("film_miller", [1, 0, 0]), "film_miller", int),
                    miller_substrate=_vec3(
                        params.get("substrate_miller", [1, 0, 0]),
                        "substrate_miller",
                        int,
                    ),
                    max_length_tol=maxstrain,
                    gap=distance,
                    vacuum_between=float(params.get("vacuum", 10.0)),
                )
            )
        except Exception as exc:
            raise ModelingError(f"Could not create interface with MatCraft Kit: {exc}") from exc

    elif operation == "vacuum":
        axis = int(params.get("axis", 2))
        vacuum = float(params.get("vacuum", 10.0))
        if axis not in (0, 1, 2) or vacuum < 0:
            raise ModelingError("axis must be 0, 1, or 2 and vacuum must be non-negative")
        atoms.center(vacuum=vacuum, axis=axis)
        result = atoms

    else:
        raise ModelingError(f"Unsupported modeling operation: {operation}")

    return result


def save_generated_structure(atoms: Atoms, output_dir: Path, operation: str) -> Path:
    """Write a generated structure to a non-colliding ExtXYZ file."""
    path = _available_path(output_dir, operation)
    write(path, atoms, format="extxyz")
    return path
