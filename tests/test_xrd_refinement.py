"""Stage boundaries, immutable inputs, isolation and independently comparable metrics."""
import base64
import csv
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


from matcreator.xrd.vendor import refinement


def cif(text="original"):
    return {"filename": "../../sample.cif", "text": text,
            "sha256": hashlib.sha256(text.encode()).hexdigest()}


def candidate(identifier="a", **extra):
    return {"candidate_id": identifier, "label": identifier, "search_score": 70,
            "source_cif": cif(), **extra}


class ScienceStub:
    """Exercise the orchestration, not the vendor solver's implementation."""
    def __init__(self):
        self.calls = []
        self.reject = set()
        self.fail = set()
        self.skip_export = set()
        self.bad_grid = set()

    def inspect(self, path):
        assert path.read_text() in {"original", "accepted", "fitted", "tentative"}
        return [4, 4, 4, 90, 90, 90]

    def background(self, points):
        assert Path("original.cif").exists()
        assert not Path("other-candidate-marker").exists()
        Path("other-candidate-marker").touch()
        return 1

    def preopt(self, directory, options):
        self.calls.append(("preopt", directory.name, Path("sample.cif").read_text(), options))
        if directory.name in self.fail:
            Path("sample.cif").write_text("tentative")
            raise RuntimeError("solver crashed")
        accepted = directory.name not in self.reject
        # Deliberate mutation on rejection verifies that orchestration will not trust it.
        Path("sample.cif").write_text("accepted" if accepted else "tentative")
        return {"accepted": accepted, "optimizer_success": True, "reason": "accepted" if accepted else "symmetry changed"}

    def fit(self, directory, options, variance, points):
        self.calls.append(("fit", directory.name, Path("sample.cif").read_text(), options))
        Path("DecomposedComponents").mkdir()
        with Path("DecomposedComponents/fitting_profile.csv").open("w", newline="") as stream:
            csv.writer(stream).writerows([[x + (1 if directory.name in self.bad_grid else 0), y * .9] for x, y in points])
        Path("refined").mkdir()
        if directory.name not in self.skip_export:
            Path("refined/sample_refined.cif").write_text("fitted")
        return {"stop_flag": 3, "rwp_percent": 99, "rp_percent": 99, "iteration": options["iterations"]}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    science = ScienceStub()
    monkeypatch.setattr(refinement, "_load_backend", lambda root: science)
    def run(stage="preopt", candidates=None, **options):
        snapshot = {"stage": stage, "match_run_id": "match-123", "pattern": {"filename": "pattern.txt",
                    "points": [[10 + i * .1, 10 + i] for i in range(30)]},
                    "candidates": candidates or [candidate()]}
        if stage == "fit":
            snapshot["preopt_run_id"] = "preopt-123"
        (tmp_path / "pipeline-input.json").write_text(json.dumps(snapshot), encoding="utf-8")
        return refinement.run_pipeline(tmp_path, tmp_path, {"workflow_stage": stage, **options})
    return tmp_path, science, run


def test_candidates_are_isolated_failures_continue_and_originals_immutable(setup):
    path, science, run = setup
    science.fail.add("candidate-01")
    before = Path.cwd()
    result = run(candidates=[candidate("a"), candidate("../../b")])
    assert Path.cwd() == before
    assert [row["status"] for row in result["candidates"]] == ["failed", "completed"]
    assert (path / "candidate-01/failure.txt").exists()
    assert (path / "candidate-01/original.cif").read_text() == "original"
    assert (path / "candidate-02/original.cif").read_text() == "original"
    assert len(science.calls) == 2
    assert all(call[0] == "preopt" for call in science.calls)
    assert json.loads((path / "progress.json").read_text(encoding="utf-8"))["completed"] == 2
    assert json.loads((path / "result.json").read_text(encoding="utf-8"))["counts"] == {"completed": 1, "failed": 1, "rejected": 0}


def test_rejected_preoptimization_exports_original_and_never_tentative(setup):
    path, science, run = setup
    science.reject.add("candidate-01")
    row = run()["candidates"][0]
    assert row["status"] == "rejected" and row["accepted"] is False
    assert row["report"]["reason"] == "symmetry changed"
    assert base64.b64decode(row["output_cif"]["source_base64"]) == b"original"
    assert row["source_sha256"] == row["starting_sha256"] == row["output_cif"]["sha256"]


def test_fit_uses_only_accepted_preopt_and_rejected_falls_back_to_original(setup):
    path, science, run = setup
    result = run("fit", [candidate("a", preopt={"accepted": True}, starting_cif=cif("accepted")),
                         candidate("b", preopt={"accepted": False}, starting_cif=cif("tentative"))])
    assert [call[2] for call in science.calls] == ["accepted", "original"]
    assert all(call[0] == "fit" for call in science.calls)
    assert result["preopt_run_id"] == "preopt-123"
    assert result["candidates"][0]["starting_source"] == "accepted_preoptimization"
    assert result["candidates"][1]["starting_source"] == "original_cif"
    assert len({row["pattern_sha256"] for row in result["candidates"]}) == 1
    assert all(row["status"] == "completed" and row["converged"] is False for row in result["candidates"])
    # Vendor numbers are intentionally wrong; reported comparison is independently calculated.
    assert all(row["metrics"]["rwp_percent"] == pytest.approx(10) for row in result["candidates"])
    assert all(row["metrics"]["rp_percent"] == pytest.approx(10) for row in result["candidates"])
    assert len(result["candidates"][0]["plot"]["observed"]) == 30


def test_cif_acquisition_failure_does_not_discard_other_candidates(setup):
    path, science, run = setup
    result = run(candidates=[{"candidate_id": "missing", "input_error": "COD unavailable"}, candidate("ok")])
    assert result["candidates"][0]["status"] == "failed"
    assert "COD unavailable" in result["candidates"][0]["error"]
    assert result["candidates"][1]["status"] == "completed"
    assert len(science.calls) == 1


def test_failed_preopt_cannot_silently_fit(setup):
    path, science, run = setup
    row = run("fit", [candidate(preopt={"status": "failed"}, starting_cif=cif("tentative"))])["candidates"][0]
    assert row["status"] == "failed" and not science.calls
    assert "output_cif" not in row


def test_accepted_preopt_without_saved_cif_is_failure(setup):
    path, science, run = setup
    row = run("fit", [candidate(preopt={"accepted": True})])["candidates"][0]
    assert row["status"] == "failed" and not science.calls


def test_hash_mismatch_is_candidate_failure_before_algorithm(setup):
    path, science, run = setup
    source = cif()
    source["sha256"] = "0" * 64
    row = run(candidates=[candidate(source_cif=source)])["candidates"][0]
    assert row["status"] == "failed" and "SHA256" in row["error"]
    assert not science.calls


def test_no_fabricated_refined_cif_when_vendor_export_is_missing(setup):
    path, science, run = setup
    science.skip_export.add("candidate-01")
    row = run("fit")["candidates"][0]
    assert row["status"] == "failed" and "output_cif" not in row
    assert row["metrics"]["rwp_percent"] == pytest.approx(10)


def test_different_profile_grid_cannot_enter_comparable_ranking(setup):
    path, science, run = setup
    science.bad_grid.add("candidate-01")
    row = run("fit")["candidates"][0]
    assert row["status"] == "failed" and "metrics" not in row


@pytest.mark.parametrize("options", [{"preopt_max_nfev": 0}, {"preopt_max_nfev": 2.5},
    {"preopt_coordinate_window": 0}, {"preopt_cell_window": .8}, {"low_angle": 80, "high_angle": 30},
    {"wavelength": float("nan")}])
def test_invalid_parameters_are_rejected_before_scientific_calls(setup, options):
    path, science, run = setup
    with pytest.raises(ValueError):
        run(**options)
    assert not science.calls


def test_plot_downsampling_preserves_peaks_and_common_x_grid():
    observed = [[float(i), float(i == 1234) * 100] for i in range(10000)]
    calculated = [[float(i), float(i == 7890) * 90] for i in range(10000)]
    plot = refinement._plot(observed, calculated)
    assert len(plot["observed"]) <= 5000
    assert [1234., 100.] in plot["observed"]
    assert [7890., 90.] in plot["calculated"]
    assert [p[0] for p in plot["observed"]] == [p[0] for p in plot["calculated"]]
