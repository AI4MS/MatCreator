"""Immutable source preparation and result projections for the native-Agent harness."""
import hashlib
import json
import time
import uuid
from types import SimpleNamespace
from .pipeline import build_pipeline_input, latest_match_run, read_owned_run


OBJECTIVE = '(held_out_Rwp_percent / 100)^2 + 0.0025 * number_of_phases; score = 100 / (1 + sqrt(objective))'

CLAIM_SCOPE = '快速混合匹配：固定参考晶胞与谱形，枚举共享零点，仅求非负相贡献和背景；并非 PyWPEM 精修，谱贡献不等于质量分数。'

def _save(path, value):
    temporary = path.with_name(f'.{path.name}.{uuid.uuid4().hex}.tmp')
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
        for attempt in range(8):
            try:
                temporary.replace(path)
                return
            except PermissionError:
                # Windows readers/scanners can briefly hold the destination open.
                # Retry only the atomic commit, never the scientific operation.
                if attempt == 7:
                    raise
                time.sleep(.01 * 2**attempt)
    finally:
        temporary.unlink(missing_ok=True)

def normalize_trial(value, *, arm, iteration, reason, labels):
    profiles = value.get('profiles', {})
    trial = {**value, 'trial_id': f'{arm}-{iteration:03}', 'iteration': iteration,
             'labels': [labels.get(cid, cid) for cid in value['candidate_ids']],
             'reason': reason, 'score': value.get('quality_score')}
    if 'converged' in value.get('fit', {}):
        trial['converged'] = value['fit']['converged']
    if profiles:
        phases = profiles.get('phases', {})
        trial['plot'] = {'observed': profiles.get('observed', []), 'calculated': profiles.get('calculated', []),
                         'contributions': [{'candidate_id': cid, 'label': labels.get(cid, cid), 'points': points}
                                           for cid, points in phases.items()]}
    # Profiles are preview-sampled by the evaluator; full points remain CSV artifacts.
    trial.pop('profiles', None)
    return trial

async def build_multiphase_payload(run_root, options, save_progress):
    owner = options.owner_node_id
    source, manifest, match = read_owned_run(run_root, options.source_match_run_id, owner, stage='search')
    latest = latest_match_run(run_root, owner)
    if not latest or latest['manifest']['run_id'] != options.source_match_run_id:
        raise ValueError('多相模式需要当前检索结果；请重新打开多相模式。')
    indexed = {c['node_id']: c for c in match.get('candidates', [])}
    ids = options.candidate_ids or list(indexed)
    if len(ids) != len(set(ids)) or not 2 <= len(ids) <= 30 or any(cid not in indexed for cid in ids):
        raise ValueError('多相模式需要当前检索中的 2–30 个不同候选。')
    payload = None
    for start in range(0, len(ids), 10):
        opt = SimpleNamespace(workflow_stage='preopt', selected_candidate_ids=ids[start:start+10],
                              workflow_match_run_id=options.source_match_run_id)
        part = await build_pipeline_input(run_root, owner, opt,
            progress=lambda value: save_progress('获取候选 CIF', start + value.get('completed', 0), len(ids)))
        if payload is None:
            payload = {**part, 'candidates': []}
        payload['candidates'].extend(part['candidates'])
    failed = [item for item in payload['candidates'] if item.get('input_error')]
    payload['candidates'] = [item for item in payload['candidates'] if item.get('source_cif')]
    if len(payload['candidates']) < 2:
        raise ValueError('可用 CIF 不足两个；逐候选错误：' + '; '.join(c.get('input_error', '') for c in failed))
    for candidate in payload['candidates']:
        original = indexed[candidate['candidate_id']]
        candidate['formula'] = (original.get('metadata') or {}).get('formula', '')
        candidate['matched_peaks'] = original.get('matches', [])
    payload.update(stage='multiphase', source_owner_node_id=owner, excluded_candidates=failed,
                   objective_description=OBJECTIVE, observed_peaks=match.get('observed_peaks', []),
                   options={'wavelength': payload['wavelength'], 'budget': options.budget,
                            'max_phases': options.max_phases, 'seed': options.seed,
                            'screening_method': 'fixed_profile_nnls_v1', 'max_nfev': options.refinement_max_nfev, 'complexity_penalty': .0025})
    payload['source_files_sha256'] = {name: hashlib.sha256((source / name).read_bytes()).hexdigest()
        for name in ('oaw.json', 'result.json', 'input.json', 'input-snapshot.json')}
    return payload
