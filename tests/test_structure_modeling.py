from __future__ import annotations

import importlib.util
from io import StringIO
from pathlib import Path

import numpy as np
import pytest
from ase import Atoms
from ase.build import bulk
from ase.io import write as ase_write


def _load_modeling_module():
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "structure_modeling_test_module", root / "web" / "structure_modeling.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


modeling = _load_modeling_module()


def test_working_structure_snapshot_takes_precedence_over_source_artifact():
    original = bulk("Cu", "fcc", a=3.6, cubic=True)
    moved = original.copy()
    moved.positions[0] += [0.4, 0.5, 0.6]
    snapshot = StringIO()
    ase_write(snapshot, moved, format="extxyz")
    fallback_called = False

    def load_original():
        nonlocal fallback_called
        fallback_called = True
        return original

    working = modeling.load_working_structure(snapshot.getvalue(), load_original)

    assert not fallback_called
    assert np.allclose(working.positions, moved.positions)


def test_surface_builder_preserves_periodicity_and_adds_vacuum():
    source = bulk("Cu", "fcc", a=3.6, cubic=True)
    slab = modeling.apply_modeling_operation(
        source, "surface", {"miller": [1, 1, 1], "layers": 3, "vacuum": 8}
    )

    assert len(slab) == 12
    assert slab.pbc.tolist() == [True, True, True]
    assert slab.cell[2, 2] > source.cell[2, 2]


def test_supercell_supports_general_integer_matrix():
    source = bulk("Fe", "bcc", a=2.87, cubic=True)
    result = modeling.apply_modeling_operation(
        source,
        "supercell",
        {"matrix": [[2, 0, 0], [0, 1, 0], [0, 0, 1]]},
    )

    assert len(result) == 2 * len(source)
    assert np.isclose(result.get_volume(), 2 * source.get_volume())


def test_molecule_can_be_created_or_appended():
    empty = Atoms()
    water = modeling.apply_modeling_operation(
        empty, "molecule", {"name": "H2O", "position": [1, 2, 3], "append": False}
    )
    combined = modeling.apply_modeling_operation(
        bulk("Cu", "fcc"), "molecule", {"name": "H2O", "position": [0, 0, 5]}
    )

    assert water.get_chemical_formula() == "H2O"
    assert np.allclose(water.get_center_of_mass(), [1, 2, 3])
    assert len(combined) == len(bulk("Cu", "fcc")) + 3


def test_crystal_is_created_through_matcraft_bulk_builder():
    copper = modeling.apply_modeling_operation(
        Atoms(),
        "crystal",
        {"symbol": "Cu", "crystal_structure": "fcc", "a": 3.6, "cubic": True},
    )

    assert copper.get_chemical_formula() == "Cu4"
    assert np.allclose(copper.cell.lengths(), [3.6, 3.6, 3.6])
    assert copper.pbc.all()


def test_smiles_generates_explicit_hydrogen_3d_molecule():
    ethanol = modeling.molecule_from_smiles("CCO")

    assert ethanol.get_chemical_formula() == "C2H6O"
    assert len(ethanol) == 9
    assert np.ptp(ethanol.positions, axis=0).max() > 1


def test_smiles_molecule_can_be_appended_at_requested_center():
    source = bulk("Cu", "fcc", a=3.6, cubic=True)
    result = modeling.apply_modeling_operation(
        source,
        "molecule",
        {"smiles": "O", "position": [0, 0, 8], "append": True},
    )

    assert len(result) == len(source) + 3
    assert np.allclose(result[-3:].get_center_of_mass(), [0, 0, 8])


def test_matcraft_perturbation_is_seeded_and_changes_positions():
    source = bulk("Cu", "fcc", a=3.6, cubic=True)
    params = {"magnitude": 0.1, "mode": "random", "seed": 42}

    first = modeling.apply_modeling_operation(source, "perturb", params)
    second = modeling.apply_modeling_operation(source, "perturb", params)

    assert not np.allclose(first.positions, source.positions)
    assert np.allclose(first.positions, second.positions)


def test_coherent_interface_returns_ranked_candidate_metadata():
    silicon = bulk("Si", "diamond", a=5.43, cubic=True)
    candidates = modeling.generate_coherent_interfaces(
        silicon,
        silicon,
        {
            "film_miller": [1, 0, 0],
            "substrate_miller": [1, 0, 0],
            "film_thickness": 1,
            "substrate_thickness": 1,
            "max_interfaces": 1,
            "max_area": 80,
        },
    )

    assert len(candidates) == 1
    assert candidates[0]["id"] == "iface_0"
    assert candidates[0]["n_atoms"] == len(candidates[0]["atoms"])
    assert candidates[0]["area"] > 0
    assert candidates[0]["atoms"].pbc.tolist() == [True, True, False]


def test_invalid_supercell_is_rejected():
    with pytest.raises(modeling.ModelingError, match="non-singular"):
        modeling.apply_modeling_operation(
            bulk("Cu", "fcc"), "supercell", {"matrix": [[1, 0, 0]] * 3}
        )


def test_generated_structures_use_non_colliding_names(tmp_path):
    atoms = bulk("Si", "diamond")
    first = modeling.save_generated_structure(atoms, tmp_path, "surface")
    second = modeling.save_generated_structure(atoms, tmp_path, "surface")

    assert first.name == "surface.extxyz"
    assert second.name == "surface-1.extxyz"
    assert first.exists() and second.exists()
