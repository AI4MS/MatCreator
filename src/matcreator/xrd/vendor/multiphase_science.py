"""Bounded, reproducible multi-phase profile refinement and a real GP/EI baseline.

Atomic positions and calculated reflection intensities are fixed. Refinement
optimizes bounded isotropic cell scales, nonnegative profile scales/background,
and shared zero shift, FWHM and pseudo-Voigt mixing. Profile scales are NOT mass fractions. No optimizer,
including an LLM, can replace these numerical evaluations or their provenance.
Scientific imports are lazy: this module is run in the XRD scientific worker.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import math
from pathlib import Path
import time

ALGORITHM_VERSION = "multiphase-profile-v1"
INTERPRETATION = (
    "多相联合全谱拟合：优化非负相贡献、平滑背景、共享零点和谱形，以及有界各向同性晶胞尺度；"
    "原子分数坐标和反射相对强度保持固定，不是完整 Rietveld 原子精修，谱贡献不是质量分数。"
    "较低残差不独立证明物相存在。角度块留出用于诊断泛化，仍不是独立实验验证。"
)


def _hash(value):
    if not isinstance(value, bytes):
        value = json.dumps(value, sort_keys=True, ensure_ascii=False,
                           separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def _write(path, value):
    path = Path(path)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def _options(payload):
    supplied = dict(payload.get("options", {}))
    if "wavelength" not in supplied and "wavelength" in payload:
        supplied["wavelength"] = payload["wavelength"]
    defaults = dict(wavelength=1.540593, max_phases=3, budget=12, seed=0,
                    zero_shift_bound=.2, fwhm_min=.03, fwhm_max=1.0,
                    background_knots=6, complexity_penalty=.0025,
                    holdout_stride=5, holdout_block_deg=.15, cell_scale_bound=.005,
                    max_nfev=60, min_peak_intensity=.02)
    limits = dict(wavelength=(.1, 5), max_phases=(1, 4), budget=(1, 100),
                  seed=(0, 2**31-1), zero_shift_bound=(.001, 1),
                  fwhm_min=(.005, .5), fwhm_max=(.05, 3),
                  background_knots=(2, 12), complexity_penalty=(0, 1),
                  holdout_stride=(3, 10), holdout_block_deg=(.02, 1), cell_scale_bound=(0, .02),
                  max_nfev=(5, 500), min_peak_intensity=(0, 1))
    integers = {"max_phases", "budget", "seed", "background_knots", "holdout_stride", "max_nfev"}
    result = {}
    for key, default in defaults.items():
        value = float(supplied.get(key, default))
        low, high = limits[key]
        if not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f"Invalid multi-phase option: {key}")
        if key in integers:
            if not value.is_integer():
                raise ValueError(f"{key} must be an integer")
            value = int(value)
        result[key] = value
    if result["fwhm_min"] >= result["fwhm_max"]:
        raise ValueError("fwhm_min must be lower than fwhm_max")
    result['screening_method'] = supplied.get('screening_method', 'nonlinear_v1')
    if result['screening_method'] not in {'nonlinear_v1', 'fixed_profile_nnls_v1'}:
        raise ValueError('Unknown screening method')
    return result


def _snapshot(payload):
    import numpy as np
    points = np.asarray(payload.get("pattern", {}).get("points", []), dtype=float)
    if (points.ndim != 2 or points.shape[1] != 2 or not 30 <= len(points) <= 100_000
            or not np.all(np.isfinite(points)) or np.any(points[:, 1] < 0)
            or not np.all(np.diff(points[:, 0]) > 0) or not np.any(points[:, 1] > 0)):
        raise ValueError("Need 30–100,000 finite nonnegative samples on a strictly increasing 2θ grid")
    candidates = payload.get("candidates", [])
    ids = [str(c.get("candidate_id", "")) for c in candidates]
    if not 1 <= len(ids) <= 30 or len(set(ids)) != len(ids) or any(not identifier for identifier in ids):
        raise ValueError("Need 1–30 uniquely identified candidates")
    return points, dict(zip(ids, candidates)), _options(payload)


def _cif(candidate):
    source = candidate.get("starting_cif") or candidate.get("source_cif")
    if not isinstance(source, dict) or not isinstance(source.get("text"), str) or not source["text"].strip():
        raise ValueError(f"Candidate {candidate.get('candidate_id')} has no CIF snapshot")
    data = source["text"].encode("utf-8")
    if len(data) > 20_000_000:
        raise ValueError("CIF exceeds 20 MB")
    checksum = _hash(data)
    if source.get("sha256") and source["sha256"] != checksum:
        raise ValueError("Candidate CIF SHA256 mismatch")
    return source["text"], checksum


def _candidate_peaks(candidate, options, angle_range, directory):
    """Calculate a CIF pattern once; cache is keyed by text, wavelength and range."""
    text, checksum = _cif(candidate)
    key = _hash([ALGORITHM_VERSION, checksum, options["wavelength"], angle_range,
                 options["min_peak_intensity"]])
    cached = Path(directory) / f"peaks-{key}.json"
    if cached.exists():
        return json.loads(cached.read_text(encoding="utf-8"))
    from pymatgen.io.cif import CifParser
    from pymatgen.analysis.diffraction.xrd import XRDCalculator
    structures = CifParser.from_str(text).parse_structures(primitive=False)
    if len(structures) != 1 or len(structures[0]) == 0 or structures[0].volume <= 0:
        raise ValueError("Each candidate must contain one valid crystal structure")
    structure = structures[0]
    pattern = XRDCalculator(wavelength=options["wavelength"]).get_pattern(
        structure, two_theta_range=tuple(angle_range))
    peaks = [[float(x), float(y)] for x, y in zip(pattern.x, pattern.y)
             if float(y) >= options["min_peak_intensity"]]
    if not peaks:
        raise ValueError("Candidate has no calculated reflections in the fitted angular range")
    if len(peaks) > 10_000:
        raise ValueError("Candidate exceeds 10,000 reflections; use a bounded angular range")
    result = {"peaks": peaks, "cif_sha256": checksum, "formula": structure.composition.reduced_formula,
              "cell": list(map(float, (*structure.lattice.abc, *structure.lattice.angles)))}
    _write(cached, result)
    return result


def _background(x, knots):
    """Nonnegative piecewise-linear basis; no high-degree background polynomial."""
    import numpy as np
    nodes = np.linspace(x[0], x[-1], knots)
    identity = np.eye(knots)
    return np.column_stack([np.interp(x, nodes, identity[:, k]) for k in range(knots)])


def decision_evidence(payload, outputdir):
    """Prepare complete evaluator reference peaks outside the server event loop.

    This computes no combination objective and performs no fitted initialization.
    The same CIF/wavelength/range cache is reused by this arm's later evaluations.
    """
    import numpy as np
    from scipy.signal import find_peaks
    points, candidates, options = _snapshot(payload)
    directory = Path(outputdir)
    directory.mkdir(parents=True, exist_ok=True)
    margin = 5*options['fwhm_max'] + options['zero_shift_bound']
    angle_range = [max(.01, float(points[0, 0])-margin), min(179.9, float(points[-1, 0])+margin)]
    indices, _ = find_peaks(points[:, 1], prominence=float(points[:, 1].max())*.012)
    strongest = sorted(indices, key=lambda i: points[i, 1], reverse=True)[:48]
    observed = [{'two_theta': float(points[i, 0]), 'intensity': float(100*points[i, 1]/points[:, 1].max())}
                for i in strongest]
    weight = sum(p['intensity'] for p in observed)
    descriptors = []
    for cid, candidate in candidates.items():
        reference = _candidate_peaks(candidate, options, angle_range, directory)
        covered = sum(p['intensity'] for p in observed if min(abs(r[0]-p['two_theta']) for r in reference['peaks']) <= .15)
        descriptors.append({'candidate_id': cid, 'full_reference_peaks': reference['peaks'],
                            'initial_peak_coverage_score': 100*covered/weight if weight else 0.})
    return {'candidates': descriptors, 'observed_peaks': observed, 'angle_range': angle_range,
            'min_peak_intensity': options['min_peak_intensity'],
            'initial_score_definition': '100 * intensity coverage of the up-to-48 detected observed peaks within 0.15 degrees of a reference reflection; heuristic only, not the fitted objective',
            'preprocessing': 'CIF reflection calculation only; zero fitted combinations'}


def _profile(x, peaks, parameters, cell_scale=0.):
    import numpy as np
    zero, fwhm, eta = parameters
    peaks = np.asarray(peaks, dtype=float).copy()
    # Bragg-law shift for isotropic a,b,c scaling; fractional positions/angles stay fixed.
    sine = np.sin(np.deg2rad(peaks[:, 0]/2))/(1+cell_scale)
    valid = sine < 1
    peaks = peaks[valid]
    peaks[:, 0] = np.rad2deg(2*np.arcsin(sine[valid]))
    profile = np.zeros(len(x))
    # Chunking bounds peak-by-sample memory; the experimental grid is untouched.
    for start in range(0, len(peaks), 64):
        chunk = peaks[start:start+64]
        distance = (x[:, None] - chunk[:, 0][None, :] - zero) / fwhm
        shape = ((1-eta) * math.sqrt(4*math.log(2)/math.pi) * np.exp(-4*math.log(2)*distance**2)
                 + eta * 2/math.pi / (1+4*distance**2)) / fwhm
        profile += (shape * chunk[:, 1]).sum(axis=1)
    return profile


def _metrics(y, calculated):
    import numpy as np
    weights = 1 / np.maximum(y, 1)
    denominator = float(np.sum(weights*y*y))
    if denominator <= 0 or float(np.sum(y)) <= 0:
        raise ValueError("Metric subset has no observed signal")
    squared = float(np.sum(weights*(y-calculated)**2))
    return {"rwp_percent": 100*math.sqrt(squared/denominator),
            "rp_percent": 100*float(np.sum(np.abs(y-calculated)))/float(np.sum(y)),
            "weighted_squared_error": squared, "weighted_denominator": denominator}


def _fit_profiles(points, peaks, options, mask, initial=None):
    """Variable projection: bounded nonlinear shape + NNLS amplitudes at every step."""
    import numpy as np
    from scipy.optimize import least_squares, nnls
    x, y = points.T
    background = _background(x, options["background_knots"])
    root_weight = 1 / np.sqrt(np.maximum(y, 1))
    observed = y[mask]*root_weight[mask]
    refine_cell = options["cell_scale_bound"] > 0
    low = np.array([-options["zero_shift_bound"], options["fwhm_min"], 0.] +
                   ([-options["cell_scale_bound"]]*len(peaks) if refine_cell else []))
    high = np.array([options["zero_shift_bound"], options["fwhm_max"], 1.] +
                    ([options["cell_scale_bound"]]*len(peaks) if refine_cell else []))

    def project(parameters):
        profiles = np.column_stack([_profile(x, phase, parameters[:3], parameters[3+k] if refine_cell else 0.)
                                    for k, phase in enumerate(peaks)])
        design = np.column_stack((profiles, background))
        weighted = design[mask]*root_weight[mask, None]
        norms = np.maximum(np.linalg.norm(weighted, axis=0), 1e-12)
        scales, _ = nnls(weighted/norms, observed, maxiter=1000)
        scales /= norms
        return design, scales

    def residual(parameters):
        design, scales = project(parameters)
        return (design[mask] @ scales - y[mask])*root_weight[mask]

    starts = [initial] if initial is not None else [[0., width, .5]+([0.]*len(peaks) if refine_cell else []) for width in (.1, .25)]
    best = None
    for start in starts:
        fitted = least_squares(residual, np.clip(start, low+1e-8, high-1e-8), bounds=(low, high),
                               max_nfev=options["max_nfev"], x_scale="jac", ftol=1e-7, xtol=1e-7, gtol=1e-7)
        if best is None or np.sum(fitted.fun**2) < np.sum(best.fun**2):
            best = fitted
    design, scales = project(best.x)
    return {"parameters": best.x, "scales": scales, "design": design,
            "calculated": design @ scales, "success": bool(best.success),
            "nfev": int(best.nfev), "termination": str(best.message)}


_FAST_CACHE = {}


def _fit_fast_profiles(points, peaks, options, train):
    """Fixed profiles, train-selected shared shift, nonnegative linear weights."""
    import numpy as np
    from scipy.optimize import nnls
    from scipy.signal import find_peaks, peak_widths
    key = _hash([points.tolist(), {k: options[k] for k in ('fwhm_min', 'fwhm_max', 'zero_shift_bound', 'background_knots', 'holdout_stride', 'holdout_block_deg', 'seed')}])
    cache = _FAST_CACHE.get(key)
    if cache is None:
        if len(_FAST_CACHE) >= 2:
            _FAST_CACHE.clear()
        x, y = points.T
        yt = np.interp(x, x[train], y[train])
        positions, _ = find_peaks(yt, prominence=max(yt)*.05)
        widths = peak_widths(yt, positions, rel_height=.5)[0]*np.median(np.diff(x))
        width = float(np.clip(np.median(widths), options['fwhm_min'], options['fwhm_max'])) if len(widths) else float(np.clip(.15, options['fwhm_min'], options['fwhm_max']))
        cache = {'width': width, 'profiles': {}, 'background': _background(x, options['background_knots'])}
        _FAST_CACHE[key] = cache
    x, y = points.T
    zeroes = np.linspace(-options['zero_shift_bound'], options['zero_shift_bound'], 9)
    grids = []
    for phase in peaks:
        phasekey = _hash(phase)
        if phasekey not in cache['profiles']:
            base = _profile(x, phase, [0, cache['width'], .5], 0)
            cache['profiles'][phasekey] = [np.interp(x-z, x, base, left=0, right=0) for z in zeroes]
        grids.append(cache['profiles'][phasekey])
    weight = 1/np.sqrt(np.maximum(y, 1))
    def solve(index, mask):
        design = np.column_stack([grid[index] for grid in grids]+[cache['background']])
        a = design[mask]*weight[mask, None]
        norms = np.maximum(np.linalg.norm(a, axis=0), 1e-12)
        scales, _ = nnls(a/norms, y[mask]*weight[mask], maxiter=1000)
        scales /= norms
        calculated = design@scales
        return {'parameters': np.array([zeroes[index], cache['width'], .5]+[0.]*len(peaks)), 'scales': scales,
                'design': design, 'calculated': calculated, 'success': True, 'nfev': 9,
                'termination': 'Fixed-profile NNLS solved; not nonlinear or structural refinement'}
    best, index, loss = None, None, float('inf')
    for i in range(len(zeroes)):
        fit = solve(i, train)
        error = np.sum(((fit['calculated']-y)[train]*weight[train])**2)
        if error < loss:
            best, index, loss = fit, i, error
    return best, solve(index, np.ones(len(points), dtype=bool))


def evaluate(payload, combo_ids, outputdir):
    """Evaluate one combination; all engines must call exactly this evaluator.

    Result keys: candidate_ids, objective (minimize), quality_score, metrics,
    validation, phase_contributions, fit, profiles, provenance, artifacts.
    Completed cache hits are scientifically identical and do not rerun fitting.
    """
    import numpy as np
    import scipy
    started = time.monotonic()
    points, candidates, options = _snapshot(payload)
    combo = sorted(str(identifier) for identifier in combo_ids)
    if not 1 <= len(combo) <= options["max_phases"] or len(set(combo)) != len(combo):
        raise ValueError("Combination must contain distinct candidates within max_phases")
    if any(identifier not in candidates for identifier in combo):
        raise ValueError("Combination contains an unknown candidate ID")
    directory = Path(outputdir).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    cif_hashes = {identifier: _cif(candidates[identifier])[1] for identifier in combo}
    science_options = {key: value for key, value in options.items() if key not in {"budget", "max_phases"}}
    provenance = {"algorithm": options["screening_method"], "algorithm_sha256": _hash(Path(__file__).read_bytes()),
                  "pattern_sha256": _hash(points.tolist()), "candidate_cif_sha256": cif_hashes,
                  "parameters": science_options, "numpy_version": np.__version__, "scipy_version": scipy.__version__}
    key = _hash(provenance)
    result_path = directory / f"combination-{key}.json"
    if result_path.exists():
        result = json.loads(result_path.read_text(encoding="utf-8"))
        result["cache_hit"] = True
        return result
    # Include peaks just outside the observed interval whose profiles may enter it.
    margin = 5*options["fwhm_max"] + options["zero_shift_bound"]
    angular_range = [max(.01, float(points[0, 0])-margin), min(179.9, float(points[-1, 0])+margin)]
    phase_patterns = [_candidate_peaks(candidates[identifier], options, angular_range, directory) for identifier in combo]
    peaks = [phase["peaks"] for phase in phase_patterns]
    indices = np.arange(len(points))
    block_ids = np.floor((points[:, 0]-points[0, 0])/options["holdout_block_deg"]).astype(int)
    holdout = block_ids % options["holdout_stride"] == options["seed"] % options["holdout_stride"]
    train = ~holdout
    if not np.any(points[holdout, 1] > 0) or not np.any(points[train, 1] > 0):
        raise ValueError("Both training and holdout subsets need observed signal")
    fast = options['screening_method'] == 'fixed_profile_nnls_v1'
    if fast:
        validation_fit, full_fit = _fit_fast_profiles(points, peaks, options, train)
    else:
        validation_fit = _fit_profiles(points, peaks, options, train)
    validation_metrics = _metrics(points[holdout, 1], validation_fit["calculated"][holdout])
    if not fast:
        full_fit = _fit_profiles(points, peaks, options, np.ones(len(points), dtype=bool), validation_fit["parameters"])
    metrics = _metrics(points[:, 1], full_fit["calculated"])
    from scipy.signal import find_peaks
    residual = np.maximum(points[:, 1]-full_fit['calculated'], 0)
    peak_indices, _ = find_peaks(residual, prominence=max(float(points[:, 1].max())*.01, 1e-8))
    residual_peaks = [{'two_theta': float(points[i, 0]), 'unexplained_intensity': float(residual[i])}
                      for i in sorted(peak_indices, key=lambda i: residual[i], reverse=True)[:12]]
    objective = (validation_metrics["rwp_percent"]/100)**2 + options["complexity_penalty"]*len(combo)
    phase_curves = full_fit["design"][:, :len(combo)] * full_fit["scales"][:len(combo)]
    background = full_fit["design"][:, len(combo):] @ full_fit["scales"][len(combo):]
    integrals = np.array([np.trapz(phase_curves[:, k], points[:, 0]) for k in range(len(combo))])
    total = float(np.sum(integrals))
    phase_results = []
    for k, identifier in enumerate(combo):
        others = np.max(np.delete(phase_curves, k, axis=1), axis=1) if len(combo)>1 else np.zeros(len(points))
        unique = (phase_curves[:, k] > 2*others) & (phase_curves[:, k] > max(float(phase_curves[:, k].max())*.1, 1e-8))
        lattice_scale = float(full_fit["parameters"][3+k]) if options["cell_scale_bound"]>0 else 0.
        cell = phase_patterns[k]["cell"]
        phase_results.append({"candidate_id": identifier, "label": candidates[identifier].get("label", identifier),
                              "formula": phase_patterns[k]["formula"], "cif_sha256": cif_hashes[identifier],
                              "profile_scale": float(full_fit["scales"][k]),
                              "profile_area_fraction": float(integrals[k]/total) if total>0 else 0.,
                              "dominant_profile_samples": int(np.sum(unique)),
                              "cell_input": cell, "cell_fitted": [value*(1+lattice_scale) if index<3 else value for index,value in enumerate(cell)],
                              "isotropic_cell_scale": 1+lattice_scale,
                              "fraction_kind": "fitted_profile_area_not_mass_fraction"})
    csv_path = directory / f"profile-{key}.csv"
    matrix = np.column_stack((points, full_fit["calculated"], background,
                              points[:, 1]-full_fit["calculated"], phase_curves))
    np.savetxt(csv_path, matrix, delimiter=",", header="two_theta,observed,calculated,background,residual,"+
               ",".join(f"phase_{k+1}" for k in range(len(combo))), comments="", fmt="%.12g")
    # Uniform preview thinning only affects UI transport; metrics and CSV use all samples.
    preview = np.unique(np.linspace(0, len(points)-1, min(len(points), 2500)).astype(int))
    result = {"candidate_ids": combo, "status": "completed", "objective": float(objective),
              "objective_definition": "(held_out_Rwp_percent / 100)^2 + complexity_penalty * number_of_phases",
              "quality_score": 100/(1+math.sqrt(max(0, objective))), "metrics": metrics,
              "validation": {"method": "interleaved_angle_block_holdout", "training_count": int(np.sum(train)),
                             "holdout_count": int(np.sum(holdout)), "stride": options["holdout_stride"],
                             "block_width_deg": options["holdout_block_deg"],
                             "holdout_indices_sha256": _hash(indices[holdout].tolist()),
                             "parameters_fit_on_training_only": True, "metrics": validation_metrics,
                             "training_metrics": _metrics(points[train, 1], validation_fit["calculated"][train]),
                             "independent_experiment": False},
              "phase_contributions": phase_results, "residual_peaks": residual_peaks,
              "fit": {"zero_shift_deg": float(full_fit["parameters"][0]),
                      "fwhm_deg": float(full_fit["parameters"][1]), "lorentz_fraction": float(full_fit["parameters"][2]),
                      "converged": bool(full_fit["success"] and validation_fit["success"]),
                      "termination": full_fit["termination"], "nfev": full_fit["nfev"],
                      "screening_method": options["screening_method"],
                      "structure_parameters_refined": "bounded_isotropic_cell_only" if not fast and options["cell_scale_bound"]>0 else False,
                      "reflection_relative_intensities_fixed": True, "atomic_fractional_coordinates_fixed": True,
                      "parameter_bounds": {"zero_shift_deg": [-options["zero_shift_bound"],options["zero_shift_bound"]],
                                           "fwhm_deg": [float(full_fit["parameters"][1])]*2 if fast else [options["fwhm_min"],options["fwhm_max"]],
                                           "lorentz_fraction": [.5,.5] if fast else [0,1],
                                           "isotropic_cell_scale": [1,1] if fast else [1-options["cell_scale_bound"],1+options["cell_scale_bound"]]},
                      "background_knots": options["background_knots"]},
              "profiles": {"observed": points[preview].tolist(),
                           "calculated": np.column_stack((points[preview, 0], full_fit["calculated"][preview])).tolist(),
                           "background": np.column_stack((points[preview, 0], background[preview])).tolist(),
                           "phases": {identifier: np.column_stack((points[preview, 0], phase_curves[preview, k])).tolist()
                                      for k, identifier in enumerate(combo)}},
              "provenance": provenance, "artifacts": {"profile_csv": str(csv_path), "result_json": str(result_path),
                                                       "profile_csv_sha256": _hash(csv_path.read_bytes())},
              "interpretation": "快速混合匹配：固定参考晶胞与谱形，枚举共享零点，仅解非负谱贡献和背景；不是 PyWPEM 精修，谱面积不是质量分数。" if fast else INTERPRETATION, "cache_hit": False,
              "elapsed_seconds": time.monotonic()-started}
    try:
        from .multiphase_structures import fitted_structures
    except ImportError:
        from multiphase_structures import fitted_structures
    result['structures'] = fitted_structures(candidates, combo, [p['cell_fitted'] for p in phase_results], source_kind='input' if fast else 'profile_cell_fit')
    _write(result_path, result)
    return result


def initial_combinations(payload):
    """Shared initialization for LLM and BO; caller must apply the same budget."""
    _, candidates, options = _snapshot(payload)
    ids = list(candidates)
    initial = [[identifier] for identifier in ids[:min(3, len(ids))]]
    for size in range(2, min(options["max_phases"], 3, len(ids))+1):
        initial.append(ids[:size])
    return initial[:options["budget"]]


def _gp_expected_improvement(train_x, losses, test_x):
    """Matern-5/2 GP, ML hyperparameters, analytic expected improvement (minimize)."""
    import numpy as np
    from scipy.linalg import cho_factor, cho_solve
    from scipy.optimize import minimize
    from scipy.special import ndtr
    train_x, test_x, losses = map(np.asarray, (train_x, test_x, losses))
    location = float(np.mean(losses))
    scale = max(float(np.std(losses)), 1e-6)
    y = (losses-location)/scale
    squared_train = np.sum((train_x[:, None]-train_x[None, :])**2, axis=2)
    squared_test = np.sum((test_x[:, None]-train_x[None, :])**2, axis=2)

    def kernel(squared, length):
        distance = np.sqrt(5*squared)/length
        return (1+distance+distance**2/3)*np.exp(-distance)

    def model(parameters):
        length, noise = np.exp(parameters)
        covariance = kernel(squared_train, length) + np.eye(len(y))*noise
        factor = cho_factor(covariance, lower=True, check_finite=False)
        alpha = cho_solve(factor, y, check_finite=False)
        loss = .5*float(y@alpha)+float(np.log(np.diag(factor[0])).sum())+.5*len(y)*math.log(2*math.pi)
        return loss, factor, alpha

    optimized = minimize(lambda parameters: model(parameters)[0], np.log([1., .0001]),
                         method="L-BFGS-B", bounds=[(math.log(.2), math.log(10)), (math.log(1e-8), math.log(.1))])
    _, factor, alpha = model(optimized.x)
    covariance = kernel(squared_test, float(np.exp(optimized.x[0])))
    mean = covariance@alpha
    variance = np.maximum(1-np.sum(covariance*cho_solve(factor, covariance.T, check_finite=False).T, axis=1), 1e-12)
    std = np.sqrt(variance)
    improvement = float(np.min(y))-mean-.01
    z = improvement/std
    ei = improvement*ndtr(z)+std*np.exp(-.5*z*z)/math.sqrt(2*math.pi)
    return ei, {"kernel": "Matern52", "length_scale": float(np.exp(optimized.x[0])),
                "noise_variance": float(np.exp(optimized.x[1])), "acquisition": "expected_improvement",
                "hyperparameter_optimizer_converged": bool(optimized.success), "loss_mean": location, "loss_scale": scale}


def propose_bo(payload, evaluated):
    """Return one unevaluated subset using GP/EI; does not spend a fit budget."""
    import numpy as np
    _, candidates, options = _snapshot(payload)
    ids = list(candidates)
    universe = [tuple(combo) for size in range(1, min(options["max_phases"], len(ids))+1)
                for combo in itertools.combinations(sorted(ids), size)]
    if len(universe)>50_000:
        raise ValueError("BO subset space exceeds 50,000; reduce candidates or max_phases")
    used = {tuple(sorted(item["candidate_ids"])) for item in evaluated}
    remaining = [combo for combo in universe if combo not in used]
    if not remaining:
        return None
    valid = [item for item in evaluated if item.get("status") == "completed" and math.isfinite(item.get("objective", math.inf))]
    if len(valid)<2:
        # Explicit initialization, never labelled as a GP step.
        return {"candidate_ids": list(remaining[0]), "method": "initialization", "reason": "Need at least two finite evaluations for GP"}
    encode = lambda combo: [float(identifier in combo) for identifier in ids]
    train_x = np.array([encode(item["candidate_ids"]) for item in valid])
    test_x = np.array([encode(combo) for combo in remaining])
    ei, gp = _gp_expected_improvement(train_x, [item["objective"] for item in valid], test_x)
    # Seeded tie order ensures exact reproducibility without an index-order preference.
    order = np.random.default_rng(options["seed"]+len(evaluated)).permutation(len(remaining))
    selected = int(order[np.argmax(ei[order])])
    return {"candidate_ids": list(remaining[selected]), "method": "GP_EI", "expected_improvement": float(ei[selected]), "gp": gp}


def run_bo(payload, outputdir, initial=None, on_evaluation=None):
    """Genuine BO_baseline using the same immutable evaluator and common starts."""
    _, _, options = _snapshot(payload)
    directory = Path(outputdir).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    trace = []
    starts = initial_combinations(payload) if initial is None else initial
    seen = set()
    while len(trace)<options["budget"]:
        proposal = None
        while starts:
            combo = list(starts[0])
            starts = starts[1:]
            if tuple(sorted(combo)) not in seen:
                proposal = {"candidate_ids": combo, "method": "common_initialization"}
                break
        if proposal is None:
            proposal = propose_bo(payload, trace)
        if proposal is None:
            break
        combo = proposal["candidate_ids"]
        seen.add(tuple(sorted(combo)))
        try:
            result = evaluate(payload, combo, directory / "evaluations")
            row = {key: result[key] for key in ("candidate_ids", "status", "objective", "quality_score", "metrics", "artifacts", "cache_hit")}
        except Exception as error:
            row = {"candidate_ids": sorted(combo), "status": "failed", "error": str(error)}
        row.update(iteration=len(trace)+1, proposal=proposal)
        trace.append(row)
        valid = [item for item in trace if item["status"]=="completed"]
        best = min(valid, key=lambda item: item["objective"]) if valid else None
        report = {"engine": "BO_baseline", "trace": trace, "best": best,
                  "evaluations": len(trace), "budget": options["budget"], "seed": options["seed"],
                  "common_initialization": initial_combinations(payload) if initial is None else initial,
                  "status": "running", "interpretation": INTERPRETATION}
        _write(directory / "bo-result.json", report)
        if on_evaluation is not None:
            on_evaluation(row, report)
    report["status"] = "completed"
    report["stop_reason"] = "evaluation_budget" if len(trace)>=options["budget"] else "subset_space_exhausted"
    _write(directory / "bo-result.json", report)
    return report
