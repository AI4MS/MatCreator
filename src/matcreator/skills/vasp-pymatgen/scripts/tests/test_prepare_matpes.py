import prepare_matpes as pm
from pymatgen.core import Lattice, Structure


class TestBuildIncarOverrides:
    def test_default_no_spin(self):
        ov = pm.build_incar_overrides(spin=False)
        assert ov["ISPIN"] == 1
        assert ov["ENCUT"] == 600
        assert ov["LCHARG"] is False
        assert ov["LAECHG"] is False
        assert ov["LMIXTAU"] is False
        assert ov["LORBIT"] is None  # None => pymatgen removes the tag
        assert ov["MAGMOM"] is None

    def test_spin_enabled(self):
        ov = pm.build_incar_overrides(spin=True)
        assert ov["ISPIN"] == 2
        assert "MAGMOM" not in ov  # keep MatPES default MAGMOM guesses
        assert ov["LORBIT"] is None
        assert ov["ENCUT"] == 600

    def test_does_not_mutate_base(self):
        pm.build_incar_overrides(spin=True)
        assert pm.BASE_OVERRIDES["ISPIN"] == 1
        assert "MAGMOM" in pm.BASE_OVERRIDES


def make_si(a: float = 5.43) -> Structure:
    return Structure(
        Lattice.cubic(a),
        ["Si", "Si"],
        [[0.0, 0.0, 0.0], [0.25, 0.25, 0.25]],
    )


class TestCheckStructure:
    def test_good_structure_passes(self):
        assert pm.check_structure(make_si()) is None

    def test_overlapping_atoms_flagged(self):
        s = Structure(
            Lattice.cubic(5.43),
            ["Si", "Si"],
            [[0.0, 0.0, 0.0], [0.01, 0.0, 0.0]],
        )
        msg = pm.check_structure(s)
        assert msg is not None and "distance" in msg

    def test_huge_volume_per_atom_flagged(self):
        s = Structure(Lattice.cubic(30.0), ["Si"], [[0.0, 0.0, 0.0]])
        msg = pm.check_structure(s)
        assert msg is not None and "volume" in msg

    def test_tiny_volume_per_atom_flagged(self):
        s = Structure(Lattice.cubic(0.9), ["Si"], [[0.0, 0.0, 0.0]])
        msg = pm.check_structure(s)
        assert msg is not None and "volume" in msg


import pytest
from ase.build import bulk
from ase.io import write as ase_write


@pytest.fixture
def traj_file(tmp_path):
    atoms = bulk("Si", "diamond", a=5.43)
    images = []
    for i in range(5):
        img = atoms.copy()
        img.positions[0, 0] += 0.01 * i
        images.append(img)
    path = tmp_path / "traj.extxyz"
    ase_write(path, images)
    return str(path)


class TestParseFramesSlice:
    def test_full_spec(self):
        assert pm.parse_frames_slice("0:10:2") == slice(0, 10, 2)

    def test_open_ended(self):
        assert pm.parse_frames_slice("2:") == slice(2, None, None)

    def test_step_only(self):
        assert pm.parse_frames_slice("::5") == slice(None, None, 5)

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            pm.parse_frames_slice("1:2:3:4")
        with pytest.raises(ValueError):
            pm.parse_frames_slice("a:b")


class TestLoadFrames:
    def test_loads_all_frames(self, traj_file):
        frames = pm.load_frames(traj_file)
        assert len(frames) == 5
        assert all(len(f) == 2 for f in frames)  # pymatgen Structures

    def test_slice_applied(self, traj_file):
        frames = pm.load_frames(traj_file, "1:5:2")
        assert len(frames) == 2

    def test_single_frame_file(self, tmp_path):
        atoms = bulk("Si", "diamond", a=5.43)
        path = tmp_path / "POSCAR"
        ase_write(path, atoms, format="vasp")
        frames = pm.load_frames(str(path))
        assert len(frames) == 1


GOOD_INCAR = {
    "ISPIN": 1,
    "ENCUT": 600.0,
    "LCHARG": False,
    "LAECHG": False,
    "LMIXTAU": False,
    "ALGO": "Normal",
    "KSPACING": 0.22,
}


class TestValidateIncar:
    def test_good_incar_passes(self):
        assert pm.validate_incar(dict(GOOD_INCAR), spin=False) == []

    def test_wrong_ispin(self):
        incar = dict(GOOD_INCAR, ISPIN=2)
        errs = pm.validate_incar(incar, spin=False)
        assert any("ISPIN" in e for e in errs)

    def test_wrong_encut(self):
        incar = dict(GOOD_INCAR, ENCUT=680.0)
        errs = pm.validate_incar(incar, spin=False)
        assert any("ENCUT" in e for e in errs)

    def test_lorbit_present_fails(self):
        incar = dict(GOOD_INCAR, LORBIT=11)
        errs = pm.validate_incar(incar, spin=False)
        assert any("LORBIT" in e for e in errs)

    def test_magmom_present_without_spin_fails(self):
        incar = dict(GOOD_INCAR, MAGMOM=[0.6, 0.6])
        errs = pm.validate_incar(incar, spin=False)
        assert any("MAGMOM" in e for e in errs)

    def test_spin_requires_ispin2_and_magmom(self):
        incar = dict(GOOD_INCAR, ISPIN=2, MAGMOM=[0.6, 0.6])
        assert pm.validate_incar(incar, spin=True) == []
        errs = pm.validate_incar(dict(GOOD_INCAR), spin=True)
        assert any("ISPIN" in e for e in errs)
        assert any("MAGMOM" in e for e in errs)

    def test_lcharg_true_fails(self):
        incar = dict(GOOD_INCAR, LCHARG=True)
        errs = pm.validate_incar(incar, spin=False)
        assert any("LCHARG" in e for e in errs)


from pathlib import Path


class TestValidateDir:
    def test_empty_dir_reports_all_missing(self, tmp_path):
        errs = pm.validate_dir(tmp_path, spin=False)
        assert len(errs) == 3
        assert any("INCAR" in e for e in errs)
        assert any("POSCAR" in e for e in errs)
        assert any("POTCAR" in e for e in errs)

    def test_missing_potcar_only(self, tmp_path):
        (tmp_path / "INCAR").write_text("ISPIN = 1\n")
        (tmp_path / "POSCAR").write_text("x\n")
        errs = pm.validate_dir(tmp_path, spin=False)
        assert errs == ["missing POTCAR"]


import os

needs_potcar = pytest.mark.skipif(
    not os.environ.get("PMG_VASP_PSP_DIR"),
    reason="PMG_VASP_PSP_DIR not set; POTCAR library unavailable",
)


@needs_potcar
class TestGenerateInputs:
    def test_generate_and_validate_no_spin(self, tmp_path):
        outdir = tmp_path / "job"
        pm.generate_inputs(make_si(), outdir, spin=False)
        assert (outdir / "INCAR").is_file()
        assert (outdir / "POSCAR").is_file()
        assert (outdir / "POTCAR").is_file()
        assert not (outdir / "KPOINTS").exists()
        assert pm.validate_dir(outdir, spin=False) == []

    def test_generate_and_validate_spin(self, tmp_path):
        outdir = tmp_path / "job_spin"
        pm.generate_inputs(make_si(), outdir, spin=True)
        assert pm.validate_dir(outdir, spin=True) == []

    def test_validator_catches_tampered_incar(self, tmp_path):
        outdir = tmp_path / "job_bad"
        pm.generate_inputs(make_si(), outdir, spin=False)
        incar_path = outdir / "INCAR"
        content = incar_path.read_text().replace("ENCUT = 600", "ENCUT = 520")
        incar_path.write_text(content)
        errs = pm.validate_dir(outdir, spin=False)
        assert any("ENCUT" in e for e in errs)


class TestCli:
    def test_requires_structure_file(self, capsys):
        with pytest.raises(SystemExit):
            pm.parse_args([])

    def test_validate_only_needs_no_structure(self):
        args = pm.parse_args(["--validate-only", "some_dir"])
        assert args.validate_only == ["some_dir"]

    def test_missing_psp_dir_errors(self, traj_file, monkeypatch, capsys):
        monkeypatch.delenv("PMG_VASP_PSP_DIR", raising=False)
        rc = pm.main([traj_file])
        assert rc == 1
        assert "PMG_VASP_PSP_DIR" in capsys.readouterr().err

    def test_multiframe_layout_and_summary(self, traj_file, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("PMG_VASP_PSP_DIR", "/fake")
        targets = []
        monkeypatch.setattr(pm, "generate_inputs", lambda s, d, spin: targets.append(Path(d)))
        monkeypatch.setattr(pm, "validate_dir", lambda d, spin: [])
        outdir = tmp_path / "out"
        rc = pm.main([traj_file, "-o", str(outdir)])
        assert rc == 0
        assert targets == [outdir / f"frame_{i:04d}" for i in range(5)]
        out = capsys.readouterr().out
        assert "5 generated, 0 skipped, 0 failed" in out

    def test_single_frame_no_subdir(self, tmp_path, monkeypatch):
        from ase.build import bulk
        from ase.io import write as ase_write
        path = tmp_path / "si.cif"
        ase_write(path, bulk("Si", "diamond", a=5.43))
        monkeypatch.setenv("PMG_VASP_PSP_DIR", "/fake")
        targets = []
        monkeypatch.setattr(pm, "generate_inputs", lambda s, d, spin: targets.append(Path(d)))
        monkeypatch.setattr(pm, "validate_dir", lambda d, spin: [])
        outdir = tmp_path / "single"
        rc = pm.main([str(path), "-o", str(outdir)])
        assert rc == 0
        assert targets == [outdir]

    def test_bad_frame_skipped_not_fatal(self, traj_file, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("PMG_VASP_PSP_DIR", "/fake")
        monkeypatch.setattr(pm, "generate_inputs", lambda s, d, spin: None)
        monkeypatch.setattr(pm, "validate_dir", lambda d, spin: [])
        warnings = iter(["atoms overlap", None, None, None, None])
        monkeypatch.setattr(pm, "check_structure", lambda s: next(warnings))
        rc = pm.main([traj_file, "-o", str(tmp_path / "out")])
        assert rc == 0
        out = capsys.readouterr().out
        assert "4 generated, 1 skipped, 0 failed" in out
        assert "[SKIP]" in out

    def test_validation_failure_exits_1(self, traj_file, tmp_path, monkeypatch):
        monkeypatch.setenv("PMG_VASP_PSP_DIR", "/fake")
        monkeypatch.setattr(pm, "generate_inputs", lambda s, d, spin: None)
        monkeypatch.setattr(pm, "validate_dir", lambda d, spin: ["bad INCAR"])
        rc = pm.main([traj_file, "-o", str(tmp_path / "out")])
        assert rc == 1

    def test_all_frames_skipped_exits_1(self, traj_file, tmp_path, monkeypatch):
        monkeypatch.setenv("PMG_VASP_PSP_DIR", "/fake")
        monkeypatch.setattr(pm, "check_structure", lambda s: "bad")
        rc = pm.main([traj_file, "-o", str(tmp_path / "out")])
        assert rc == 1

    def test_validate_only_mode(self, tmp_path, monkeypatch, capsys):
        d1, d2 = tmp_path / "a", tmp_path / "b"
        d1.mkdir(); d2.mkdir()
        results = {str(d1): [], str(d2): ["missing INCAR"]}
        monkeypatch.setattr(pm, "validate_dir", lambda d, spin: results[str(d)])
        rc = pm.main(["--validate-only", str(d1), str(d2)])
        assert rc == 1
        out = capsys.readouterr().out
        assert "[OK]" in out and "[FAIL]" in out
