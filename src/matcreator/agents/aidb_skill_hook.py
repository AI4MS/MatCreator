"""Material preflight with final B consent through the public aidb Skill adapter.

Candidate discovery and fixed Si/PBE reuse use public exports. Lookup never
authorizes a new remote calculation.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import sys
import uuid
import weakref

from .session_log import append_session_log_entry

AIDB_REUSE_INSTRUCTION = """
For a silicon calculation request, consult the current `aidb_preflight` response
before preparing or submitting work. On reuse.status="reused", return its
verified results and record/source IDs; do not run preparation or computation.
Report relaxation and linked static energies separately with eV/cell units,
structure and actual conditions. Use the selection reason from the receipt.
Archive recency and a denser k mesh do not establish greater accuracy: never
claim a selected/static record is more accurate or highest precision. Current
public readback is authoritative; old memory/logs are not this turn's lookup.
On needs_clarification explain scientific differences or missing evidence;
On failed explain that existing data is unknown and computation may duplicate
existing work. Pause until the human replies with the exact confirmation shown
in this failure's message. Never generate that confirmation as an executor
action. Confirmation allows only the original scope and never marks lookup or
the full loop successful. Explicit recalculation still requires lookup.
"""

logger = logging.getLogger(__name__)
# Locks live only while callbacks are running; no Futures enter ADK state.
_preflight_locks = weakref.WeakValueDictionary()
_CONTINUATION_RE = re.compile(r'继续|照旧|这个|该结构|改用|换成|切换材料|重算|重新计算|same|continue|recalculat|recompute|\bit\b', re.I)
AIDB_PREFLIGHT_SKILLS = frozenset({'vasp-pymatgen', 'atomic-structure'})
_PREPARATION_TOOLS = frozenset({'run_python', 'run_bash', 'run_skill_script',
    'submit_bohr_batchjob', 'submit_bohr_sandbox', 'upload_remote_job_input',
    'start_remote_job_command', 'run_remote_job_command', 'run_sub_agent', 'run_flash_step'})
_REMOTE_TOOLS = frozenset({'submit_bohr_batchjob', 'submit_bohr_sandbox',
    'upload_remote_job_input', 'start_remote_job_command', 'run_remote_job_command'})
_CONFIRM_RE = re.compile(r'(?:确认继续|confirm continue)\s+([0-9a-f]{32})[。.!]?\s*', re.I)


def _failure_warning(feedback, tool_context, *, can_confirm):
    failure_id = uuid.uuid4().hex
    feedback.update(failure_id=failure_id, loop_complete=False,
        bypass={'status': 'pending', 'can_confirm': can_confirm},
        message='本地查库失败，无法判断已有数据是否存在，继续可能重复计算；新增计算保持暂停。')
    if can_confirm:
        feedback['message'] += f'若明确接受风险并继续原授权范围，请回复「确认继续 {failure_id}」。拒绝或未确认保持暂停；仅准备授权不允许提交。'
    else:
        feedback['message'] += '任务信息提取失败，请先修复或澄清；不能绕过此错误。'
    feedback['failure_session_id'] = tool_context._invocation_context.session.id


def _confirm_failure(text, task, saved, tool_context):
    """Consent comes only from a later human turn after the exact warning."""
    feedback = saved.get('feedback') or {}
    match = _CONFIRM_RE.fullmatch(text.partition('\n[AIDB_PRIOR_CONTEXT]\n')[0].strip())
    if (not match or tool_context.state['temp:aidb_internal_step']
            or feedback.get('status') != 'failed'
            or not feedback.get('bypass', {}).get('can_confirm')
            or match[1] != feedback.get('failure_id')
            or feedback.get('failure_session_id') != tool_context._invocation_context.session.id
            or task != feedback.get('task_context')):
        return None
    # State alone, including copied state from another session, is not evidence
    # that this human has received the warning for this event.
    events = tool_context._invocation_context.session.events
    if not any(event.invocation_id != tool_context.invocation_id and event.content
            and any(part.function_response and
                (part.function_response.response.get('aidb_preflight') or {}).get('failure_id') == match[1]
                for part in (event.content.parts or [])) for event in events):
        return None
    return {**feedback, 'bypass': {**feedback['bypass'], 'status': 'confirmed'},
        'message': '已明确确认此次查询失败及重复计算风险；仅继续原授权范围。查询仍失败，不代表未命中、预检成功或完整闭环通过；计算后仍尝试本地归档并如实报告。'}


def inherit_aidb_confirmation(state, tool_context, child_session_id):
    """The executor runner delegates this turn's grant to one fresh child."""
    saved = tool_context.state.get('aidb_preflight') or {}
    feedback = saved.get('feedback') or {}
    state.pop('aidb_parent_bypass', None)
    if (saved.get('invocation_id') == tool_context.invocation_id
            and feedback.get('bypass', {}).get('status') == 'confirmed'):
        state['aidb_parent_bypass'] = {'child_session_id': child_session_id,
            'feedback': feedback, 'request': saved['request']}


def _delegated_failure(text, workspace, tool_context):
    grant = tool_context.state.get('aidb_parent_bypass') or {}
    if (not tool_context.state['temp:aidb_internal_step']
            or grant.get('child_session_id') != tool_context._invocation_context.session.id):
        return None
    feedback = grant['feedback']
    parent = feedback['task_context']
    task = _task_context(text, workspace, parent)
    if (not task or task['known'] != parent['known']
            or not set(task['targets']).issubset(parent['targets'])
            or (task['require_static'] and 'total_energy' not in parent['targets'])):
        tool_context.state['aidb_parent_bypass'] = None
        return None
    task['prepare_only'] = task['prepare_only'] or parent['prepare_only']
    return {**feedback, 'task_context': task}


def _request(tool_context):
    """Current user content wins over a previous goal or persisted receipt."""
    invocation = tool_context._invocation_context
    content = invocation.user_content
    text = '\n'.join(p.text for p in (content.parts or []) if p.text)
    try:
        step = json.loads(text)
    except (ValueError, TypeError):
        step = None
    tool_context.state['temp:aidb_internal_step'] = isinstance(step, dict) and 'action' in step
    if tool_context.state['temp:aidb_internal_step']:
        text = str(step['action']) + '\n[AIDB_PRIOR_CONTEXT]\n' + str(step.get('prior_context') or '')
        # Step requests inherit the parent's goal; it is current task context,
        # rather than a prior user turn, in this isolated executor invocation.
        text += '\n' + str(tool_context.state.get('goal') or '')
    elif _CONTINUATION_RE.search(text):
        for event in reversed(invocation.session.events):
            prior = event.content
            if prior and prior.role == 'user' and event.invocation_id != tool_context.invocation_id:
                text += '\n[AIDB_PRIOR_CONTEXT]\n' + '\n'.join(p.text for p in (prior.parts or []) if p.text)
                break
    if not tool_context.state['temp:aidb_internal_step']:
        tool_context.state['aidb_user_request'] = text.partition('\n[AIDB_PRIOR_CONTEXT]\n')[0]
    return text


def _known_context(text, workspace):
    lower = text.lower()
    known = {}
    if re.search(r'单质硅|硅晶|硅体|硅单体|金刚石.*硅|\bsilicon\b|(?<![A-Za-z0-9])si(?![A-Za-z0-9])|硅', lower) and not re.search(r'氧化硅|二氧化硅|碳化硅|氮化硅|silica|sic\b|sio2\b', lower):
        known['formula'] = 'Si'
    for formula, pattern in [('C', r'金刚石碳|碳体相|\bcarbon\b'),
                             ('Ge', r'锗|\bgermanium\b'), ('Cu', r'铜|\bcopper\b')]:
        if re.search(pattern, lower):
            known['formula'] = formula
    if re.search(r'氧化硅|二氧化硅|碳化硅|氮化硅|silica|sic\b|sio2\b', lower):
        known['formula'] = 'outside_scope'
    if re.search(r'金刚石|diamond', lower):
        known['structure_model'] = 'diamond'
    if re.search(r'beta[- ]tin|β[- ]?锡|非金刚石|六方|hexagonal', lower):
        known['structure_model'] = 'outside_scope'
    if re.search(r'体相|晶体|bulk|crystal', lower):
        known['domain'] = 'bulk'
    if re.search(r'孤立|硅原子|单个.*原子|表面|缺陷|isolated|silicon atom\b|surface|defect', lower):
        known['domain'] = 'outside_scope'
    method = re.search(r'(?<![A-Za-z0-9])(pbe\+u|pbesol|hse06|scan|lda|pbe)(?![A-Za-z0-9])', lower)
    if method:
        known['functional'] = method.group(1).upper()
    cutoff = re.search(r'ENCUT\s*[=:]?\s*(\d+(?:\.\d+)?)', text, re.I)
    if cutoff:
        known['encut'] = float(cutoff.group(1))
    mesh = re.search(r'(?:k网格|k点|kpoints?|k[- ]?mesh)\s*[=:]?\s*(\d+)\s*[x×]\s*(\d+)\s*[x×]\s*(\d+)', text, re.I)
    constraints = {}
    for setting in re.finditer(r'\b([A-Z][A-Z0-9_]*)\s*=\s*([^\s,，;；]+)', text):
        if mesh and mesh.start() <= setting.start() < mesh.end():
            continue  # Mesh is a separate VASP input, not an INCAR tag.
        raw = setting.group(2)
        try:
            value = float(raw)
        except ValueError:
            value = {'T': True, 'TRUE': True, '.TRUE.': True, 'F': False, 'FALSE': False, '.FALSE.': False}.get(raw.upper(), raw)
        constraints[setting.group(1)] = value
    if constraints:
        known['incar_constraints'] = constraints
    if mesh:
        known['kpoint_mesh'] = [int(v) for v in mesh.groups()]
    # Read explicitly supplied existing structures; never create one at lookup.
    for match in re.finditer(r'[A-Za-z0-9_./-]+\.(?:cif|xyz|extxyz)|(?<![A-Za-z0-9_])POSCAR(?![A-Za-z0-9_])', text, re.I):
        path = (workspace / match.group()).resolve()
        if path.is_relative_to(workspace) and path.is_file():
            from ase.io import read
            atoms = read(path)
            known['structure_path'] = str(path)
            known['structure_digest'] = hashlib.sha256(path.read_bytes()).hexdigest()
            known['formula'] = 'Si' if set(atoms.get_chemical_symbols()) == {'Si'} else atoms.get_chemical_formula()
            known['domain'] = 'bulk' if all(atoms.pbc) else 'outside_scope'
            # A file does not establish the crystal model by composition alone.
    return known


def _prepare_only(text):
    lower = text.lower()
    # Restrict the requested task, not unrelated qualifiers such as "only after"
    # or "record hashes only". Those occur in authorized executor actions too.
    limited_preparation = re.search(
        r'(?:只|仅)(?:要|需|需要|要求|允许|授权)?(?:帮我|为我)?(?:准备|生成|制作|创建|提供|输入)'
        r'|\bonly\s+(?:(?:please|to)\s+)?(?:prepare|generate|create|write|provide|inputs?\b)'
        r'|\b(?:prepare|generate|create|write|provide)\s+only\s+(?:[\w-]+\s+)*inputs?\b'
        r'|\binputs?(?:\s+(?:preparation|generation))?\s+only\b', lower)
    input_only_request = re.search(
        r'(?:^|[.!?;\n])\s*(?:please\s+)?only\s+(?:the|an?)\s+'
        r'(?:[\w-]+\s+)*inputs?\b', lower)
    return bool(re.search(r'不提交|不要提交|不要计算|先别计算|暂不[^。；\n]*(?:提交|计算)|do not submit', lower) or limited_preparation or input_only_request) or (
        bool(re.search(r'准备|prepare', lower)) and not re.search(r'提交|submit|执行计算|run calculation', lower))


def _task_context(text, workspace, previous=None):
    current, _, prior = text.partition('\n[AIDB_PRIOR_CONTEXT]\n')
    lower = current.lower()
    continuation = bool(_CONTINUATION_RE.search(current))
    calculation = continuation or _prepare_only(current) or bool(re.search(r'弛豫|优化|总能|计算|准备|生成|执行|提交|relax|total energy|calculat|prepare|generate|submit|\brun\b', lower))
    conceptual = bool(re.search(r'解释|介绍|什么是|概念|explain|what is|概念说明', lower))
    if not calculation or (conceptual and not re.search(r'请.*(?:准备|生成|执行)|please.*(?:prepare|run|calculate)', lower)):
        return None
    previous = previous or {}
    known = dict(previous.get('known') or {})
    known.update(_known_context(prior, workspace))
    current_known = _known_context(current, workspace)
    if 'incar_constraints' in current_known:
        current_known['incar_constraints'] = {**known.get('incar_constraints', {}), **current_known['incar_constraints']}
    known.update(current_known)
    if re.search(r'改用|换成|切换材料', current) and 'formula' not in current_known and 'functional' not in current_known and 'encut' not in current_known and 'incar_constraints' not in current_known and 'kpoint_mesh' not in current_known:
        known.pop('formula', None)
    defaults = {key: value for key, value in {
        'functional': 'PBE', 'domain': 'bulk', 'structure_model': 'diamond',
    }.items() if key not in known}
    target_text = lower if re.search(r'弛豫|优化|relax|总能|能量|energy', lower) else prior.lower()
    targets = []
    if re.search(r'弛豫|优化|relax', target_text):
        targets.append('relaxation')
    if re.search(r'总能|能量|total energy|energy', target_text):
        targets.append('total_energy')
    if not targets:
        targets = list(previous.get('targets') or [])
    return {'known': known, 'retrieval_defaults': defaults,
        'unknown': [k for k in ('formula', 'structure_model', 'domain', 'functional') if k not in known],
        'targets': targets, 'require_static': bool(re.search(r'静态|单点|\bstatic\b|single.point', target_text))
            or (continuation and not re.search(r'弛豫|优化|relax|总能|energy', lower) and previous.get('require_static', False)),
        'recalculate': bool(re.search(r'重算|重新计算|recalculat|recompute',
            re.sub(r'(?:不|不要|不用|无需|禁止)(?:再|重新)?(?:重算|重新计算|计算)|(?:do not|don.t|no need to)\s+(?:recalculate|recompute)', '', lower))),
        'prepare_only': _prepare_only(current) or (
            (_prepare_only(prior) or previous.get('prepare_only', False))
            and not re.search(r'提交|submit|执行计算|run calculation', lower)),
        'workspace': str(workspace)}


def _adapter_path():
    from matcreator.skill import skill_bundle_info, is_skill_disabled
    if is_skill_disabled('ai-ready-db'):
        raise RuntimeError('ai-ready-db Skill is disabled')
    bundle = skill_bundle_info('ai-ready-db')
    if bundle and bundle.get('skill_dir'):
        path = Path(bundle['skill_dir']) / 'scripts/aidb_loop_adapter.py'
        if path.is_file():
            return path
    # The canonical Skill package supplied by the configured public bridge.
    root = os.environ.get('AI_READY_DB_ROOT')
    if root:
        path = Path(root).expanduser() / 'skills/ai-ready-db/scripts/aidb_loop_adapter.py'
        if path.is_file():
            return path
    raise RuntimeError('ai-ready-db adapter unavailable; install the Skill or configure AI_READY_DB_ROOT')


async def _query(task, task_id):
    workspace = Path(task['workspace'])
    directory = workspace / '.aidb' / 'requests' / task_id / uuid.uuid4().hex
    directory.mkdir(parents=True, exist_ok=True)
    task_path = directory / 'task.json'
    diagnostic_path = directory / 'adapter-result.json'
    task_path.write_text(json.dumps({'task_id': task_id, 'purpose': task['targets'],
        'target': {'formula': task['known']['formula'], 'min_records': 1,
            'functional': task['known'].get('functional', 'PBE'), 'domain': 'bulk',
            'allow_dft_u': False}, 'related_materials': []}), encoding='utf-8')
    diagnostic = {'task_path': str(task_path), 'query_attempted': False}
    try:
        process = await asyncio.create_subprocess_exec(sys.executable, str(_adapter_path()),
            '--workspace', str(workspace), 'preflight', '--task-json', str(task_path),
            '--command-timeout', '20', '--export-dir', str(directory / 'export'),
            '--export-format', 'jsonl', stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        diagnostic['query_attempted'] = True
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=50)
        except BaseException:
            if process.returncode is None:
                process.kill()
            await process.communicate()
            raise
        diagnostic.update(returncode=process.returncode, stdout=stdout.decode(errors='replace'),
            stderr=stderr.decode(errors='replace'))
        receipt = json.loads(stdout.decode())
        if not isinstance(receipt, dict):
            raise ValueError('adapter receipt must be an object')
        if process.returncode or receipt.get('ok') is not True:
            return {**receipt, 'status': 'failed', 'diagnostic_path': str(diagnostic_path)}
        report_path = Path(receipt['report_path'])
        report = json.loads(report_path.read_text(encoding='utf-8'))
        query = report['queries'][0]
        if query['formula'] != task['known']['formula'] or query['status'] not in {'found', 'not_found'}:
            raise ValueError('invalid target query report')
        ids = query['record_ids']
        if not isinstance(ids, list) or bool(ids) != (query['status'] == 'found'):
            raise ValueError('inconsistent candidate report')
        records = [json.loads(line) for export in report['exports']
            for line in Path(export['output_path']).read_text(encoding='utf-8').splitlines() if line.strip()]
        if sorted(r['record_id'] for r in records) != sorted(ids):
            raise ValueError('export does not contain exactly the current candidate IDs')
        from .silicon_reuse import select_reuse
        reuse = select_reuse(records, task) if ids else None
        return {'reuse': reuse, 'status': query['status'], 'candidate_ids': ids, 'report_path': str(report_path),
            'audit_path': receipt['audit_path'], 'report': report, 'diagnostic_path': str(diagnostic_path)}
    except Exception as exc:
        diagnostic['error'] = str(exc) or type(exc).__name__
        return {'status': 'failed', 'detail': diagnostic['error'],
            'diagnostic_path': str(diagnostic_path)}
    finally:
        diagnostic_path.write_text(json.dumps(diagnostic, ensure_ascii=False), encoding='utf-8')


def _export_feedback(feedback):
    """Keep input evidence visible without declaring it a generated artifact."""
    task = feedback.get('task_context')
    if not task or 'structure_path' not in task.get('known', {}):
        return feedback
    known = dict(task['known'])
    known['input_structure_path'] = known.pop('structure_path')
    return {**feedback, 'task_context': {**task, 'known': known}}


async def before_aidb_skill_load(tool, args, tool_context):
    name = getattr(tool, 'name', '')
    skill_name = str(args.get('skill_name') or '').strip().lower()
    feedback_key = f'temp:aidb_skill_feedback_{tool_context.function_call_id}'
    tool_context.state[feedback_key] = None
    if name == 'load_skill':
        if skill_name not in AIDB_PREFLIGHT_SKILLS:
            return None
    elif name not in _PREPARATION_TOOLS:
        return None
    try:
        workspace = Path(tool_context.state.get('workspace_dir') or tool_context.state.get('workdir') or '.').resolve()
        text = _request(tool_context)
        saved_task = (tool_context.state.get('aidb_preflight') or {}).get('feedback', {}).get('task_context')
        referential = _CONTINUATION_RE.search(text.partition('\n[AIDB_PRIOR_CONTEXT]\n')[0])
        if name != 'load_skill' or skill_name == 'atomic-structure':
            active = (tool_context.state.get('aidb_preflight') or {}).get('invocation_id') == tool_context.invocation_id
            pending_failure = (tool_context.state.get('aidb_preflight') or {}).get('feedback', {}).get('status') == 'failed'
            if not active and not pending_failure and not (referential and saved_task) and not re.search(r'vasp|硅|\bsilicon\b|(?<![A-Za-z0-9])Si(?![A-Za-z0-9])', text, re.I):
                return None
        task = _task_context(text, workspace, saved_task if referential else None)
        if task is None:
            pending = (tool_context.state.get('aidb_preflight') or {}).get('feedback') or {}
            if name in _PREPARATION_TOOLS and pending.get('status') == 'failed':
                return {'status': 'blocked', 'aidb_preflight': _export_feedback(pending),
                    'message': '此次失败尚无当前有效确认；新增计算保持暂停。'}
            return None
        if tool_context.state['temp:aidb_internal_step']:
            parent_text = tool_context.state.get('aidb_user_request') or tool_context.state.get('goal') or ''
            parent_task = _task_context(parent_text, workspace)
            if parent_task:
                task['recalculate'] = task['recalculate'] or parent_task['recalculate']
            task['prepare_only'] = task['prepare_only'] or tool_context.state.get('aidb_user_prepare_only', False) or _prepare_only(tool_context.state.get('aidb_user_request') or tool_context.state.get('goal') or '')
        else:
            tool_context.state['aidb_user_prepare_only'] = task['prepare_only']
        lock_key = (id(asyncio.get_running_loop()), tool_context._invocation_context.session.id,
                    tool_context.invocation_id)
        lock = _preflight_locks.setdefault(lock_key, asyncio.Lock())
        async with lock:
            saved = tool_context.state.get('aidb_preflight') or {}
            confirmed = _delegated_failure(text, workspace, tool_context)
            original_request = text
            if confirmed:
                task = confirmed['task_context']
                original_request = tool_context.state['aidb_parent_bypass']['request']
            if _CONFIRM_RE.fullmatch(text.partition('\n[AIDB_PRIOR_CONTEXT]\n')[0].strip()) and saved.get('request'):
                original_task = _task_context(saved['request'], workspace)
                confirmed = _confirm_failure(text, original_task, saved, tool_context)
                if confirmed:
                    task = original_task
                    original_request = saved['request']
                    tool_context.state['aidb_user_request'] = saved['request']
                    tool_context.state['aidb_user_prepare_only'] = task['prepare_only']
            fingerprint = hashlib.sha256(json.dumps(task, sort_keys=True).encode()).hexdigest()
            key = f'{tool_context.invocation_id}:{fingerprint}'
            if saved.get('key') == key:
                feedback = saved['feedback']
            else:
                known = task['known']
                if confirmed:
                    feedback = confirmed
                elif not known.get('formula'):
                    feedback = {'status': 'needs_clarification', 'message': '请明确材料身份；尚未查询，新增计算暂停。'}
                elif known['formula'] != 'Si' or known.get('domain') == 'outside_scope' or known.get('structure_model') == 'outside_scope' or known.get('functional', 'PBE') != 'PBE':
                    feedback = {'status': 'needs_clarification', 'message': '首期仅支持单质硅金刚石体相与普通 PBE；请明确当前目标，新增计算暂停。'}
                else:
                    feedback = await _query(task, fingerprint[:24])
                feedback['task_context'] = task
                feedback['query_executed'] = feedback['status'] in {'found', 'not_found'}
                if feedback['status'] == 'found':
                    feedback['message'] = feedback['reuse']['reason']
                    if feedback['reuse']['status'] == 'reused':
                        feedback['message'] = '已复用本地成功结果，不启动新计算。' + feedback['message']
                    elif feedback['reuse']['status'] == 'recalculate':
                        feedback['message'] = '已核验已有结果；按显式重算请求继续原授权范围。'
                elif feedback['status'] == 'not_found':
                    feedback['message'] = '本地查库成功，候选为空；仅继续原授权范围内的准备。'
                elif feedback['status'] == 'failed' and not confirmed:
                    _failure_warning(feedback, tool_context, can_confirm=True)
                tool_context.state['aidb_preflight'] = {'key': key, 'invocation_id': tool_context.invocation_id,
                    'request': original_request, 'feedback': feedback}
                logger.info('[aidb preflight] %s', feedback)
                append_session_log_entry(tool_context, {'kind': 'aidb_hook_feedback', **_export_feedback(feedback)})
    except Exception as exc:
        logger.warning('aidb preflight failed', exc_info=True)
        feedback = {'status': 'failed', 'query_executed': False, 'detail': str(exc),
            'message': '本地预检失败。'}
        _failure_warning(feedback, tool_context, can_confirm=False)
        try:
            diagnostic = workspace / '.aidb' / 'requests' / 'errors' / f'{uuid.uuid4().hex}.json'
            diagnostic.parent.mkdir(parents=True, exist_ok=True)
            diagnostic.write_text(json.dumps(feedback, ensure_ascii=False), encoding='utf-8')
            feedback['diagnostic_path'] = str(diagnostic)
        except (OSError, UnboundLocalError):
            logger.warning('Could not persist aidb extraction diagnostic', exc_info=True)
        tool_context.state['aidb_preflight'] = {'invocation_id': tool_context.invocation_id, 'feedback': feedback}
        try:
            append_session_log_entry(tool_context, {'kind': 'aidb_hook_feedback', **feedback})
        except Exception:
            logger.warning('Could not persist aidb failure feedback', exc_info=True)
    tool_context.state[feedback_key] = _export_feedback(feedback)
    if name == 'load_skill':
        return None
    if feedback['status'] not in {'found', 'not_found'} and feedback.get('bypass', {}).get('status') != 'confirmed':
        return {'status': 'blocked', 'aidb_preflight': _export_feedback(feedback)}
    reuse = feedback.get('reuse') or {}
    if reuse.get('status') == 'reused':
        return {'status': 'reused', 'message': feedback['message'], 'aidb_preflight': _export_feedback(feedback)}
    if reuse.get('status') == 'needs_clarification' and not task['recalculate']:
        return {'status': 'blocked', 'message': reuse['reason'], 'aidb_preflight': _export_feedback(feedback)}
    if 'structure_model' in task['unknown'] and not task['known'].get('structure_path'):
        return {'status': 'blocked', 'message': '已查候选；计算准备前请明确结构模型，检索默认值不是计算授权。', 'aidb_preflight': _export_feedback(feedback)}
    if name in _REMOTE_TOOLS and task['prepare_only']:
        return {'status': 'blocked', 'message': '当前请求仅授权准备，未授权提交计算。', 'aidb_preflight': _export_feedback(feedback)}
    return None


def after_aidb_skill_load(tool, args, tool_context, tool_response):
    """Put lookup evidence in the actual tool response seen by the agent/user."""
    name = getattr(tool, 'name', '')
    if name == 'collect_silicon_vasp_result' and isinstance(tool_response, dict) and tool_response.get('status') == 'success':
        from matcreator.tools.silicon_archive import archive_silicon_vasp_result
        result = archive_silicon_vasp_result(**args, tool_context=tool_context)
        feedback = (tool_context.state.get('aidb_preflight') or {}).get('feedback') or {}
        if feedback.get('status') == 'failed':
            result = {**result, 'aidb_preflight': _export_feedback(feedback), 'loop_complete': False}
        return result
    if name in _PREPARATION_TOOLS or (name == 'load_skill' and str(args.get('skill_name') or '').strip().lower() in AIDB_PREFLIGHT_SKILLS):
        feedback = tool_context.state.get(f'temp:aidb_skill_feedback_{tool_context.function_call_id}')
        if feedback and isinstance(tool_response, dict):
            return {**tool_response, 'aidb_preflight': feedback}
    return None
