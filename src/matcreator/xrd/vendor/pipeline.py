"""Resolve staged refinement inputs only from owned, immutable run snapshots."""
import base64
import hashlib
import json
from pathlib import Path
import re

from . import structures


def run_directory(root, run_id):
    if not isinstance(run_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}', run_id):
        raise ValueError('需要有效的运行编号')
    root = Path(root).resolve()
    directory = (root / f'oaw-{run_id}').resolve()
    if directory.parent != root:
        raise ValueError('运行目录不在 XRD 运行根目录内')
    return directory


def read_owned_run(root, run_id, agent_id, *, stage=None, completed=True):
    directory = run_directory(root, run_id)
    try:
        manifest = json.loads((directory / 'oaw.json').read_text(encoding='utf-8'))
        if manifest.get('agent_id') != agent_id or manifest.get('run_id') != run_id:
            raise ValueError('不能引用其他节点的运行')
        if completed and manifest.get('status') != 'completed':
            raise ValueError('引用的运行尚未成功完成')
        result = json.loads((directory / 'result.json').read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError('找不到引用的运行快照，请重新检索') from exc
    actual_stage = result.get('stage') if result.get('mode') == 'pipeline' else 'search' if result.get('mode') == 'match' else None
    if stage and actual_stage != stage:
        raise ValueError('引用的运行阶段不匹配')
    return directory, manifest, result


def owned_runs(root, agent_id):
    records = []
    for path in Path(root).glob('oaw-*/oaw.json'):
        try:
            record = json.loads(path.read_text(encoding='utf-8'))
            if record.get('agent_id') != agent_id or run_directory(root, record.get('run_id')) != path.parent.resolve():
                continue
            result_path = path.parent / 'result.json'
            result = json.loads(result_path.read_text(encoding='utf-8')) if result_path.is_file() else None
            stage = record.get('workflow_stage')
            if not stage:
                stage = result.get('stage') if result and result.get('mode') == 'pipeline' else 'search' if result and result.get('mode') == 'match' else None
            # Created time remains stable while progress/final manifest updates occur.
            records.append({'directory': path.parent, 'manifest': record, 'result': result,
                            'stage': stage, 'order': record.get('created_at_ns', path.stat().st_mtime_ns)})
        except (OSError, ValueError, TypeError):
            continue
    return sorted(records, key=lambda entry: entry['order'], reverse=True)


def latest_match_run(root, agent_id):
    return next((entry for entry in owned_runs(root, agent_id)
                 if entry['stage'] == 'search' and entry['manifest'].get('status') == 'completed'
                 and entry['result'] and entry['result'].get('mode') == 'match'), None)


def cif_input(filename, text, sha256=None):
    if not isinstance(text, str) or not text.strip() or '\x00' in text or len(text.encode('utf-8')) > structures.MAX_CIF_BYTES:
        raise ValueError('候选 CIF 为空或不合法')
    digest = hashlib.sha256(text.encode('utf-8')).hexdigest()
    if sha256 and digest != sha256:
        raise ValueError('候选 CIF 与保存的哈希不一致')
    # Filenames are descriptive only; worker never receives arbitrary filesystem paths.
    filename = re.split(r'[/\\]', str(filename))[-1] or 'candidate.cif'
    return {'filename': filename, 'text': text, 'sha256': digest}


def decoded_cif(value):
    try:
        raw = base64.b64decode(value['source_base64'], validate=True)
        if len(raw) > structures.MAX_CIF_BYTES or hashlib.sha256(raw).hexdigest() != value['sha256']:
            raise ValueError('候选 CIF 与保存的哈希不一致')
        # Preserve exact UTF-8 source bytes; a BOM may be part of the source hash.
        return cif_input(value['filename'], raw.decode('utf-8'), value['sha256'])
    except (KeyError, UnicodeError) as exc:
        raise ValueError('候选 CIF 内容不完整') from exc


async def build_pipeline_input(root, agent_id, options, *, progress=None):
    stage = options.workflow_stage
    if stage not in {'preopt', 'fit'}:
        raise ValueError('不支持的结构优化阶段')
    selected = options.selected_candidate_ids
    if not selected or len(selected) > 10 or len(set(selected)) != len(selected) or any(not isinstance(cid, str) or not cid for cid in selected):
        raise ValueError('请选择 1–10 个不重复的候选结构')
    match_dir, match_manifest, match = read_owned_run(root, options.workflow_match_run_id, agent_id, stage='search')
    if match_manifest.get('created_at_ns', 0) / 1e6 < getattr(options, 'workflow_started_at_ms', 0):
        raise ValueError('新流程需要重新检索，不能使用先前实验谱的候选')
    latest = latest_match_run(root, agent_id)
    if not latest or latest['manifest']['run_id'] != options.workflow_match_run_id:
        raise ValueError('候选属于旧检索，请从本次匹配结果重新选择')
    candidates = {candidate['node_id']: candidate for candidate in match.get('candidates', [])}
    if any(cid not in candidates for cid in selected):
        raise ValueError('所选候选不属于本次匹配结果')
    try:
        snapshot = json.loads((match_dir / 'input-snapshot.json').read_text(encoding='utf-8'))
        search_options = json.loads((match_dir / 'input.json').read_text(encoding='utf-8'))
        patterns = [item for item in snapshot if item['value']['kind'] == 'pattern']
        if len(patterns) != 1 or not patterns[0]['value']['points']:
            raise ValueError('检索运行缺少原始实验谱快照')
        pattern = patterns[0]['value']
        pattern_output = {'filename': pattern['filename'], 'points': pattern['points'],
                          'sha256': pattern.get('sha256'), 'node_id': patterns[0]['node_id']}
        wavelength = search_options['wavelength']
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError('检索运行的输入快照不完整，请重新检索') from exc
    payload = {'stage': stage, 'match_run_id': options.workflow_match_run_id,
               'wavelength': wavelength, 'pattern': pattern_output, 'candidates': []}
    prior_items = {}
    if stage == 'fit':
        preopt_dir, preopt_manifest, preopt_result = read_owned_run(root, options.workflow_preopt_run_id, agent_id, stage='preopt', completed=False)
        if preopt_manifest.get('status') == 'running':
            raise ValueError('预优化仍在运行，请等待结束后再选择拟合候选')
        if preopt_result.get('match_run_id') != options.workflow_match_run_id:
            raise ValueError('预优化结果与本次检索不属于同一流程')
        try:
            preopt_input = json.loads((preopt_dir / 'pipeline-input.json').read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError('预优化输入快照缺失') from exc
        if preopt_input.get('match_run_id') != options.workflow_match_run_id:
            raise ValueError('预优化输入与检索来源不一致')
        prior_items = {item['candidate_id']: item for item in preopt_input['candidates']}
        outcomes = {item['candidate_id']: item for item in preopt_result['candidates']}
        if any(cid not in outcomes or cid not in prior_items for cid in selected):
            raise ValueError('所选候选没有对应的预优化结果')
        payload['preopt_run_id'] = options.workflow_preopt_run_id
    for position, cid in enumerate(selected):
        candidate = candidates[cid]
        label = candidate.get('filename') or cid
        if progress:
            progress({'percent': 5 * position / len(selected), 'stage': '获取候选 CIF', 'indeterminate': True,
                      'completed': position, 'total': len(selected)})
        if stage == 'fit':
            previous, outcome = prior_items[cid], outcomes[cid]
            if not previous.get('source_cif') or outcome.get('status') == 'failed':
                payload['candidates'].append({'candidate_id': cid, 'label': label, 'search_score': candidate.get('score'),
                    'input_error': outcome.get('error') or '预优化失败，不能进入全谱拟合'})
                continue
            source = cif_input(**previous['source_cif'])
            if outcome.get('source_sha256') != source['sha256']:
                raise ValueError(f'{label} 的预优化源文件哈希不一致')
            accepted = outcome.get('accepted') is True and outcome.get('status') == 'completed'
            if accepted:
                if not outcome.get('output_cif'):
                    raise ValueError(f'{label} 缺少已接受的预优化 CIF')
                starting = decoded_cif(outcome['output_cif'])
            elif outcome.get('status') == 'rejected' and outcome.get('accepted') is False:
                starting = source
            else:
                raise ValueError(f'{label} 的预优化失败或尚未完成，不能进入全谱拟合')
            item = {'candidate_id': cid, 'label': label, 'search_score': candidate.get('score'),
                    'source_cif': source, 'starting_cif': starting,
                    'preopt': {'accepted': accepted, 'status': outcome['status'],
                               'fallback_to_original': not accepted}}
        else:
            metadata = candidate.get('metadata') or {}
            library_id = metadata.get('library_node_id')
            cod_id = metadata.get('reference_code')
            # COD identity comes from server-generated candidates and an authorized
            # library snapshot, never from a client URL or requested file path.
            from .library import expand_library_slots
            library_nodes = {item['node_id'] for item in expand_library_slots(snapshot) if item['value']['kind'] == 'library'}
            try:
                if library_id in library_nodes and isinstance(cod_id, str) and re.fullmatch(r'[1-9][0-9]{6}', cod_id) and cid == f'{library_id}:{cod_id}':
                    prepared = await structures.prepare_cod_structure({}, {'cod_id': cod_id})
                    source = decoded_cif(prepared['structure'])
                else:
                    associated_ids = {item['node_id'] for item in candidate.get('cifs', [])}
                    cifs = [item['value'] for item in snapshot if item['value']['kind'] == 'cif'
                            and item['node_id'] in associated_ids and item['value'].get('reference_node_id') == cid]
                    if len(cifs) != 1:
                        raise ValueError(f'{label} 需要检索快照中唯一关联且有读取权限的 CIF；请连接后重新检索')
                    source = decoded_cif(cifs[0]) if cifs[0].get('source_base64') else cif_input(cifs[0]['filename'], cifs[0]['text'], cifs[0].get('sha256'))
                item = {'candidate_id': cid, 'label': label, 'search_score': candidate.get('score'), 'source_cif': source}
            except (ValueError, OSError, structures.ResourceValidationError) as exc:
                item = {'candidate_id': cid, 'label': label, 'search_score': candidate.get('score'), 'input_error': str(exc)}
        payload['candidates'].append(item)
    return payload
