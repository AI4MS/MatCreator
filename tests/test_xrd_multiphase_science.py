"""Scientific worker tests; run with the XRD numpy/scipy Python interpreter.

Synthetic patterns verify joint recovery, not phase identification in real data.
"""
import importlib.util
import json
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import patch

MODULE_PATH = Path(__file__).resolve().parents[1] / "src/matcreator/xrd/vendor/multiphase_science.py"
sys.path.insert(0, str(MODULE_PATH.parent))
spec = importlib.util.spec_from_file_location("multiphase_science", MODULE_PATH)
science = importlib.util.module_from_spec(spec)
spec.loader.exec_module(science)

try:
    import numpy as np
    import scipy
except ImportError:
    np = None


@unittest.skipIf(np is None, "run in .venv-xrd for scientific dependencies")
class MultiphaseScienceTests(unittest.TestCase):
    def payload(self):
        x = np.linspace(12, 60, 961)
        self.peaks = {"a": [[18, 100], [30, 60], [44, 45]],
                      "b": [[22, 100], [35, 80], [52, 60]],
                      "c": [[25, 100], [38, 80], [57, 60]]}
        shape = [.035, .18, .35]
        y = 3*science._profile(x, self.peaks["a"], shape, .0015) + science._profile(x, self.peaks["b"], shape, -.001)
        y += 6 + .04*(x-x[0])
        return {"pattern": {"points": np.column_stack((x, y)).tolist()},
                "candidates": [{"candidate_id": identifier, "label": identifier,
                                "source_cif": {"text": "synthetic-"+identifier}} for identifier in self.peaks],
                "options": {"budget": 7, "max_phases": 3, "max_nfev": 60, "seed": 0}}

    def calculated_peaks(self, candidate, *_):
        return {"peaks": self.peaks[candidate["candidate_id"]], "formula": candidate["candidate_id"],
                "cell": [5., 5., 5., 90., 90., 90.], "cif_sha256": science._cif(candidate)[1]}

    def test_fast_screening_avoids_nonlinear_and_keeps_cells_fixed(self):
        payload = self.payload()
        payload['options']['screening_method'] = 'fixed_profile_nnls_v1'
        with tempfile.TemporaryDirectory() as directory, patch.object(science, '_candidate_peaks', self.calculated_peaks), patch.object(science, '_fit_profiles', side_effect=AssertionError('Nonlinear fitting forbidden')):
            result = science.evaluate(payload, ['a', 'b'], directory)
            bad = science.evaluate(payload, ['a', 'c'], directory)
            self.assertLess(result['objective'], bad['objective'])
            self.assertEqual(result['fit']['screening_method'], 'fixed_profile_nnls_v1')
            self.assertFalse(result['fit']['structure_parameters_refined'])
            self.assertTrue(all(p['cell_input'] == p['cell_fitted'] for p in result['phase_contributions']))
            self.assertAlmostEqual(result['objective'], (result['validation']['metrics']['rwp_percent']/100)**2+.005)
            self.assertTrue(science.evaluate(payload, ['a', 'b'], directory)['cache_hit'])

    def test_decision_evidence_keeps_all_reference_peaks_and_performs_no_fit(self):
        payload = self.payload()
        many = [[12+i*.1, 100/(i+1)] for i in range(400)]
        ranges = []
        def reference(candidate, options, angular_range, directory):
            ranges.append(angular_range)
            return {'peaks': many}
        with tempfile.TemporaryDirectory() as directory, patch.object(science, '_candidate_peaks', reference), \
                patch.object(science, '_fit_profiles', side_effect=AssertionError('must not fit')):
            evidence = science.decision_evidence(payload, directory)
        self.assertEqual(len(evidence['candidates']), 3)
        self.assertTrue(all(c['full_reference_peaks'] == many for c in evidence['candidates']))
        self.assertLessEqual(len(evidence['observed_peaks']), 48)
        self.assertEqual(ranges, [[6.8, 65.2]]*3)

    def test_joint_fit_recovers_mixture_and_preserves_original_grid(self):
        payload = self.payload()
        before = json.dumps(payload, sort_keys=True)
        with tempfile.TemporaryDirectory() as directory, patch.object(science, "_candidate_peaks", self.calculated_peaks):
            single = science.evaluate(payload, ["a"], directory)
            self.assertTrue(any(abs(peak['two_theta']-22) < .1 for peak in single['residual_peaks']))
            joint = science.evaluate(payload, ["a", "b"], directory)
            redundant = science.evaluate(payload, ["a", "b", "c"], directory)
            self.assertLess(joint["metrics"]["rwp_percent"], .03)
            self.assertLess(joint["objective"], single["objective"])
            self.assertLess(joint["validation"]["metrics"]["rwp_percent"], single["validation"]["metrics"]["rwp_percent"]*.1)
            self.assertLess(joint["objective"], redundant["objective"])
            self.assertAlmostEqual(joint["phase_contributions"][0]["profile_scale"], 3, delta=.003)
            self.assertAlmostEqual(joint["phase_contributions"][1]["profile_scale"], 1, delta=.003)
            self.assertAlmostEqual(joint["phase_contributions"][0]["isotropic_cell_scale"], 1.0015, delta=.00002)
            self.assertAlmostEqual(joint["phase_contributions"][1]["isotropic_cell_scale"], .999, delta=.00002)
            csv = np.loadtxt(joint["artifacts"]["profile_csv"], delimiter=",", skiprows=1)
            np.testing.assert_allclose(csv[:, :2], payload["pattern"]["points"], rtol=1e-10)
            self.assertTrue(np.all(csv[:, 3]>=0))
            self.assertTrue(np.all(csv[:, 5:]>=0))
            np.testing.assert_allclose(csv[:, 2], csv[:, 3]+csv[:, 5:].sum(axis=1), rtol=1e-9)
            self.assertFalse(joint["validation"]["independent_experiment"])
            self.assertTrue(joint["validation"]["parameters_fit_on_training_only"])
            self.assertEqual(joint["validation"]["method"], "interleaved_angle_block_holdout")
        self.assertEqual(before, json.dumps(payload, sort_keys=True))

    def test_cache_key_covers_data_options_and_cif_hashes(self):
        payload = self.payload()
        with tempfile.TemporaryDirectory() as directory, patch.object(science, "_candidate_peaks", self.calculated_peaks):
            first = science.evaluate(payload, ["a", "b"], directory)
            cached = science.evaluate(payload, ["b", "a"], directory)
            self.assertTrue(cached["cache_hit"])
            self.assertEqual(first["objective"], cached["objective"])
            payload["pattern"]["points"][0][1] += .1
            changed = science.evaluate(payload, ["a", "b"], directory)
            self.assertFalse(changed["cache_hit"])
            self.assertNotEqual(first["provenance"]["pattern_sha256"], changed["provenance"]["pattern_sha256"])
            payload["candidates"][0]["source_cif"]["sha256"] = "bad-hash"
            with self.assertRaisesRegex(ValueError, "SHA256"):
                science.evaluate(payload, ["a"], directory)

    def test_invalid_combinations_and_unordered_grid_rejected(self):
        payload = self.payload()
        with tempfile.TemporaryDirectory() as directory:
            for combo in ([], ["a", "a"], ["unknown"]):
                with self.assertRaises(ValueError):
                    science.evaluate(payload, combo, directory)
            payload["pattern"]["points"][2][0] = payload["pattern"]["points"][1][0]
            with self.assertRaisesRegex(ValueError, "strictly increasing"):
                science.evaluate(payload, ["a"], directory)

    def test_gp_ei_is_data_dependent_deterministic_and_no_repeats(self):
        payload = self.payload()
        evaluated = [{"candidate_ids": [identifier], "status": "completed", "objective": value}
                     for identifier, value in zip(("a", "b", "c"), (.3, .5, .7))]
        proposal = science.propose_bo(payload, evaluated)
        again = science.propose_bo(payload, evaluated)
        self.assertEqual(proposal, again)
        self.assertEqual(proposal["method"], "GP_EI")
        self.assertEqual(proposal["gp"]["kernel"], "Matern52")
        self.assertGreater(proposal["expected_improvement"], 0)
        self.assertNotIn(proposal["candidate_ids"], [row["candidate_ids"] for row in evaluated])
        train = np.array([[1,0,0],[0,1,0],[0,0,1]], dtype=float)
        test = np.array([[1,1,0],[1,0,1],[0,1,1]], dtype=float)
        first, _ = science._gp_expected_improvement(train, [.1,.2,1.], test)
        second, _ = science._gp_expected_improvement(train, [1.,.2,.1], test)
        self.assertFalse(np.allclose(first, second))

    def test_bo_budget_includes_failures_common_starts_and_joint_winner(self):
        payload = self.payload()
        scores = {("a",): .5, ("b",): .8, ("c",): 1., ("a", "b"): .02,
                  ("a", "c"): .4, ("b", "c"): .7, ("a", "b", "c"): .023}
        calls = []
        def evaluate(_payload, combo, _directory):
            calls.append(tuple(sorted(combo)))
            if combo == ["c"]:
                raise ValueError("candidate failed")
            return {"candidate_ids": sorted(combo), "status": "completed", "objective": scores[tuple(sorted(combo))],
                    "quality_score": 80., "metrics": {}, "artifacts": {}, "cache_hit": False}
        with tempfile.TemporaryDirectory() as directory, patch.object(science, "evaluate", evaluate):
            result = science.run_bo(payload, directory)
        self.assertEqual(result["evaluations"], 7)
        self.assertEqual(len(set(calls)), len(calls))
        self.assertEqual(result["best"]["candidate_ids"], ["a", "b"])
        self.assertEqual(result["trace"][2]["status"], "failed")
        self.assertEqual(result["trace"][5]["proposal"]["method"], "GP_EI")
        self.assertEqual(result["trace"][:5][0]["proposal"]["method"], "common_initialization")


if __name__ == "__main__":
    unittest.main()
