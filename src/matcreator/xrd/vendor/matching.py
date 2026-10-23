"""Peak-position screening, not phase identification or quantitative phase analysis."""
import math
import heapq
import itertools
import time
import json


def match_patterns(inputs, options, *, run_dir=None):
    try:
        from .library import expand_library_slots
    except ImportError:
        from library import expand_library_slots
    def progress(percent, stage, *, indeterminate=False, completed=None, total=None):
        if run_dir is None:
            return
        payload = {"percent": percent, "stage": stage, "indeterminate": indeterminate,
                   "completed": completed, "total": total}
        temp = run_dir / "progress.tmp"
        temp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temp.replace(run_dir / "progress.json")
    progress(5, "解析实验谱与检峰")
    inputs = expand_library_slots(inputs)
    import numpy as np
    from scipy.ndimage import minimum_filter1d, gaussian_filter1d
    from scipy.signal import find_peaks
    from scipy.optimize import linear_sum_assignment

    patterns = [item for item in inputs if item["value"]["kind"] == "pattern"]
    references = [item for item in inputs if item["value"]["kind"] == "reference"]
    libraries = [item for item in inputs if item['value']['kind'] == 'library']
    cifs = [item for item in inputs if item["value"]["kind"] == "cif"]
    if len(patterns) != 1 or not (references or libraries):
        raise ValueError("每次匹配需连接一个实验谱和标准卡片或参考谱库")
    pattern = patterns[0]["value"]
    xy = np.asarray(pattern["points"], dtype=float)
    if xy.ndim != 2 or xy.shape[0] < 20 or xy.shape[1] != 2:
        raise ValueError("请先导入实验谱")
    x, y = xy.T
    step = float(np.median(np.diff(x)))
    # Resample only for peak detection; original samples remain the displayed/source data.
    grid = np.linspace(x[0], x[-1], len(x))
    signal = np.interp(grid, x, y)
    window = max(3, round(1.0 / step) | 1)
    baseline = gaussian_filter1d(minimum_filter1d(signal, size=window), max(1, window / 6))
    corrected = np.maximum(signal - baseline, 0)
    smoothed = gaussian_filter1d(corrected, max(.5, options["smoothing_deg"] / step))
    indices, props = find_peaks(smoothed, prominence=float(smoothed.max()) * options["prominence_fraction"],
                                distance=max(1, round(options["min_peak_distance"] / step)))
    if len(indices) > 1000:
        raise ValueError("检出峰超过 1000；请提高突出度阈值")
    observed = [{"two_theta": float(grid[i]), "intensity": float(smoothed[i]), "prominence": float(p)}
                for i, p in zip(indices, props["prominences"])]
    candidates, explained = [], set()
    wavelength = options["wavelength"]
    tolerance = options["tolerance_deg"]
    try:
        from .library import iter_references, allowed_elements
    except ImportError:
        from library import iter_references, allowed_elements
    allowed = allowed_elements(options.get('library_elements',''))
    scanned, invalid, excluded = 0, 0, 0
    invalid_ids = []
    started = time.monotonic()
    heap = []
    top_n = options.get('library_top_n', 20)
    engine = options.get('library_engine', 'native')
    if engine not in {'native', 'qualx'}:
        raise ValueError('不支持的谱库检索引擎')
    engine_runs = []
    streams = [iter(references)]
    if libraries and engine == 'qualx':
        try:
            from .qualx import search_library
        except ImportError:
            from qualx import search_library
        for index, item in enumerate(libraries):
            progress(10 + 65 * index / len(libraries), "QualX 初筛", indeterminate=True, completed=index, total=len(libraries))
            ids, info = search_library(pattern, item, options, run_dir=run_dir, index=index)
            progress(10 + 65 * (index + 1) / len(libraries), "QualX 初筛完成", completed=index + 1, total=len(libraries))
            engine_runs.append(info)
            streams.append(iter_references(item, allowed, reference_ids=ids))
    else:
        streams.extend(iter_references(item, allowed) for item in libraries)
    total_work = len(references) + (sum(i['candidate_count'] for i in engine_runs) if engine_runs else sum(i['value']['count'] for i in libraries))
    start_percent = 75 if engine_runs else 10
    progress(start_percent, "候选比对", completed=0, total=total_work)
    last_progress = time.monotonic()
    obs_angles = np.array([p['two_theta'] for p in observed])
    obs_weight = np.array([p['prominence'] for p in observed])
    for serial, ref in enumerate(itertools.chain.from_iterable(streams)):
        if serial == 0 or time.monotonic() - last_progress >= .15:
            progress(start_percent + (95 - start_percent) * serial / max(1, total_work), "候选比对", completed=serial, total=total_work)
            last_progress = time.monotonic()
        is_library = serial >= len(references)
        if is_library:
            scanned += 1
            if scanned % 10000 == 0:
                print(f'Full-library screening: {scanned} records', flush=True)
        if ref and ref.get('excluded'):
            excluded += 1
            continue
        if ref is None or ref.get('invalid_record'):
            invalid += 1
            if ref and len(invalid_ids)<50:
                invalid_ids.append(ref['invalid_record'])
            continue
        doc = ref["value"]
        if not is_library and not doc["peaks"]:
            raise ValueError(f"标准卡片尚未导入：{ref['node_id']}")
        peaks = []
        if is_library:
            ratios = wavelength / (2 * doc['_d'])
            valid = (ratios < 1) & (doc['_intensity'] >= options['reference_min_intensity'])
            angles = np.degrees(2 * np.arcsin(np.clip(ratios, 0, 1)))
            valid &= (angles >= x[0]) & (angles <= x[-1])
            ref_angles = angles[valid]
            weights = doc['_intensity'][valid]
        else:
            ref_angles = None
        for p in ([] if is_library else doc["peaks"]):
            ratio = wavelength / (2 * p["d"])
            if ratio >= 1 or p["intensity"] < options["reference_min_intensity"]:
                continue
            angle = math.degrees(2 * math.asin(ratio))
            if x[0] <= angle <= x[-1]:
                peaks.append({**p, "two_theta": angle})
        if not is_library:
            ref_angles = np.array([p['two_theta'] for p in peaks])
            weights = np.array([p['intensity'] for p in peaks])
        matches = []
        if len(ref_angles) and observed:
            delta = np.abs(obs_angles[:, None] - ref_angles[None, :])
            if is_library and not (delta <= tolerance).any():
                continue
            # Dummy columns permit unmatched observations; valid matches always beat a dummy.
            cost = np.where(delta <= tolerance, delta, 1e6)
            cost = np.concatenate([cost, np.full((len(observed), len(observed)), (len(observed) + 1) * tolerance)], axis=1)
            rows, cols = linear_sum_assignment(cost)
            for row, col in zip(rows, cols):
                if col < len(ref_angles) and delta[row, col] <= tolerance:
                    explained.add(int(row))
                    matches.append({"observed_index": int(row), "reference_index": int(col),
                                    "observed": observed[row]["two_theta"], "reference": float(ref_angles[col]),
                                    "delta": float(observed[row]["two_theta"] - ref_angles[col]), "hkl": [] if is_library else peaks[col]["hkl"]})
        if is_library and not matches:
            continue
        mean_delta = float(np.mean([abs(m['delta']) for m in matches])) if matches else None
        ref_coverage = float(sum(weights[m['reference_index']] for m in matches) / weights.sum()) if weights.sum() else 0
        obs_coverage = float(sum(obs_weight[m['observed_index']] for m in matches) / obs_weight.sum()) if obs_weight.sum() else 0
        score = math.sqrt(ref_coverage * obs_coverage) * max(0, 1 - (mean_delta or 0) / (2 * tolerance))
        if is_library and len(heap) >= top_n and (score, len(matches), -serial) <= heap[0][:3]:
            continue
        if is_library:
            ds = doc['_d'][valid]
            peaks = [{'two_theta':float(a),'d':float(d),'intensity':float(i),'hkl':[]} for a,d,i in zip(ref_angles,ds,weights)]
        structures = [{"node_id": c["node_id"], "filename": c["value"]["filename"], "sha256": c["value"]["sha256"]}
                      for c in cifs if c["value"]["reference_node_id"] == ref["node_id"] and c["value"]["text"]]
        candidate = {"node_id": ref["node_id"], "filename": doc["filename"], "metadata": doc["metadata"],
                           "peaks": peaks, "matches": matches, "matched_reference_count": len(matches),
                           'score':score,'reference_intensity_coverage':ref_coverage,'observed_prominence_coverage':obs_coverage,
                           "reference_count": len(peaks), "mean_abs_delta": mean_delta,
                           "cifs": structures, "fit_input_status": "associated_cif_available" if structures else "missing_cif"}
        if is_library:
            heapq.heappush(heap, (score,len(matches),-serial,candidate))
            if len(heap)>top_n:
                heapq.heappop(heap)
        else:
            candidates.append(candidate)
    progress(95, "整理匹配结果", completed=total_work, total=total_work)
    candidates.extend(entry[3] for entry in heap)
    candidates.sort(key=lambda c: (-c['score'], -c['matched_reference_count'], c['node_id']))
    # A full database can collectively explain almost any peak. Report residuals
    # against the leading candidate, not the union of thousands of possible phases.
    if libraries:
        explained = {m['observed_index'] for m in candidates[0]['matches']} if candidates else set()
    # Preserve local extrema for a compact, honest display; full data are in the input snapshot.
    display_indices = {0, len(x)-1}
    for chunk in np.array_split(np.arange(len(x)), min(1000, len(x))):
        display_indices.update((int(chunk[np.argmin(y[chunk])]), int(chunk[np.argmax(y[chunk])])))
    return {"mode": "match", "status": "completed", "pattern": {"filename": pattern["filename"], "metadata": pattern["metadata"],
            "node_id": patterns[0]["node_id"], "sha256": pattern["sha256"], "points": xy[sorted(display_indices)].tolist()},
            "parameters": {k: options[k] for k in ("wavelength", "tolerance_deg", "prominence_fraction", "min_peak_distance", "smoothing_deg", "reference_min_intensity")},
            "observed_peaks": observed, "candidates": candidates,
            'library_search': {'enabled':bool(libraries),'scanned':scanned,'invalid_records':invalid,
                'engine':engine if libraries else 'native',
                'total_records':sum(i['value']['count'] for i in libraries),
                'screened_candidates':sum(i['candidate_count'] for i in engine_runs) if engine_runs else scanned,
                'scored_records':scanned - invalid - excluded,
                'retrieval_seconds':round(sum(i['elapsed_seconds'] for i in engine_runs),3),
                'ranking_method':'oaw_peak_coverage','engine_runs':engine_runs,
                'invalid_record_ids':invalid_ids,'excluded_by_elements':excluded,'allowed_elements':sorted(allowed),
                'top_n':top_n,'elapsed_seconds':round(time.monotonic()-started,3),
                'residual_scope':'top_candidate' if libraries else 'connected_references',
                'libraries':[{'node_id':i['node_id'],'sha256':i['value']['sha256'],'count':i['value']['count'],'metadata':i['value']['metadata']} for i in libraries]},
            "unexplained_peaks": [p for i, p in enumerate(observed) if i not in explained],
            "inputs": [{"node_id": i["node_id"], "revision": i["revision"], "kind": i["value"]["kind"], "sha256": i["value"]["sha256"]} for i in inputs],
            "interpretation": "峰位筛选，不是物相确认、含量分析或精修。使用单波长；Kα 双线、择优取向、峰重叠和零点偏差会影响结果。" +
                ((('QualX3 强峰预筛候选后由 OAW 重新比对排序；此分数不是 QualX FOM，预筛可能漏掉弱相。' if engine == 'qualx' else '全库逐条筛选；') +
                  '分数为参考强度覆盖与实验峰突出度覆盖的几何平均，结合峰位偏差，不是概率。未解释峰仅相对于排名第一候选，不代表多相组合结论。')
                 if libraries else '未解释峰相对于本次连接的标准卡片及当前阈值。')}
