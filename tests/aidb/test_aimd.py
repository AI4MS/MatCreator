from pathlib import Path

import pytest

from matcreator.aidb.service import AIDBError


FIXTURE = Path(__file__).resolve().parent / (
    "fixtures/vasp/si-md-1"
)


def test_prepare_nvt_keeps_requested_hydrogen_timestep_and_step_count(tmp_path, monkeypatch):
    from pymatgen.core import Lattice, Structure
    from pymatgen.io.vasp import Incar, Poscar
    from pymatgen.io.vasp.sets import MPMDSet

    from matcreator.aidb.aimd import prepare_aimd_nvt

    water = Structure(Lattice.cubic(10), ["O", "H", "H"], [[0.5, 0.5, 0.5], [0.59, 0.5, 0.5], [0.48, 0.59, 0.5]])
    water.to(filename=tmp_path / "H2O-cell.cif")

    def write_without_potcar(self, output_dir, **kwargs):
        # Exercise the real pymatgen-generated structure and INCAR without licensed PAW data.
        Path(output_dir).mkdir()
        self.incar.write_file(Path(output_dir) / "INCAR")
        self.poscar.write_file(Path(output_dir) / "POSCAR")

    monkeypatch.setattr(MPMDSet, "write_input", write_without_potcar)
    prepared = prepare_aimd_nvt(tmp_path, "H2O-cell.cif", "aimd", temperature_kelvin=300, timestep_fs=1, steps=20)
    assert prepared == tmp_path / "aimd"
    incar = Incar.from_file(prepared / "INCAR")
    assert {tag: incar[tag] for tag in ("TEBEG", "TEEND", "POTIM", "NSW", "IBRION", "ISIF", "MDALGO", "SMASS")} == {
        "TEBEG": 300, "TEEND": 300, "POTIM": 1, "NSW": 20, "IBRION": 0, "ISIF": 2, "MDALGO": 2, "SMASS": 0,
    }
    assert incar["RANDOM_SEED"] == [248489752, 0, 0]
    assert Poscar.from_file(prepared / "POSCAR").structure.composition == water.composition
    with pytest.raises(ValueError, match="NSW"):
        prepare_aimd_nvt(tmp_path, "H2O-cell.cif", "bad", temperature_kelvin=300, timestep_fs=1, steps=20,
                         incar_overrides={"NSW": 5})


def test_managed_parser_exports_the_observed_temperature_curve():
    from matcreator.aidb.exports import export_record
    from matcreator.aidb.vasp_archive import parse_vasp_result

    record = parse_vasp_result(FIXTURE, include_temperature=True)
    record["record_id"] = "si-historical"
    files, issues = export_record(record, ["temperature"])
    assert not issues
    assert files["temperature"]["filename"] == "temperature-si-historical.html"
    html = files["temperature"]["content"]
    assert "<svg" in html
    assert "<td>1</td><td>1</td><td>300.0</td>" in html
    assert "<td>2</td><td>2</td><td>297.38</td>" in html
    assert "<td>3</td><td>3</td><td>289.76</td>" in html
    assert record["frame_index"] == 2
    assert record["source_metadata"]["vasp_facts"]["sampling"]["stored_scope"] == "final_frame"

    del record["source_metadata"]["vasp_facts"]["sampling"]["temperature_series_kelvin"]
    files, issues = export_record(record, ["temperature"])
    assert not files and "temperature" in issues


@pytest.mark.parametrize("shift", [6.15e-6, 0.01, float("nan")])
def test_md_stress_allows_only_small_asymmetry_corroborated_by_outcar(tmp_path, shift):
    import gzip
    from xml.etree import ElementTree

    from matcreator.aidb.vasp_archive import parse_vasp_result

    for name in ("vasprun.xml", "OUTCAR"):
        (tmp_path / name).write_bytes(gzip.decompress((FIXTURE / (name + ".gz")).read_bytes()))
    xml = tmp_path / "vasprun.xml"
    document = ElementTree.parse(xml)
    rows = document.findall(".//calculation/varray[@name='stress']")[-1].findall("v")
    row = list(map(float, rows[0].text.split()))
    counterpart = float(rows[1].text.split()[0])
    row[1] = counterpart + shift
    rows[0].text = " ".join(map(str, row))
    document.write(xml)
    if shift != 6.15e-6:
        with pytest.raises(AIDBError, match="应力"):
            parse_vasp_result(tmp_path)
        return

    result = parse_vasp_result(tmp_path)
    assert result["stress"][0][1] == result["stress"][1][0] == pytest.approx(-counterpart - shift / 2)
    assert result["provenance"]["stress_symmetrization"] == "mean_of_transposed_xml_components"
