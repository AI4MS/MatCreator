"""Post-search PyWPEM joint refinement; never changes the search/BO objective.

Runs only in the isolated science worker. Explicit phase-file lineage avoids
ambiguous HKL-based CIF assignment for isostructural candidates.
"""
from __future__ import annotations
import csv
import inspect
import json
import os
from pathlib import Path
import random
import shutil
import sys
try:
    from .engine import engine_root
except ImportError:
    from engine import engine_root
import traceback

try:
    from .multiphase_science import _snapshot, _cif, _hash, _write, _metrics
except ImportError:
    from multiphase_science import _snapshot, _cif, _hash, _write, _metrics


def fit_combination(payload, combo, output, root, iterations=20):
    import numpy as np
    from pymatgen.io.cif import CifFile
    try:
        from .multiphase_structures import fitted_structures
    except ImportError:
        from multiphase_structures import fitted_structures
    points, candidates, options = _snapshot(payload)
    combo = sorted(combo)
    if not 1 <= len(combo) <= options['max_phases'] or len(set(combo)) != len(combo) or any(c not in candidates for c in combo):
        raise ValueError('Invalid refinement combination')
    if not 1 <= iterations <= 100:
        raise ValueError('PyWPEM iteration budget must be 1–100')
    root = Path(root).resolve()
    sys.path.insert(0, str(engine_root().parent))
    from OAW_XRDfit.src import WPEM
    from OAW_XRDfit.src.EMBraggOpt.EMBraggSolver import WPEMsolver
    import pandas as pd
    provenance = {'engine': 'OAW_XRDfit', 'adapter_sha256': _hash(Path(__file__).read_bytes()),
                  'metric_implementation_sha256': _hash(inspect.getsource(_metrics)),
                  'component_implementation_sha256': _hash(Path(__file__).with_name('multiphase_components.py').read_bytes()),
                  'structure_implementation_sha256': _hash(Path(__file__).with_name('multiphase_structures.py').read_bytes()),
                  'numpy_version': np.__version__, 'pandas_version': pd.__version__,
                  'source_sha256': {str(p.relative_to(engine_root())): _hash(p.read_bytes()) for p in sorted((engine_root() / 'src').rglob('*.py'))},
                  'pattern_sha256': _hash(points.tolist()), 'candidate_ids': combo,
                  'cif_sha256': {c: _cif(candidates[c])[1] for c in combo},
                  'iterations': iterations, 'seed': options['seed'], 'wavelength': options['wavelength']}
    directory = Path(output).resolve() / _hash(provenance)
    directory.mkdir(parents=True, exist_ok=True)
    result_path = directory / 'result.json'
    if result_path.exists():
        return json.loads(result_path.read_text(encoding='utf-8'))
    previous = Path.cwd()
    observer = getattr(WPEMsolver, 'frame_callback', None)
    progress_observer = getattr(WPEMsolver, 'progress_callback', None)
    def progress(stage, iteration=None):
        _write(Path(output).resolve() / 'progress.json', {'stage': stage, 'iteration': iteration, 'candidate_ids': combo})
    original_export = WPEM.export_refined_cifs
    original_report = WPEMsolver.cal_output_result
    captured, report, mapping = {}, {}, []
    result = {'status': 'failed', 'candidate_ids': combo, 'labels': [candidates[c].get('label', c) for c in combo],
              'provenance': provenance, 'directory': str(directory)}
    try:
        os.chdir(directory)
        np.random.seed(options['seed'])
        random.seed(options['seed'])
        with open('intensity.csv', 'w', newline='', encoding='utf-8') as stream:
            csv.writer(stream).writerows(points.tolist())
        progress("背景预处理")
        variance = WPEM.BackgroundFit(pd.DataFrame(points), lowAngleRange=17, poly_n=13, bac_split=16, bac_num=300)
        cells, densities, paths = [], [], []
        for k, identifier in enumerate(combo):
            path = directory / f'phase-{k}.cif'
            path.write_bytes(_cif(candidates[identifier])[0].encode('utf-8'))
            progress(f"CIF 预处理 · 物相 {k + 1}/{len(combo)}")
            cell, _, density = WPEM.CIFpreprocess(filepath=str(path), wavelength=options['wavelength'], two_theta_range=(points[0, 0], points[-1, 0]))
            peak = directory / f'peak{k}.csv'
            shutil.copy2(directory / 'output_xrd' / f'{path.stem}HKL.csv', peak)
            cells.append(list(cell)); densities.append(density); paths.append(str(path))
            mapping.append({'candidate_id': identifier, 'source_cif': str(path), 'source_cif_sha256': _hash(path.read_bytes()),
                            'peak_file': str(peak), 'peak_sha256': _hash(peak.read_bytes())})
        _write(directory / 'phase-map.json', mapping)

        def capture(iteration, angles, values, fitted_cells):
            x = np.asarray(angles, dtype=float).ravel()
            y = np.asarray(values, dtype=float).ravel()
            if x.shape != points[:, 0].shape or not np.allclose(x, points[:, 0], rtol=0, atol=1e-7):
                raise ValueError('PyWPEM changed the original experimental grid')
            if not np.all(np.isfinite(y)):
                raise ValueError('Nonfinite PyWPEM curve')
            captured.update(calculated=y.copy(), iteration=int(iteration), cells=np.asarray(fitted_cells).tolist())
            preview = np.unique(np.linspace(0, len(points)-1, min(2500, len(points))).astype(int))
            _write(Path(output) / 'live.json', {'status': 'running', 'stage': 'pywpem',
                'trial_id': 'pywpem-' + _hash(combo)[:12], 'iteration': int(iteration), 'candidate_ids': combo,
                'labels': result['labels'], 'metrics': _metrics(points[:, 1], y),
                'plot': {'observed': points[preview].tolist(), 'calculated': np.column_stack((x[preview], y[preview])).tolist()},
                'structures': fitted_structures(candidates, combo, captured['cells'], source_kind='pywpem_cell_fit'),
                'description': 'PyWPEM 实时迭代；最终背景更新前的中间状态'})

        def capture_report(solver):
            values = original_report(solver)
            report.update(zip(('rp_percent', 'rwp_percent', 'iteration', 'stop_flag', 'cells'), values))
            return values

        def export(cif_files, fitted_cells, output_dir=None, phase_hkls=None, **kwargs):
            if list(cif_files) != paths or len(fitted_cells) != len(mapping) or phase_hkls is None or len(phase_hkls) != len(mapping):
                raise ValueError('Phase export lineage mismatch')
            destination = Path(output_dir); destination.mkdir(parents=True, exist_ok=True)
            saved = []
            for k, entry in enumerate(mapping):
                peak = Path(entry['peak_file'])
                if _hash(peak.read_bytes()) != entry['peak_sha256'] or not np.array_equal(WPEM._read_hkl_file(peak), phase_hkls[k]):
                    raise ValueError('Phase peak mapping changed')
                cell = np.asarray(fitted_cells[k], dtype=float)
                if cell.shape != (6,) or not np.all(np.isfinite(cell)) or np.any(cell[:3] <= 0) or np.any(cell[3:] <= 0) or np.any(cell[3:] >= 180):
                    raise ValueError('Invalid refined cell')
                cif = CifFile.from_str(Path(entry['source_cif']).read_text(encoding='utf-8'))
                for block in cif.data.values():
                    for key, value in zip(('a', 'b', 'c', 'alpha', 'beta', 'gamma'), cell):
                        block.data[('_cell_length_' if key in ('a', 'b', 'c') else '_cell_angle_') + key] = f'{value:.8f}'
                target = destination / f'phase-{k}-refined.cif'
                target.write_text(str(cif), encoding='utf-8'); saved.append(str(target))
            return saved

        WPEMsolver.frame_callback = staticmethod(capture)
        WPEMsolver.progress_callback = staticmethod(progress)
        progress("初始化精修")
        WPEMsolver.cal_output_result = capture_report
        WPEM.export_refined_cifs = export
        WPEM.XRDfit(wavelength=[options['wavelength']], Var=variance, Lattice_constants=cells,
                    no_bac_intensity_file='ConvertedDocuments/no_bac_intensity.csv', original_file='intensity.csv',
                    bacground_file='ConvertedDocuments/bac.csv', density_list=densities,
                    bta=.85, asy_C=0, cpu=2, subset_number=11,
                    low_bound=20, up_bound=70, iter_max=iterations, InitializationEpoch=0,
                    work_dir=str(directory), cif_files=paths, cif_output_dir=str(directory / 'refined'))
        if not captured or report.get('stop_flag') == -1:
            raise ValueError('PyWPEM produced no valid final curve')
        progress("结果导出")
        # XRDfit updates the background after its last iteration callback.
        # Audit the final exported curve, not the pre-background observer frame.
        final_profile = np.loadtxt(directory / 'DecomposedComponents/fitting_profile.csv', delimiter=',')
        if final_profile.shape != points.shape or not np.all(np.isfinite(final_profile)) or not np.allclose(final_profile[:, 0], points[:, 0], rtol=0, atol=1e-7):
            raise ValueError('Final PyWPEM export changed the experimental grid')
        calculated = final_profile[:, 1]
        metrics = _metrics(points[:, 1], calculated)
        csv_path = directory / 'profile.csv'
        np.savetxt(csv_path, np.column_stack((points, calculated, points[:, 1]-calculated)), delimiter=',',
                   header='two_theta,observed,calculated,residual', comments='', fmt='%.12g')
        preview = np.unique(np.linspace(0, len(points)-1, min(2500, len(points))).astype(int))
        result.update(status='completed', metrics=metrics, stop_flag=int(report['stop_flag']),
                      converged=int(report['stop_flag']) == 1, iteration=int(report['iteration']), cells=captured['cells'],
                      algorithm_report={'rp_percent': float(report['rp_percent']), 'rwp_percent': float(report['rwp_percent'])},
                      plot={'observed': points[preview].tolist(), 'calculated': np.column_stack((points[preview, 0], calculated[preview])).tolist()},
                      profile_csv=str(csv_path), profile_sha256=_hash(csv_path.read_bytes()), phase_map=mapping)
        result['structures'] = fitted_structures(candidates, combo, captured['cells'], source_kind='pywpem_cell_fit')
        try:
            try:
                from .multiphase_components import phase_components
            except ImportError:
                from multiphase_components import phase_components
            result['plot']['contributions'], result['component_audit'] = phase_components(
                directory, combo, result['labels'], points, calculated, preview)
        except (ValueError, OSError, KeyError, AttributeError) as exc:
            result['component_error'] = str(exc)
    except Exception as exc:
        result['status'] = 'failed'
        result['error'] = f'{type(exc).__name__}: {exc}'
        (directory / 'error.log').write_text(traceback.format_exc(), encoding='utf-8')
    finally:
        if progress_observer is None:
            if hasattr(WPEMsolver, 'progress_callback'):
                del WPEMsolver.progress_callback
        else:
            WPEMsolver.progress_callback = staticmethod(progress_observer)
        WPEM.export_refined_cifs = original_export
        WPEMsolver.cal_output_result = original_report
        if observer is None:
            if hasattr(WPEMsolver, 'frame_callback'):
                del WPEMsolver.frame_callback
        else:
            WPEMsolver.frame_callback = staticmethod(observer)
        os.chdir(previous)
        import matplotlib.pyplot as plt
        plt.close('all')
    _write(result_path, result)
    return result


def review_combinations(payload, combinations, output, root, iterations=20, drop_one=True):
    """Same post-search protocol for both incumbents; drop-one tests are descriptive."""
    reviews = []
    for combo in sorted({tuple(sorted(c)) for c in combinations if c}):
        full = fit_combination(payload, combo, output, root, iterations)
        removals = []
        if drop_one and full['status'] == 'completed' and len(combo) > 1:
            for omitted in combo:
                reduced = fit_combination(payload, [c for c in combo if c != omitted], output, root, iterations)
                removals.append({'omitted_candidate_id': omitted, 'status': reduced['status'], 'metrics': reduced.get('metrics'),
                                 'delta_rwp': reduced['metrics']['rwp_percent']-full['metrics']['rwp_percent'] if reduced['status'] == 'completed' else None,
                                 'error': reduced.get('error'), 'directory': reduced['directory']})
        reviews.append({'full': full, 'removals': removals})
    result = {'engine': 'OAW_XRDfit', 'status': 'completed' if reviews and all(r['full']['status'] == 'completed' for r in reviews) else 'failed',
              'reviews': reviews, 'iterations': iterations,
              'scope': '独立于组合搜索评分的 PyWPEM 联合复核。移除后 Rwp 增大表示该候选在当前模型中有解释贡献，不等于主相或含量确认；原子坐标未精修。'}
    _write(Path(output) / 'review.json', result)
    return result


if __name__ == '__main__':
    run = Path(sys.argv[1]).resolve()
    request = json.loads((run / 'pywpem-request.json').read_text(encoding='utf-8'))
    payload = json.loads((run / 'multiphase-input.json').read_text(encoding='utf-8'))
    review_combinations(payload, request['combinations'], run / 'pywpem-review', request['root'], request['iterations'], drop_one=request.get('drop_one', False))
