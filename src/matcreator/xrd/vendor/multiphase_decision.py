"""Autoregressive subset construction; only legality/reachability masks actions."""
from __future__ import annotations

import hashlib
import json
import math

PROTOCOL_VERSION = 'jev_autoregressive_v2'


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def _text(value, limit=160):
    value = str(value or '')
    return '' if any(c in value for c in ('\\', '/', '\n', '\r', '\x00')) else value[:limit]


def _metrics(value):
    return {k: value[k] for k in ('rwp_percent', 'rp_percent', 'rmse', 'r_squared')
            if _number(value.get(k)) is not None} if isinstance(value, dict) else {}


def _peaks(values, intensity_key='intensity', limit=None):
    result = []
    for peak in values if isinstance(values, list) else []:
        if not isinstance(peak, dict) or _number(peak.get('two_theta')) is None:
            continue
        item = {'two_theta': round(peak['two_theta'], 6)}
        for name in (intensity_key, 'prominence'):
            if _number(peak.get(name)) is not None:
                item[name] = round(peak[name], 6)
        result.append(item)
    return result if limit is None else sorted(result, key=lambda p: -p.get(intensity_key, 0))[:limit]


def _trial(value, pool):
    result = {'candidate_ids': [cid for cid in value.get('candidate_ids', []) if cid in pool],
              'status': value.get('status') if value.get('status') in {'completed', 'failed'} else 'unknown'}
    for name in ('score', 'objective'):
        if _number(value.get(name)) is not None:
            result[name] = value[name]
    result['metrics'] = _metrics(value.get('metrics', {}))
    result['validation'] = {'metrics': _metrics(value.get('validation', {}).get('metrics', {}))}
    result['residual_peaks'] = _peaks(value.get('residual_peaks', []), 'unexplained_intensity')
    result['phase_contributions'] = []
    for phase in value.get('phase_contributions', []):
        if phase.get('candidate_id') in pool:
            item = {'candidate_id': phase['candidate_id'], 'fraction_kind': 'fitted_profile_area_not_mass_fraction'}
            for name in ('profile_scale', 'profile_area_fraction', 'isotropic_cell_scale', 'dominant_profile_samples'):
                if _number(phase.get(name)) is not None:
                    item[name] = phase[name]
            result['phase_contributions'].append(item)
    converged = value.get('converged', value.get('fit', {}).get('converged'))
    if isinstance(converged, bool):
        result['converged'] = converged
    return result


def legal_actions(ids, draft, tried, max_phases):
    """Every ADD with an untried reachable completion; no score is consulted."""
    if len(draft) != len(set(draft)) or any(cid not in ids for cid in draft) or len(draft) > max_phases:
        raise ValueError('Draft must contain distinct supplied candidates within max_phases.')
    chosen = set(draft)
    tried = {tuple(sorted(combo)) for combo in tried}
    def reachable(selected):
        total = sum(math.comb(len(ids)-len(selected), n) for n in range(max_phases-len(selected)+1))
        used = sum(selected.issubset(combo) for combo in tried)
        return total > used
    actions = {}
    if draft and tuple(sorted(draft)) not in tried:
        actions['submit'] = {'kind': 'submit', 'candidate_ids': list(draft),
                             'description': 'Submit the current draft unchanged for numerical fitting.'}
    if len(draft) < max_phases:
        for i, cid in enumerate(ids):
            if cid not in chosen and reachable(chosen | {cid}):
                actions[f'add:P{i+1}'] = {'kind': 'add', 'candidate_id': cid, 'description': f'Add P{i+1} to the draft.'}
    return actions


def build_decision(state, payload, *, objective, claim_scope, draft_candidate_ids=None, evidence=None):
    """Pure projection used both for issuing reservations and replay validation."""
    options = state.get('options', {})
    candidates = payload.get('candidates') or state.get('pool', [])
    ids = [c['candidate_id'] for c in candidates]
    if len(ids) != len(set(ids)) or len(ids) > 30:
        raise ValueError('Decisions require at most 30 distinct candidates.')
    pool = set(ids)
    max_phases = min(max(1, int(options.get('max_phases', 3))), 4, len(ids))
    draft = list(draft_candidate_ids or [])
    trials = state.get('trials', [])
    tried = {tuple(sorted(t.get('candidate_ids', []))) for t in trials}
    space_size = sum(math.comb(len(ids), n) for n in range(1, max_phases+1))
    remaining = max(0, min(int(options.get('budget', 0)), space_size)-len(trials))
    actions = legal_actions(ids, draft, tried, max_phases)
    if not remaining or state.get('status') != 'running':
        actions = {}
    auto_submit = bool(actions and len(draft) == max_phases and 'submit' in actions)
    if auto_submit:
        actions = {}
    evidence = evidence or {}
    descriptors = {c['candidate_id']: c for c in evidence.get('candidates', [])}
    compact_pool = []
    for i, candidate in enumerate(candidates):
        cid = candidate['candidate_id']
        item = {'candidate_id': cid, 'phase_code': f'P{i+1}', 'label': _text(candidate.get('label')),
                'formula': _text(candidate.get('formula'), 100), 'matched_peak_positions': []}
        for match in candidate.get('matched_peaks', []):
            projected = {name: round(match[key], 6) for key, name in
                         (('observed', 'observed'), ('reference', 'reference'), ('delta', 'delta_deg'))
                         if _number(match.get(key)) is not None}
            if projected:
                item['matched_peak_positions'].append(projected)
        if _number(candidate.get('search_score')) is not None:
            item['search_score'] = candidate['search_score']
        descriptor = descriptors.get(cid, {})
        refs = descriptor.get('full_reference_peaks', [])
        item['full_reference_peaks'] = [[round(p[0], 6), round(p[1], 6)] for p in refs
                                         if isinstance(p, list) and len(p) == 2 and all(_number(v) is not None for v in p)]
        item['reference_peak_count'] = len(item['full_reference_peaks'])
        item['reference_peak_scope'] = 'all_evaluator_reflections_in_fit_range_including_profile_margin'
        if _number(descriptor.get('initial_peak_coverage_score')) is not None:
            item['initial_peak_coverage_score'] = descriptor['initial_peak_coverage_score']
        compact_pool.append(item)
        if f'add:P{i+1}' in actions:
            actions[f'add:P{i+1}']['description'] = f"Add P{i+1}: {item['label'] or item['formula']} to the draft."
    count = int(state.get('cloud_request_count', 0))
    limit = int(state.get('cloud_request_limit', min(int(options.get('budget', 0)), space_size)*max_phases))
    projection = {'status': state.get('status'), 'protocol_version': PROTOCOL_VERSION,
                  'objective': objective, 'claim_scope': claim_scope, 'pool': compact_pool,
                  'draft_candidate_ids': draft, 'trials': [_trial(t, pool) for t in trials],
                  'incumbent': _trial(state['incumbent'], pool) if state.get('incumbent') else None,
                  'observed_peaks': _peaks(evidence.get('observed_peaks', payload.get('observed_peaks', [])), limit=48),
                  'evidence_scope': 'Full reference lists use the evaluator wavelength, angular margin and intensity threshold; no extra top-k reflection truncation. Observed summaries contain at most 48 strongest detected peaks. Search/coverage scores are heuristics, not fitted objectives.',
                  'reference_angle_range': evidence.get('angle_range', []),
                  'reference_min_relative_intensity': evidence.get('min_peak_intensity'),
                  'initial_score_definition': evidence.get('initial_score_definition', ''),
                  'remaining': remaining, 'budget': options.get('budget'), 'max_phases': max_phases,
                  'cloud_request_count': count, 'cloud_request_limit': limit}
    result = {'state': projection, 'protocol_version': PROTOCOL_VERSION, 'choices': actions,
              'auto_submit': auto_submit, 'candidate_ids': draft if auto_submit else [],
              'space_size': space_size, 'remaining_space_size': space_size-len(tried),
              'menu_policy': 'all_candidates_add_or_submit_reachability_only', 'menu_size': len(actions),
              'cloud_request_count': count, 'cloud_request_limit': limit}
    token = _hash({'run_id': state.get('run_id'), 'decision': result})
    return {'decision_id': token, 'selection_token': token, **result}


def validate_construction(state, payload, evidence, candidate_ids, selection_token, path, *, objective, claim_scope):
    """Replay issued reservations, transitions and the final SUBMIT without cloud calls."""
    maximum = int(state['options']['max_phases'])
    if not isinstance(path, list) or not 1 <= len(path) <= maximum+1:
        raise ValueError('Construction path must include each ADD and a final submission.')
    records = {r['selection_token']: r for r in state.get('decision_requests', [])
               if r.get('evaluation_index') == len(state.get('trials', []))+1 and r.get('status') == 'reserved'}
    draft, used = [], set()
    for i, step in enumerate(path):
        if not isinstance(step, dict):
            raise ValueError('Each construction step must be an audit object.')
        token = step.get('selection_token')
        record = records.get(token)
        if not record or token in used or record['draft_candidate_ids'] != draft:
            raise ValueError('Construction contains an unissued, repeated, stale or unreachable decision.')
        used.add(token)
        snapshot = {**state, 'cloud_request_count': record['cloud_request_count']}
        decision = build_decision(snapshot, payload, objective=objective, claim_scope=claim_scope,
                                  draft_candidate_ids=draft, evidence=evidence)
        if token != decision['selection_token'] or step.get('decision_id', token) != token:
            raise ValueError('Construction decision token does not match its frozen evidence.')
        if step.get('draft_before') != draft:
            raise ValueError('Construction draft_before does not match the ADD sequence.')
        if step.get('kind') == 'auto_submit':
            if not decision['auto_submit'] or i != len(path)-1:
                raise ValueError('Auto submission is legal only at the phase limit.')
        else:
            action = decision['choices'].get(step.get('choice'))
            if action is None or action['kind'] != step.get('kind'):
                raise ValueError('Construction selects an illegal action.')
            if action['kind'] == 'add':
                draft = [*draft, action['candidate_id']]
                if i == len(path)-1:
                    raise ValueError('Construction must end with SUBMIT or auto_submit.')
            elif i != len(path)-1:
                raise ValueError('SUBMIT must be the last construction action.')
        if step.get('draft_after') != draft:
            raise ValueError('Construction draft_after does not match the selected action.')
    if path[-1].get('selection_token') != selection_token or sorted(draft) != sorted(candidate_ids or []):
        raise ValueError('Final construction and submitted candidates/token disagree.')
    if state.get('pending_decision', {}).get('selection_token') != selection_token:
        raise ValueError('A more recent draft reservation superseded the submitted token.')
    return {'method': PROTOCOL_VERSION, 'selection_token': selection_token, 'decision_id': selection_token,
            'construction_path': path, 'cloud_requests': sum(s.get('kind') != 'auto_submit' for s in path),
            'cloud_request_count': state.get('cloud_request_count'), 'cloud_request_limit': state.get('cloud_request_limit')}
