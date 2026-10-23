"""Isolated, auditable candidate refinement using the bundled OAW_XRDfit engine.

This module is executed in the scientific worker, not in the web service.
Every candidate sees the same unmodified experimental samples and parameters.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import random
import shutil
import sys
try:
    from .engine import engine_root
except ImportError:
    from engine import engine_root
import time
import traceback
import warnings


def _json_default(value):
    if hasattr(value, "tolist"):
        return value.tolist()
    raise TypeError(f"Not JSON serializable: {type(value).__name__}")


def _write_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2,
                                    default=_json_default, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


@contextmanager
def _working_directory(path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _cif_bytes(item):
    if not isinstance(item, dict) or not isinstance(item.get("text"), str) or not item["text"].strip():
        raise ValueError("候选缺少可用 CIF")
    data = item["text"].encode("utf-8")
    if len(data) > 20_000_000:
        raise ValueError("CIF 文件超过 20 MB")
    if item.get("sha256") and _hash(data) != item["sha256"]:
        raise ValueError("CIF SHA256 与输入快照不一致")
    return data


def _pattern(points):
    if not isinstance(points, list) or not 20 <= len(points) <= 2_000_000:
        raise ValueError("实验谱需要 20 至 2,000,000 个数据点")
    result = []
    for point in points:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise ValueError("实验谱必须为两列数据")
        x, y = map(float, point)
        if not math.isfinite(x) or not math.isfinite(y) or y < 0 or (result and x <= result[-1][0]):
            raise ValueError("实验谱必须有限、强度非负且 2θ 严格递增")
        result.append([x, y])
    if not any(point[1] > 0 for point in result):
        raise ValueError("实验谱强度不能全为零")
    return result


def _validated_options(options):
    result = dict(options)
    ranges = {
        "wavelength": (1.540593, .1, 5),
        "iterations": (5, 1, 700),
        "low_angle": (20, 0, 170),
        "high_angle": (70, 0, 180),
        "preopt_max_nfev": (120, 1, 2000),
        "preopt_coordinate_window": (.01, 0, .1),
        "preopt_cell_window": (.35, 0, .5),
    }
    for key, (default, low, high) in ranges.items():
        value = float(options.get(key, default))
        exclusive = key in {"wavelength", "preopt_coordinate_window", "preopt_cell_window"}
        if not math.isfinite(value) or value < low or value > high or (exclusive and value == low):
            raise ValueError(f"参数 {key} 超出允许范围")
        if key in {"iterations", "preopt_max_nfev"}:
            if not value.is_integer():
                raise ValueError(f"参数 {key} 必须为整数")
            value = int(value)
        result[key] = value
    if result["low_angle"] >= result["high_angle"]:
        raise ValueError("晶胞更新角度下限必须小于上限")
    return result


class _PyWPEMBackend:
    def __init__(self, root):
        sys.path.insert(0, str(engine_root().parent))
        import numpy as np
        import pandas as pd
        from pymatgen.io.cif import CifParser
        from OAW_XRDfit.src import WPEM
        from OAW_XRDfit.src.EMBraggOpt.EMBraggSolver import WPEMsolver
        self.np, self.pd, self.CifParser = np, pd, CifParser
        self.wpem, self.solver = WPEM, WPEMsolver

    def inspect(self, path):
        structures = self.CifParser(str(path)).parse_structures(primitive=False)
        if len(structures) != 1 or len(structures[0]) == 0:
            raise ValueError("每个候选 CIF 必须包含一个有效结构")
        structure = structures[0]
        cell = list(map(float, (*structure.lattice.abc, *structure.lattice.angles)))
        if not all(math.isfinite(value) for value in cell) or min(cell[:3]) <= 0 or structure.volume <= 0:
            raise ValueError("CIF 晶胞无效")
        return cell

    def background(self, points):
        self.np.random.seed(0)
        random.seed(0)
        frame = self.pd.DataFrame(points)
        return self.wpem.BackgroundFit(frame, lowAngleRange=17, poly_n=13, bac_split=16, bac_num=300)

    def theoretical_peaks(self, text, options, points):
        from pymatgen.analysis.diffraction.xrd import XRDCalculator
        structure = self.CifParser.from_str(text).parse_structures(primitive=False)[0]
        pattern = XRDCalculator(wavelength=options['wavelength']).get_pattern(structure, two_theta_range=(points[0][0], points[-1][0]))
        return [{'two_theta':float(x),'intensity':float(y)} for x,y in zip(pattern.x,pattern.y)]

    def preopt(self, directory, options):
        from OAW_XRDfit.src.StructureSolver.solver import StructureSolver
        def capture(text, angles, intensities, objective, iteration):
            self.frame_callback(cif=text, peaks=[{'two_theta': float(x), 'intensity': float(y)} for x,y in zip(angles,intensities)],
                metric={'name': '峰残差目标', 'value': objective, 'unit': ''},
                quality=1 / (1 + math.sqrt(max(0, objective))), iteration=iteration, state='best_evaluation')
        StructureSolver.frame_callback = staticmethod(capture)
        try:
            return self.wpem.StructureSolve(
            no_bac_intensity_file="ConvertedDocuments/no_bac_intensity.csv", cif_file="sample.cif",
            wavelength=options["wavelength"], work_dir=str(directory),
            max_nfev=options["preopt_max_nfev"], coordinate_window=options["preopt_coordinate_window"],
            cell_window=options["preopt_cell_window"],
            )
        finally:
            del StructureSolver.frame_callback

    def fit(self, directory, options, variance, points):
        lattice, _, _ = self.wpem.CIFpreprocess(
            filepath="sample.cif", wavelength=options["wavelength"],
            two_theta_range=(points[0][0], points[-1][0]))
        shutil.copy2("output_xrd/sampleHKL.csv", "peak0.csv")
        captured = {}
        original = self.solver.cal_output_result

        def capture(solver):
            values = original(solver)
            captured.update(zip(("rp_percent", "rwp_percent", "iteration", "stop_flag", "lattice"), values))
            return values

        self.solver.cal_output_result = capture
        def frame(iteration, angles, values, cells):
            if iteration != 1 and iteration % max(1, math.ceil(options['iterations'] / 18)):
                return
            from pymatgen.io.cif import CifFile
            cif = CifFile.from_str((directory / 'sample.cif').read_text(encoding='utf-8'))
            for block in cif.data.values():
                for key, value in zip(('a','b','c','alpha','beta','gamma'), cells[0]):
                    block.data[('_cell_length_' if key in ('a','b','c') else '_cell_angle_') + key] = str(float(value))
            calculated = [[float(self.np.asarray(x).ravel()[0]), float(self.np.asarray(y).ravel()[0])] for x,y in zip(angles,values)]
            denominator = sum(p[1] ** 2 / max(p[1], 1) for p in points)
            if len(calculated) != len(points) or any(abs(a[0]-b[0]) > 1e-7 for a,b in zip(points,calculated)):
                raise ValueError('实时谱网格与实验谱不一致')
            rwp = 100 * math.sqrt(sum((a[1]-b[1])**2/max(a[1],1) for a,b in zip(points,calculated)) / denominator)
            self.frame_callback(cif=str(cif), calculated=_plot(points, calculated)['calculated'],
                metric={'name': 'Rwp', 'value': rwp, 'unit': '%'}, quality=1/(1+rwp/100), iteration=iteration, state='iteration')
        self.solver.frame_callback = staticmethod(frame)
        try:
            self.wpem.XRDfit(
                wavelength=[options["wavelength"]], Var=variance, Lattice_constants=[lattice],
                no_bac_intensity_file="ConvertedDocuments/no_bac_intensity.csv", original_file="intensity.csv",
                bacground_file="ConvertedDocuments/bac.csv", bta=.85, asy_C=0, cpu=2, subset_number=11,
                low_bound=options["low_angle"], up_bound=options["high_angle"],
                iter_max=options["iterations"], InitializationEpoch=0,
                work_dir=str(directory), cif_files=[str(directory / "sample.cif")],
                cif_output_dir=str(directory / "refined"))
        finally:
            self.solver.cal_output_result = original
            del self.solver.frame_callback
        if not captured or int(captured["stop_flag"]) == -1:
            raise ValueError("全谱拟合未产生有效求解结果")
        return captured


def _load_backend(root):
    return _PyWPEMBackend(root)


def _profile_metrics(observed, profile_path):
    with profile_path.open(encoding="utf-8", newline="") as stream:
        calculated = [[float(row[0]), float(row[1])] for row in csv.reader(stream) if row]
    if len(calculated) != len(observed):
        raise ValueError("拟合谱采样点数与原始实验谱不一致，不能进行共同指标比较")
    for source, fitted in zip(observed, calculated):
        if not all(math.isfinite(value) for value in fitted) or abs(source[0] - fitted[0]) > 1e-7:
            raise ValueError("拟合谱角度网格与原始实验谱不一致")
    rp = sum(abs(source[1] - fitted[1]) for source, fitted in zip(observed, calculated)) / sum(p[1] for p in observed)
    numerator = sum((source[1] - fitted[1]) ** 2 / max(source[1], 1.0) for source, fitted in zip(observed, calculated))
    denominator = sum(source[1] ** 2 / max(source[1], 1.0) for source in observed)
    metrics = {"rp_percent": rp * 100, "rwp_percent": math.sqrt(numerator / denominator) * 100}
    if not all(math.isfinite(value) for value in metrics.values()):
        raise ValueError("拟合指标不是有限数值，不能进行候选比较")
    return metrics, calculated


def _plot(observed, calculated, budget=5000):
    """Preserve both curves' local extrema when limiting only the UI payload."""
    if len(observed) <= budget:
        return {"observed": observed, "calculated": calculated}
    size = max(1, math.ceil(len(observed) / ((budget - 2) // 4)))
    indices = {0, len(observed) - 1}
    for start in range(0, len(observed), size):
        bucket = range(start, min(start + size, len(observed)))
        for curve in (observed, calculated):
            indices.add(min(bucket, key=lambda i: curve[i][1]))
            indices.add(max(bucket, key=lambda i: curve[i][1]))
    indices = sorted(indices)
    return {"observed": [observed[i] for i in indices], "calculated": [calculated[i] for i in indices]}


def _output(path, filename):
    data = path.read_bytes()
    return {"filename": filename, "source_base64": base64.b64encode(data).decode("ascii"), "sha256": _hash(data)}


def run_pipeline(root, run, options):
    """Execute every selected candidate, keeping failed/rejected outcomes explicit."""
    root, run = Path(root).resolve(), Path(run).resolve()
    snapshot = json.loads((run / "pipeline-input.json").read_text(encoding="utf-8"))
    stage = options.get("workflow_stage")
    if stage not in {"preopt", "fit"} or snapshot.get("stage") != stage:
        raise ValueError("流程阶段与输入快照不一致")
    candidates = snapshot.get("candidates", [])
    if not 1 <= len(candidates) <= 10 or len({str(c.get("candidate_id")) for c in candidates}) != len(candidates):
        raise ValueError("请选择 1 至 10 个不同候选")
    points = _pattern(snapshot.get("pattern", {}).get("points"))
    options = _validated_options(options)
    backend = _load_backend(root)
    result = {"mode": "pipeline", "stage": stage, "match_run_id": snapshot["match_run_id"], "candidates": [],
              "parameters": {key: options[key] for key in ("wavelength", "iterations", "low_angle", "high_angle",
                  "preopt_max_nfev", "preopt_coordinate_window", "preopt_cell_window")},
              "interpretation": "候选分别优化与拟合。预优化仅在原算法接受时采用；全谱拟合更新晶胞与谱形，不独立精修原子坐标。完成或较低残差不等于物相确认。"}
    if snapshot.get("preopt_run_id"):
        result["preopt_run_id"] = snapshot["preopt_run_id"]
    source_files = ("src/WPEM.py", "src/StructureSolver/solver.py", "src/EMBraggOpt/EMBraggSolver.py", "src/Background/BacDeduct.py")
    result["algorithm_sources"] = {name: _hash((engine_root() / name).read_bytes())
                                   for name in source_files if (engine_root() / name).is_file()}
    started = time.monotonic()
    try:
        from .frames import FrameWriter
    except ImportError:
        sys.path.insert(0, str(Path(__file__).parent))
        from frames import FrameWriter
    frames = FrameWriter(run, stage, points)

    def progress(completed, label, active=False):
        _write_json(run / "progress.json", {"percent": 5 + 95 * completed / len(candidates), "stage": label,
            "indeterminate": active, "completed": completed, "total": len(candidates)})

    _write_json(run / "result.json", result)
    for index, candidate in enumerate(candidates):
        identifier = str(candidate["candidate_id"])
        # Candidate IDs and uploaded filenames are never used as disk paths.
        directory = run / f"candidate-{index + 1:02d}"
        directory.mkdir(exist_ok=False)
        row = {"candidate_id": identifier, "label": candidate.get("label", identifier), "status": "failed"}
        if "search_score" in candidate:
            row["search_score"] = candidate["search_score"]
        progress(index, f"{index + 1}/{len(candidates)} · {row['label']} · {'结构预优化' if stage == 'preopt' else '全谱拟合'}", True)
        print(f"[pipeline] {stage} candidate {index + 1}/{len(candidates)}: {identifier}", flush=True)
        warning_context = warnings.catch_warnings(record=True)
        captured_warnings = warning_context.__enter__()
        warnings.simplefilter("always")
        try:
            if candidate.get("input_error"):
                raise ValueError(str(candidate["input_error"]))
            original = _cif_bytes(candidate.get("source_cif"))
            (directory / "original.cif").write_bytes(original)
            row["source_sha256"] = _hash(original)
            starting = original
            if stage == "fit":
                preopt = candidate.get("preopt", {})
                if preopt.get("status") == "failed":
                    raise ValueError("该候选结构预优化失败，需重试后继续")
                if preopt.get("accepted") is True:
                    starting = _cif_bytes(candidate.get("starting_cif"))
                    row["starting_source"] = "accepted_preoptimization"
                else:
                    row["starting_source"] = "original_cif"
                    row["preopt_accepted"] = False
            row["starting_sha256"] = _hash(starting)
            (directory / "starting.cif").write_bytes(starting)
            (directory / "sample.cif").write_bytes(starting)
            with (directory / "intensity.csv").open("w", encoding="utf-8", newline="") as stream:
                csv.writer(stream).writerows(points)
            row["pattern_sha256"] = _hash((directory / "intensity.csv").read_bytes())
            row["cell_before"] = backend.inspect(directory / "sample.cif")
            frame_count = [0]
            def emit(**value):
                # Keep replay bounded; final accepted/fallback output is always stored.
                if frame_count[0] < 20:
                    frames.append(identifier, row['label'], **value)
                    frame_count[0] += 1
            backend.frame_callback = emit
            initial_text = starting.decode('utf-8')
            emit(cif=initial_text, state='initial', peaks=backend.theoretical_peaks(initial_text, options, points) if hasattr(backend, 'theoretical_peaks') else None)
            with _working_directory(directory):
                variance = backend.background(points)
                if stage == "preopt":
                    report = backend.preopt(directory, options)
                    row["report"] = report
                    row["accepted"] = report.get("accepted") is True
                    row["converged"] = bool(report.get("optimizer_success"))
                    row["status"] = "completed" if row["accepted"] else "rejected"
                    if not row["accepted"]:
                        # Explicitly restore the original even if a future solver mutates on rejection.
                        (directory / "sample.cif").write_bytes(original)
                    row["cell_after"] = backend.inspect(directory / "sample.cif")
                    row["output_cif"] = _output(directory / "sample.cif", f"candidate-{index + 1:02d}-{'preoptimized' if row['accepted'] else 'original'}.cif")
                else:
                    solver = backend.fit(directory, options, variance, points)
                    row["report"] = {"solver": solver, "metric_weighting": "w_i = 1 / max(original_intensity_i, 1); same original samples for every candidate",
                                     "fitted_parameters": "lattice and profile; atomic fractional coordinates retained from starting CIF"}
                    row["converged"] = int(solver["stop_flag"]) == 1
                    row["metrics"], calculated = _profile_metrics(points, directory / "DecomposedComponents/fitting_profile.csv")
                    row["plot"] = _plot(points, calculated)
                    exports = list((directory / "refined").glob("*.cif"))
                    if len(exports) != 1:
                        raise ValueError("算法未导出唯一有效的精修晶胞 CIF")
                    row["cell_after"] = backend.inspect(exports[0])
                    row["output_cif"] = _output(exports[0], f"candidate-{index + 1:02d}-fitted.cif")
                    row["status"] = "completed"
            output = base64.b64decode(row['output_cif']['source_base64']).decode('utf-8')
            final = {'cif': output, 'state': 'fallback' if row.get('accepted') is False else 'final'}
            if row.get('plot'):
                rwp = row['metrics']['rwp_percent']
                final.update(calculated=row['plot']['calculated'], metric={'name':'Rwp','value':rwp,'unit':'%'}, quality=1/(1+rwp/100))
            elif hasattr(backend, 'theoretical_peaks'):
                final['peaks'] = backend.theoretical_peaks(output, options, points)
                objective = row['report'].get('final_score' if row['accepted'] else 'initial_score')
                if objective is not None:
                    final.update(metric={'name':'峰残差目标','value':objective,'unit':''}, quality=1/(1+math.sqrt(max(0,objective))))
            frames.append(identifier, row['label'], **final)
        except Exception as exc:
            row.update(status="failed", error=f"{type(exc).__name__}: {exc}")
            frames.append(identifier, row['label'], state='failed', error=row['error'])
            row.pop("output_cif", None)
            (directory / "failure.txt").write_text(traceback.format_exc(), encoding="utf-8")
        finally:
            warning_context.__exit__(None, None, None)
            if captured_warnings:
                row["warnings"] = list(dict.fromkeys(str(item.message) for item in captured_warnings))
                (directory / "warnings.txt").write_text("\n".join(row["warnings"]), encoding="utf-8")
            # BackgroundFit/XRDfit create figures even with Agg; release them between candidates.
            if "matplotlib.pyplot" in sys.modules:
                sys.modules["matplotlib.pyplot"].close("all")
        row["directory"] = directory.name
        _write_json(directory / "result.json", row)
        result["candidates"].append(row)
        result["elapsed_seconds"] = time.monotonic() - started
        _write_json(run / "result.json", result)
        progress(index + 1, "候选处理完成")
    result["status"] = "completed" if any(row["status"] != "failed" for row in result["candidates"]) else "failed"
    result["counts"] = {key: sum(row["status"] == key for row in result["candidates"]) for key in ("completed", "rejected", "failed")}
    _write_json(run / "result.json", result)
    progress(len(candidates), "结构预优化完成" if stage == "preopt" else "全谱拟合完成")
    return result
