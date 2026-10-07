"""Development-stage material preflight through the public aidb Skill adapter.

Only candidate discovery is implemented here. A successful query is not proof
of scientific compatibility, and never authorizes a new remote calculation.
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

from .session_log import append_session_log_entry

logger = logging.getLogger(__name__)
AIDB_PREFLIGHT_SKILLS = frozenset({'vasp-pymatgen', 'atomic-structure'})
_PREPARATION_TOOLS = frozenset({'run_python', 'run_bash', 'run_skill_script',
    'submit_bohr_batchjob', 'submit_bohr_sandbox', 'upload_remote_job_input',
    'start_remote_job_command', 'run_remote_job_command', 'run_sub_agent'})
_REMOTE_TOOLS = frozenset({'submit_bohr_batchjob', 'submit_bohr_sandbox',
    'upload_remote_job_input', 'start_remote_job_command', 'run_remote_job_command'})


def _request(tool_context):
    """Current user content wins over a previous goal or persisted receipt."""
    invocation = tool_context._invocation_context
    content = invocation.user_content
    text = '\n'.join(p.text for p in (content.parts or []) if p.text)
    try:
        step = json.loads(text)
    except (ValueError, TypeError):
        step = None
    if isinstance(step, dict) and 'action' in step:
        text = '\n'.join(str(step.get(k) or '') for k in ('action', 'prior_context'))
        # Step requests inherit the parent's goal; it is current task context,
        # rather than a prior user turn, in this isolated executor invocation.
        text += '\n' + str(tool_context.state.get('goal') or '')
    elif re.search(r'继续|这个|该结构|改用|重算|重新计算|same|\bit\b|recompute', text, re.I):
        for event in reversed(invocation.session.events):
            prior = event.content
            if prior and prior.role == 'user' and event.invocation_id != tool_context.invocation_id:
                text += '\n' + '\n'.join(p.text for p in (prior.parts or []) if p.text)
                break
    return text


def _task_context(text, workspace):
    lower = text.lower()
    calculation = bool(re.search(r'弛豫|优化|总能|计算|准备|生成|relax|total energy|calculat|prepare|generate', lower))
    conceptual = bool(re.search(r'解释|介绍|什么是|概念|explain|what is|概念说明', lower))
    if not calculation or (conceptual and not re.search(r'请.*(?:准备|生成|执行)|please.*(?:prepare|run|calculate)', lower)):
        return None
    known = {}
    if re.search(r'单质硅|硅晶|硅体|硅单体|金刚石.*硅|\bsilicon\b|\bsi\b|硅', lower) and not re.search(r'氧化硅|二氧化硅|碳化硅|氮化硅|silica|sic\b|sio2\b', lower):
        known['formula'] = 'Si'
    if re.search(r'金刚石|diamond', lower):
        known['structure_model'] = 'diamond'
    if re.search(r'beta[- ]tin|β[- ]?锡|非金刚石|六方|hexagonal', lower):
        known['structure_model'] = 'outside_scope'
    if re.search(r'体相|晶体|bulk|crystal', lower):
        known['domain'] = 'bulk'
    if re.search(r'孤立|原子|表面|缺陷|isolated|surface|defect', lower):
        known['domain'] = 'outside_scope'
    method = re.search(r'\b(pbe\+u|pbesol|hse06|scan|lda|pbe)\b', lower)
    if method:
        known['functional'] = method.group(1).upper()
    # Read explicitly supplied existing structures; never create one at lookup.
    for match in re.finditer(r'[\w./-]+\.(?:cif|xyz|extxyz)|\bPOSCAR\b', text, re.I):
        path = (workspace / match.group()).resolve()
        if path.is_relative_to(workspace) and path.is_file():
            from ase.io import read
            atoms = read(path)
            known['structure_path'] = str(path)
            known['formula'] = 'Si' if set(atoms.get_chemical_symbols()) == {'Si'} else atoms.get_chemical_formula()
            known['domain'] = 'bulk' if all(atoms.pbc) else 'outside_scope'
            # A file does not establish the crystal model by composition alone.
    defaults = {key: value for key, value in {
        'functional': 'PBE', 'domain': 'bulk', 'structure_model': 'diamond',
    }.items() if key not in known}
    targets = []
    if re.search(r'弛豫|优化|relax', lower):
        targets.append('relaxation')
    if re.search(r'总能|能量|total energy|energy', lower):
        targets.append('total_energy')
    return {'known': known, 'retrieval_defaults': defaults,
        'unknown': [k for k in ('formula', 'structure_model', 'domain', 'functional') if k not in known],
        'targets': targets, 'recalculate': bool(re.search(r'重算|重新计算|recalculat|recompute', lower)),
        'prepare_only': bool(re.search(r'不提交|do not submit|only.*input|仅生成', lower)) or (
            bool(re.search(r'准备|prepare', lower)) and not re.search(r'提交|submit|执行计算|run calculation', lower)),
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
            '--command-timeout', '20', stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
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
        return {'status': query['status'], 'candidate_ids': ids, 'report_path': str(report_path),
            'audit_path': receipt['audit_path'], 'report': report, 'diagnostic_path': str(diagnostic_path)}
    except Exception as exc:
        diagnostic['error'] = str(exc) or type(exc).__name__
        return {'status': 'failed', 'detail': diagnostic['error'],
            'diagnostic_path': str(diagnostic_path)}
    finally:
        diagnostic_path.write_text(json.dumps(diagnostic, ensure_ascii=False), encoding='utf-8')


async def before_aidb_skill_load(tool, args, tool_context):
    name = getattr(tool, 'name', '')
    skill_name = str(args.get('skill_name') or '').strip().lower()
    feedback_key = f'temp:aidb_skill_feedback_{tool_context.function_call_id}'
    if name == 'load_skill':
        tool_context.state[feedback_key] = None
        if skill_name not in AIDB_PREFLIGHT_SKILLS:
            return None
    elif name not in _PREPARATION_TOOLS:
        return None
    try:
        workspace = Path(tool_context.state.get('workspace_dir') or tool_context.state.get('workdir') or '.').resolve()
        text = _request(tool_context)
        if name != 'load_skill' or skill_name == 'atomic-structure':
            if not re.search(r'vasp|硅|\bsilicon\b|\bSi\b', text, re.I):
                return None
        task = _task_context(text, workspace)
        if task is None:
            return None
        fingerprint = hashlib.sha256(json.dumps(task, sort_keys=True).encode()).hexdigest()
        key = f'{tool_context.invocation_id}:{fingerprint}'
        saved = tool_context.state.get('aidb_preflight') or {}
        if saved.get('key') == key:
            feedback = saved['feedback']
        else:
            known = task['known']
            if not known.get('formula'):
                feedback = {'status': 'needs_clarification', 'message': '请明确材料身份；尚未查询，新增计算暂停。'}
            elif known['formula'] != 'Si' or known.get('domain') == 'outside_scope' or known.get('structure_model') == 'outside_scope' or known.get('functional', 'PBE') != 'PBE':
                feedback = {'status': 'needs_clarification', 'message': '首期仅支持单质硅金刚石体相与普通 PBE；请明确当前目标，新增计算暂停。'}
            else:
                feedback = await _query(task, fingerprint[:24])
            feedback['task_context'] = task
            feedback['query_executed'] = feedback['status'] in {'found', 'not_found'}
            if feedback['status'] == 'found':
                feedback['message'] = '本地查库成功，有候选记录；候选尚未核验结构、结果和成功状态，不能直接认定可复用。'
            elif feedback['status'] == 'not_found':
                feedback['message'] = '本地查库成功，候选为空；仅继续原授权范围内的准备。'
            elif feedback['status'] == 'failed':
                feedback['message'] = '本地查库失败，开发期 A 暂停新增计算；这不是未命中。请检查诊断产物。'
            tool_context.state['aidb_preflight'] = {'key': key, 'feedback': feedback}
            logger.info('[aidb preflight] %s', feedback)
            append_session_log_entry(tool_context, {'kind': 'aidb_hook_feedback', **feedback})
    except Exception as exc:
        logger.warning('aidb preflight failed', exc_info=True)
        feedback = {'status': 'failed', 'query_executed': False, 'detail': str(exc),
            'message': '本地预检失败，开发期 A 暂停新增计算；不代表无数据。'}
        tool_context.state['aidb_preflight'] = {'feedback': feedback}
    if name == 'load_skill':
        tool_context.state[feedback_key] = feedback
        return None
    if feedback['status'] not in {'found', 'not_found'}:
        return {'status': 'blocked', 'aidb_preflight': feedback}
    if 'structure_model' in task['unknown']:
        return {'status': 'blocked', 'message': '已查候选；计算准备前请明确结构模型，检索默认值不是计算授权。', 'aidb_preflight': feedback}
    if name in _REMOTE_TOOLS and task['prepare_only']:
        return {'status': 'blocked', 'message': '当前请求仅授权准备，未授权提交计算。', 'aidb_preflight': feedback}
    return None


def after_aidb_skill_load(tool, args, tool_context, tool_response):
    """Put lookup evidence in the actual tool response seen by the agent/user."""
    if getattr(tool, 'name', '') == 'load_skill' and str(args.get('skill_name') or '').strip().lower() in AIDB_PREFLIGHT_SKILLS:
        feedback = tool_context.state.get(f'temp:aidb_skill_feedback_{tool_context.function_call_id}')
        if feedback and isinstance(tool_response, dict):
            return {**tool_response, 'aidb_preflight': feedback}
    return None
