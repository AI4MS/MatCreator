"""AIDB read boundary. A workspace may contain many independently bound sessions."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import sys
from uuid import uuid4
from matcreator.aidb.facts import record_facts


class AIDBError(RuntimeError):
    def __init__(self, message, status='failed'):
        super().__init__(message)
        self.status = status


def _save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    temporary.replace(path)


class SessionAIDB:
    def __init__(self, workspace, session_id, *, query_database=None, query_timeout=None):
        if not session_id or session_id == 'default':
            raise AIDBError('AIDB 操作需要明确的会话身份', 'needs_configuration')
        self.workspace = Path(workspace).resolve()
        self.session_id = session_id
        self.root = self.workspace / '.aidb-sessions' / hashlib.sha256(session_id.encode()).hexdigest()
        self.query_database = query_database or (InstalledSkillQuery(timeout=query_timeout) if query_timeout is not None
                                                 else InstalledSkillQuery())

    def bind(self, config_path):
        config = Path(config_path).expanduser().resolve(strict=True)
        binding = {'config_path': str(config), 'config_sha256': hashlib.sha256(config.read_bytes()).hexdigest(),
                   'binding_id': uuid4().hex}
        _save(self.root / 'binding.json', binding)
        return {'status': 'bound', 'binding_id': binding['binding_id'], 'session_id': self.session_id}

    def binding(self, expected=None):
        try:
            binding = json.loads((self.root / 'binding.json').read_text(encoding='utf-8'))
        except FileNotFoundError:
            raise AIDBError('本会话尚未选择 AIDB 配置', 'needs_configuration') from None
        if expected and expected != binding['binding_id']:
            raise AIDBError('此结果绑定的数据库已切换，请重新查询', 'stale_binding')
        config = Path(binding['config_path'])
        if not config.is_file() or hashlib.sha256(config.read_bytes()).hexdigest() != binding['config_sha256']:
            raise AIDBError('所选 AIDB 配置已改变，请重新绑定并查询', 'stale_binding')
        return binding

    def query(self, conditions, *, deduplicate=True):
        if not isinstance(conditions, dict) or not any(v is not None for v in conditions.values()):
            raise AIDBError('请明确要查询的材料或筛选条件', 'needs_clarification')
        binding = self.binding()
        native, filters = query_conditions(conditions)
        records, report = self.query_database(Path(binding['config_path']), self.root, native)
        excluded = []
        matched = []
        for record in records:
            reasons = filter_reasons(record, filters)
            if reasons:
                excluded.append({'record_id': record['record_id'], 'reasons': reasons})
            else:
                matched.append(record)
        selected = {}
        for record in matched:
            identity = (frame_identity(record) if deduplicate else None) or ('record', record['record_id'])
            if identity not in selected:
                selected[identity] = record
        records = list(selected.values())
        return {'status': 'complete', 'kind': 'aidb_result', 'session_id': self.session_id,
                'binding_id': binding['binding_id'], 'records': records, 'report': report,
                'conditions': conditions, 'matched_count': len(records), 'excluded': excluded,
                'host_filter': filters, 'coverage': coverage(records), 'duplicates': len(matched) - len(records)}

    def detail(self, record_id, *, binding_id=None):
        expected = self.binding(binding_id)['binding_id']
        result = self.query({'record_id': record_id})
        if result['binding_id'] != expected:
            raise AIDBError('此结果绑定的数据库已切换，请重新查询', 'stale_binding')
        self.binding(expected)
        if len(result['records']) != 1 or result['records'][0].get('record_id') != record_id:
            raise AIDBError('该记录无法在本会话绑定的数据库中唯一读回', 'not_found')
        return {**result, 'record': result['records'][0], 'expanded_record_id': record_id}


    def publish(self, result):
        """Persist exact readback; return a UI reference and answerable model facts."""
        self.binding(result['binding_id'])
        result_id = uuid4().hex
        _save(self.root / 'results' / (result_id + '.json'), result)
        detail = 'expanded_record_id' in result
        return {'kind': 'aidb_result', 'status': result['status'], 'session_id': self.session_id,
                'binding_id': result['binding_id'], 'result_id': result_id,
                'operation': 'detail' if detail else result.get('operation', 'query'), 'fresh_read': True,
                **({'frame_index': result['frame_index']} if 'frame_index' in result else {}),
                **({'eos_member': result['eos_member']} if 'eos_member' in result else {}),
                'expanded_record_id': result.get('expanded_record_id'),
                'matched_count': len(result['records']), 'conditions': result.get('conditions'),
                'report': {key: result.get('report', {}).get(key)
                           for key in ('complete', 'match_count', 'conditions')},
                'host_filter': result.get('host_filter'), 'duplicates': result.get('duplicates'),
                'coverage': result.get('coverage'),
                'facts_scope': {'omitted_arrays': 'full_values_in_ui_snapshot',
                                'comparison': 'not_exhaustive',
                                'null': 'unknown_or_not_saved',
                                'unlisted_fields': 'not_evidence_of_missing_or_not_applicable',
                                'optional_not_provided': 'not_in_returned_record; raw_output_not_assessed',
                                'label_presence': 'not_alignment_convergence_or_training_admission'},
                'records': [{'record_id': r['record_id'], 'formula': r.get('formula_reduced'),
                    'functional': r.get('functional'), 'calculation_type': r.get('calculation_type'),
                    'labels': [k for k in ('energy', 'forces', 'stress', 'virial') if r.get(k) is not None],
                    'facts': record_facts(r, detail=detail, frame_verified=frame_identity(r) is not None)}
                    for r in result['records']]}

    def result(self, result_id):
        if not re.fullmatch(r'[a-f0-9]{32}', result_id):
            raise AIDBError('无效的查询结果标识', 'not_found')
        try:
            result = json.loads((self.root / 'results' / (result_id + '.json')).read_text(encoding='utf-8'))
        except FileNotFoundError:
            raise AIDBError('查询结果不存在', 'not_found') from None
        self.binding(result['binding_id'])
        return result

    def export(self, record_id, formats, *, result_id=None, binding_id=None, verify=None):
        """Use a result snapshot only for selection. Always read record values by ID."""
        from matcreator.aidb.exports import export_record

        if not formats or any(fmt not in ('extxyz', 'poscar', 'html', 'temperature') for fmt in formats):
            raise AIDBError('请选择 EXTXYZ、POSCAR、HTML 详情或 AIMD 温度曲线', 'unsupported')
        binding_id = self.binding(binding_id)['binding_id']
        if result_id is not None:
            selection = self.result(result_id)
            if selection.get('operation') == 'relaxation_candidates' and verify is None:
                raise AIDBError('请在对话中明确选用 VASP 候选', 'not_ready')
            if (selection['binding_id'] != binding_id
                    or not any(r['record_id'] == record_id for r in selection['records'])):
                raise AIDBError('该记录未在本次结果中核实', 'not_ready')
        result = self.detail(record_id, binding_id=binding_id)
        artifacts = (verify(result['record']) or {}) if verify is not None else {}
        files, issues = export_record(result['record'], dict.fromkeys(formats))
        if verify is not None and issues:
            raise AIDBError('未能生成 VASP 步骤要求的全部文件：' + '；'.join(issues.values()), 'not_ready')
        self.binding(binding_id)
        export_id = uuid4().hex
        receipt = {'kind': 'aidb_export', 'export_id': export_id, 'record_id': record_id,
                   'binding_id': binding_id, 'session_id': self.session_id,
                   'status': 'complete' if not issues else 'partial' if files else 'unavailable',
                   'files': {fmt: {'filename': file['filename']} for fmt, file in files.items()},
                   'issues': issues}
        if artifacts:
            from matcreator.aidb.vasp_archive import file_digest

            destination = self.root / 'exports' / export_id
            destination.mkdir(parents=True, exist_ok=False)
            try:
                for name, (source, digest) in artifacts.items():
                    target = destination / name
                    shutil.copyfile(source, target)
                    if file_digest(target) != digest:
                        raise AIDBError('VASP 文件在交付期间发生变化', 'not_ready')
                self.binding(binding_id)
            except BaseException:
                shutil.rmtree(destination)
                raise
            receipt['artifact_paths'] = {name: (destination / name).relative_to(self.workspace).as_posix()
                                         for name in artifacts}
            receipt['files'].update({name: {'filename': name} for name in artifacts})
        _save(self.root / 'exports' / (export_id + '.json'), {'receipt': receipt, 'files': files})
        return receipt

    def export_file(self, export_id, fmt):
        if not re.fullmatch(r'[a-f0-9]{32}', export_id):
            raise AIDBError('无效的导出标识', 'not_found')
        try:
            export = json.loads((self.root / 'exports' / (export_id + '.json')).read_text(encoding='utf-8'))
        except FileNotFoundError:
            raise AIDBError('导出不存在', 'not_found') from None
        self.binding(export['receipt']['binding_id'])
        artifact_path = export['receipt'].get('artifact_paths', {}).get(fmt)
        if artifact_path:
            path = self.workspace / artifact_path
            if path.resolve() == path and path.is_file():
                return {'path': path, 'filename': fmt}
        if fmt not in export['files']:
            raise AIDBError('所请求的下载尚未就绪', 'not_ready')
        return export['files'][fmt]


class InstalledSkillQuery:
    def __init__(self, script=None, python=None, *, timeout=60):
        self.script = script
        self.python = python or sys.executable
        self.timeout = timeout

    def script_path(self):
        script = self.script
        if script is None:
            from matcreator.skill import skill_bundle_info, is_skill_disabled
            if is_skill_disabled('ai-ready-db'):
                raise AIDBError('ai-ready-db skill 已停用', 'needs_configuration')
            bundle = skill_bundle_info('ai-ready-db') or {}
            folder = Path(bundle['skill_dir']) if bundle.get('skill_dir') else None
            script = folder / 'scripts' / 'aidb_ops.py' if folder else None
        if not script or not Path(script).is_file():
            raise AIDBError('未安装 ai-ready-db skill 的 aidb_ops.py', 'needs_configuration')
        return Path(script).resolve()

    def __call__(self, config, workspace, conditions):
        script = self.script_path()
        workspace.mkdir(parents=True, exist_ok=True)
        path = workspace / ('conditions-' + uuid4().hex + '.json')
        _save(path, conditions)
        try:
            proc = subprocess.run([self.python, '-B', str(script), '--workspace', str(workspace),
                '--config', str(config), 'query', '--conditions-json', str(path)],
                cwd=workspace, text=True, capture_output=True, timeout=self.timeout)
            receipt = json.loads(proc.stdout)
            if not isinstance(receipt, dict):
                raise AIDBError('AIDB 未返回对象回执', 'incomplete')
            if proc.returncode or receipt.get('status') == 'error':
                raise AIDBError(str(receipt.get('message') or 'AIDB 查询失败'))
            if receipt.get('status') != 'query_complete' or receipt.get('complete') is not True:
                raise AIDBError('AIDB 查询回执不完整', 'incomplete')
            def artifact(name):
                candidate = Path(receipt[name]).resolve(strict=True)
                if not candidate.is_relative_to(workspace.resolve()):
                    raise AIDBError('AIDB 回执产物不在本次会话目录内', 'incomplete')
                return candidate
            report = json.loads(artifact('report_path').read_text(encoding='utf-8'))
            if not isinstance(report, dict):
                raise AIDBError('AIDB 未返回对象查询报告', 'incomplete')
            records = [json.loads(line) for line in artifact('details_path').read_text(encoding='utf-8').splitlines() if line.strip()]
            if (report.get('conditions') != conditions or report.get('complete') is not True
                or receipt.get('match_count') != len(records) or report.get('match_count') != len(records)
                or any(not isinstance(r, dict) or not isinstance(r.get('record_id'), str) or not r.get('record_id') for r in records)
                or len({r.get('record_id') for r in records}) != len(records)):
                raise AIDBError('AIDB 查询回执与记录不一致', 'incomplete')
            return records, report
        except subprocess.TimeoutExpired:
            raise AIDBError('AIDB 查询超时，未取得完整结果', 'timeout') from None
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise AIDBError('AIDB 查询产物读取失败：' + str(exc), 'incomplete') from None

NATIVE_STRINGS = {'record_id', 'formula', 'functional', 'calculation_type', 'source_dataset', 'source_id'}
NATIVE_BOOLS = {'has_hubbard_u', 'has_forces', 'has_stress'}
HOST_FILTERS = {'structure_role', 'spacegroup', 'target_kelvin'}


def query_conditions(conditions):
    unsupported = set(conditions) - NATIVE_STRINGS - NATIVE_BOOLS - {'elements'} - HOST_FILTERS
    if unsupported:
        raise AIDBError('尚不支持这些查询条件：' + ', '.join(sorted(unsupported)), 'unsupported')
    conditions = {key: value for key, value in conditions.items() if value is not None}
    for key, value in conditions.items():
        valid = True
        if key in NATIVE_STRINGS | {'spacegroup'}:
            valid = isinstance(value, str) and bool(value.strip())
        elif key in NATIVE_BOOLS:
            valid = isinstance(value, bool)
        elif key == 'elements':
            valid = isinstance(value, list) and bool(value) and all(isinstance(el, str) and el for el in value)
        elif key == 'structure_role':
            valid = value in ('initial', 'final')
        elif key == 'target_kelvin':
            valid = type(value) in (int, float) and math.isfinite(value) and value >= 0
        if not valid:
            raise AIDBError('查询条件格式不正确：' + key, 'needs_clarification')
    if ('spacegroup' in conditions) != ('structure_role' in conditions):
        raise AIDBError('空间群筛选需同时明确 initial 起始结构或 final 末帧角色', 'needs_clarification')
    native = {key: value for key, value in conditions.items() if key not in HOST_FILTERS}
    if not native:
        raise AIDBError('请同时提供化学式、元素或其他原生查询条件', 'needs_clarification')
    return native, {key: value for key, value in conditions.items() if key in HOST_FILTERS}


def filter_reasons(record, filters):
    facts = (record.get('source_metadata') or {}).get('vasp_facts') or {}
    reasons = []
    if 'spacegroup' in filters:
        structure = (facts.get('initial_structure') or {}) if filters['structure_role'] == 'initial' else facts
        actual = structure.get('spacegroup') or (structure.get('symmetry') or {}).get('symbol')
        if actual != filters['spacegroup']:
            reasons.append(filters['structure_role'] + ' 空间群：' + (actual or '未核实'))
    if 'target_kelvin' in filters:
        sampling = facts.get('sampling') or {}
        if (sampling.get('target_start_kelvin'), sampling.get('target_end_kelvin')) != (filters['target_kelvin'], filters['target_kelvin']):
            reasons.append('未核实指定的恒定设定温度')
    return reasons


def frame_identity(record):
    meta = record.get('source_metadata') or {}
    source = (meta.get('vasp_facts') or {}).get('source') or {}
    index = record.get('frame_index')
    fingerprint = source.get('run_fingerprint')
    if fingerprint and index is not None:
        return ('vasp_run', fingerprint, index)
    hashes = source.get('sha256') or {}
    file_hashes = source.get('file_sha256') or {}
    digest = hashes.get('vasprun.xml') or file_hashes.get('vasprun.xml') or meta.get('sha256')
    # Only a recorded frame index establishes identity; a formula or directory cannot.
    return (digest, index) if digest and index is not None else None


def coverage(records):
    frames = {}
    for record in records:
        identity = frame_identity(record)
        if identity is not None:
            flags = frames.setdefault(identity, [False, False])
            labels = record.get('energy') is not None and record.get('forces') is not None
            flags[0] |= labels
            flags[1] |= labels and record.get('virial') is not None
    return {'records': len(records), 'verified_frames': len(frames),
            'unverified_records': sum(frame_identity(r) is None for r in records),
            'energy_force_frames': sum(v[0] for v in frames.values()),
            'also_virial_frames': sum(v[1] for v in frames.values())}
