import prepare_matpes as pm


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
