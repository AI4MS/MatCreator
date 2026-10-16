"""Versioned XRD journal in the SQLite card connected by xrd.history-store.

Working files remain engine scratch/recovery inputs. A bound SQL card is the
history source of truth. No model receives SQL write permission from this edge.
"""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from matcreator.xrd.compat import AgentNodeBehavior, NodeLifecycleTransaction

_contexts = {}
VERSION = 1


class _Create(NodeLifecycleTransaction):
    def __init__(self, inner, context, node):
        self.inner, self.context, self.node = inner, context, node

    async def commit(self):
        await self.inner.commit()
        _contexts[self.node.id] = self.context

    async def rollback(self, error):
        _contexts.pop(self.node.id, None)
        await self.inner.rollback(error)

    async def finalize(self):
        await self.inner.finalize()


class HistoryAgentBehavior(AgentNodeBehavior):
    async def on_startup(self, context, node):
        await super().on_startup(context, node)
        _contexts[node.id] = context

    async def on_shutdown(self, context, node):
        await super().on_shutdown(context, node)
        if _contexts.get(node.id) is context:
            _contexts.pop(node.id, None)

    async def prepare_create(self, context, node, request):
        return _Create(await super().prepare_create(context, node, request), context, node)


def database(owner):
    context = _contexts.get(owner)
    if context is None:
        return None
    targets = {e.target for e in context.nodes.list_edges_from(owner)
               if e.relationship == 'xrd.history-store'}
    if not targets:
        return None
    if len(targets) != 1:
        raise ValueError('XRD 工作台只能连接一个运行数据库')
    path = context.resources.node_storage_path(next(iter(targets))) / 'database.sqlite3'
    if not path.is_file():
        raise ValueError('XRD 运行数据库文件缺失；请恢复 SQL database 卡片')
    return path


SCHEMA = '''
CREATE TABLE IF NOT EXISTS xrd_schema(version INTEGER PRIMARY KEY, installed_at_ns INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS xrd_runs(
 owner_id TEXT NOT NULL, run_id TEXT NOT NULL, stage TEXT NOT NULL,
 status TEXT NOT NULL, created_at_ns INTEGER NOT NULL, updated_at_ns INTEGER NOT NULL,
 manifest_json TEXT NOT NULL CHECK(json_valid(manifest_json)),
 PRIMARY KEY(owner_id,run_id));
CREATE TABLE IF NOT EXISTS xrd_records(
 record_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, run_id TEXT NOT NULL,
 schema_version INTEGER NOT NULL CHECK(schema_version=1),
 kind TEXT NOT NULL CHECK(kind IN ('status','state','decision','evaluation','review','result','agent_report')),
 actor_id TEXT NOT NULL, recorded_at_ns INTEGER NOT NULL,
 payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
 FOREIGN KEY(owner_id,run_id) REFERENCES xrd_runs(owner_id,run_id));
CREATE INDEX IF NOT EXISTS xrd_records_run ON xrd_records(owner_id,run_id,recorded_at_ns);
CREATE TABLE IF NOT EXISTS xrd_artifacts(
 owner_id TEXT NOT NULL, run_id TEXT NOT NULL, name TEXT NOT NULL,
 sha256 TEXT NOT NULL, size_bytes INTEGER NOT NULL, content BLOB NOT NULL,
 PRIMARY KEY(owner_id,run_id,name),
 FOREIGN KEY(owner_id,run_id) REFERENCES xrd_runs(owner_id,run_id));
CREATE VIEW IF NOT EXISTS xrd_evaluations AS
 SELECT record_id,owner_id,run_id,actor_id,recorded_at_ns,
 json_extract(payload_json,'$.trial_id') AS trial_id,
 json_extract(payload_json,'$.candidate_ids') AS candidate_ids,
 json_extract(payload_json,'$.status') AS status,
 json_extract(payload_json,'$.score') AS score,
 json_extract(payload_json,'$.metrics.rwp_percent') AS rwp_percent,
 json_extract(payload_json,'$.metrics.rp_percent') AS rp_percent,
 json_extract(payload_json,'$.converged') AS converged,
 payload_json FROM xrd_records WHERE kind='evaluation';
CREATE VIEW IF NOT EXISTS xrd_agent_reports AS
 SELECT record_id,owner_id,run_id,actor_id,recorded_at_ns,
 json_extract(payload_json,'$.summary') AS summary,
 json_extract(payload_json,'$.conclusion') AS conclusion,
 json_extract(payload_json,'$.evidence') AS evidence,
 json_extract(payload_json,'$.limitations') AS limitations
 FROM xrd_records WHERE kind='agent_report';
CREATE VIEW IF NOT EXISTS xrd_decisions AS
 SELECT record_id,owner_id,run_id,actor_id,recorded_at_ns,
 json_extract(payload_json,'$.decision_id') AS decision_id,
 json_extract(payload_json,'$.selection_token') AS selection_token,
 json_extract(payload_json,'$.draft_candidate_ids') AS draft_candidate_ids,
 json_extract(payload_json,'$.status') AS status,payload_json
 FROM xrd_records WHERE kind='decision';
'''


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(',', ':'))


@contextmanager
def connection(path):
    # Never recreate a deleted database/card silently.
    con = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=rw', uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    try:
        con.execute('PRAGMA foreign_keys=ON')
        con.execute('BEGIN')
        with con:
            yield con
    finally:
        con.close()


def initialize(con):
    # Transactional DDL; executescript would implicitly commit the caller.
    for statement in SCHEMA.split(';'):
        if statement.strip():
            con.execute(statement)
    versions = [r[0] for r in con.execute('SELECT version FROM xrd_schema')]
    if versions and versions != [VERSION]:
        raise ValueError('不支持的 XRD SQL schema 版本')
    con.execute('INSERT OR IGNORE INTO xrd_schema VALUES (?,?)', (VERSION, time.time_ns()))


def record(con, owner, run_id, kind, actor, payload):
    body = dumps(payload)
    key = hashlib.sha256(dumps([owner, run_id, kind, actor, payload]).encode()).hexdigest()
    con.execute('INSERT OR IGNORE INTO xrd_records VALUES (?,?,?,?,?,?,?,?)',
                (key, owner, run_id, VERSION, kind, actor, time.time_ns(), body))
    return key


def capture(directory, *, artifacts=False):
    directory = Path(directory).resolve()
    for name in ('oaw.json', 'multiphase-state.json', 'result.json'):
        if not (directory / name).resolve().is_relative_to(directory):
            raise ValueError('归档来源不能指向运行目录之外')
    manifest = json.loads((directory / 'oaw.json').read_text(encoding='utf-8'))
    owner = manifest.get('source_owner_node_id') or manifest.get('agent_id')
    path = database(owner)
    if path is None:
        return False
    run_id = manifest['run_id']
    if directory.name != 'oaw-' + run_id:
        raise ValueError('运行编号与目录不匹配')
    with connection(path) as con:
        initialize(con)
        con.execute('INSERT INTO xrd_runs VALUES (?,?,?,?,?,?,?) ON CONFLICT(owner_id,run_id) '
                    'DO UPDATE SET status=excluded.status,updated_at_ns=excluded.updated_at_ns,manifest_json=excluded.manifest_json',
                    (owner, run_id, manifest.get('workflow_stage') or 'search', manifest['status'],
                     manifest.get('created_at_ns') or 0, time.time_ns(), dumps(manifest)))
        record(con, owner, run_id, 'status', manifest['agent_id'], manifest)
        state_path = directory / 'multiphase-state.json'
        if state_path.is_file():
            state = json.loads(state_path.read_text(encoding='utf-8'))
            if state.get('run_id') != run_id or state.get('options', {}).get('owner_node_id') != owner:
                raise ValueError('多相记录来源不匹配')
            record(con, owner, run_id, 'state', manifest['agent_id'],
                   {k:state[k] for k in ('status','progress','timing','review_started_at_ns','error','stop_reason') if k in state})
            for kind, values in [('decision', state.get('decision_requests', [])),
                                 ('evaluation', state.get('trials', [])),
                                 ('review', state.get('pywpem_review', {}).get('reviews', []))]:
                for value in values:
                    if kind == 'evaluation':
                        value = evaluation(value)
                    elif kind == 'review':
                        value = {**value, 'review_started_at_ns': state.get('review_started_at_ns')}
                    record(con, owner, run_id, kind, manifest['agent_id'], value)
            for value in state.get('baseline', {}).get('trials', []):
                record(con, owner, run_id, 'evaluation', 'BO_baseline', evaluation(value))
        result = directory / 'result.json'
        if result.is_file():
            result_value = json.loads(result.read_text(encoding='utf-8'))
            record(con, owner, run_id, 'result', manifest['agent_id'], result_value)
            if not state_path.is_file():
                for value in result_value.get('candidates', []):
                    record(con, owner, run_id, 'evaluation', manifest['agent_id'], evaluation(value))
        # Small current files are persisted during progress; full artifacts at terminal/migration.
        paths = directory.rglob('*') if artifacts else [directory / n for n in
            ('oaw.json', 'input.json', 'multiphase-state.json', 'result.json', 'progress.json')]
        for source in paths:
            if not source.is_file() or not source.resolve().is_relative_to(directory) or source.name.endswith('.tmp'):
                continue
            raw = source.read_bytes()
            con.execute('INSERT INTO xrd_artifacts VALUES (?,?,?,?,?,?) ON CONFLICT(owner_id,run_id,name) '
                        'DO UPDATE SET sha256=excluded.sha256,size_bytes=excluded.size_bytes,content=excluded.content '
                        'WHERE sha256<>excluded.sha256',
                        (owner, run_id, source.relative_to(directory).as_posix(), hashlib.sha256(raw).hexdigest(), len(raw), raw))
    return True


def evaluation(value):
    from .results_context import _trial
    return {**_trial(value), **{k:value[k] for k in
        ('optimizer_proposal', 'elapsed_seconds', 'objective_definition', 'quality_score', 'filename', 'metadata') if k in value}}


class AgentReport(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)
    schema_version: Literal[1]
    run_id: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=12000)
    evidence: list[str] = Field(min_length=1, max_length=50)
    limitations: list[str] = Field(min_length=1, max_length=50)
    conclusion: Literal['supported', 'inconclusive', 'failed']


def submit_report(context, arguments):
    report = AgentReport.model_validate(arguments)
    if not context.actor_id:
        raise ValueError('报告必须由已授权 Agent 提交')
    path = database(context.node_id)
    if path is None:
        raise ValueError('请先连接 XRD 运行数据库')
    with connection(path) as con:
        initialize(con)
        if not con.execute('SELECT 1 FROM xrd_runs WHERE owner_id=? AND run_id=?',
                           (context.node_id, report.run_id)).fetchone():
            raise ValueError('运行不存在或不属于此工作台')
        for name in report.evidence:
            if not con.execute('SELECT 1 FROM xrd_artifacts WHERE owner_id=? AND run_id=? AND name=?',
                               (context.node_id, report.run_id, name)).fetchone():
                raise ValueError('证据必须引用此运行中已归档的文件名')
        key = record(con, context.node_id, report.run_id, 'agent_report', context.actor_id, report.model_dump())
    return {'record_id': key, 'schema_version': VERSION, 'run_id': report.run_id, 'stored': True}


def migrate(context, arguments):
    if arguments:
        raise ValueError('迁移不接受路径或运行编号')
    if database(context.node_id) is None:
        raise ValueError('请先连接 XRD 运行数据库')
    from .history import _root, _owned
    imported = 0
    for manifest_path in _root().glob('oaw-*/oaw.json'):
        if context.cancelled.is_set():
            raise ValueError('迁移已取消；已提交的运行可安全重试')
        try:
            directory, _ = _owned(_root(), context.node_id, manifest_path.parent.name.removeprefix('oaw-'))
        except (ValueError, OSError):
            continue
        capture(directory, artifacts=True)
        imported += 1
    return {'schema_version': VERSION, 'imported_runs': imported}


def report_contract(owner):
    path = database(owner)
    if path is None:
        return {'storage': 'unbound', 'report_required': False}
    with connection(path) as con:
        initialize(con)
        runs = []
        for row in con.execute('SELECT run_id,stage,status FROM xrd_runs WHERE owner_id=? ORDER BY created_at_ns DESC LIMIT 10', (owner,)):
            item = dict(row)
            item['evidence_files'] = [r[0] for r in con.execute('SELECT name FROM xrd_artifacts WHERE owner_id=? AND run_id=? ORDER BY name LIMIT 100', (owner, row['run_id']))]
            runs.append(item)
    return {'storage': 'sqlite', 'schema_version': VERSION, 'report_required': True,
            'instruction': 'Submit factual analysis via xrd_submit_report before the final response. Cite evidence_files; include limitations. Do not invent run IDs or evidence.',
            'report_schema': AgentReport.model_json_schema(), 'runs': runs}


def records(context, arguments):
    path = database(context.node_id)
    if path is None:
        return {'items': [], 'total': 0}
    offset = arguments.get('offset', 0)
    if type(offset) is not int or offset < 0:
        raise ValueError('无效的记录分页')
    kind = arguments.get('kind', 'evaluation')
    if kind not in {'status', 'state', 'decision', 'evaluation', 'review', 'result', 'agent_report'}:
        raise ValueError('无效的记录类型')
    with connection(path) as con:
        initialize(con)
        params = (context.node_id, arguments.get('run_id'), kind)
        total = con.execute('SELECT count(*) FROM xrd_records WHERE owner_id=? AND run_id=? AND kind=?', params).fetchone()[0]
        rows = con.execute('SELECT record_id,schema_version,kind,actor_id,recorded_at_ns,payload_json FROM xrd_records WHERE owner_id=? AND run_id=? AND kind=? ORDER BY recorded_at_ns DESC,record_id LIMIT 20 OFFSET ?', (*params, offset)).fetchall()
    from .results_context import _trial
    items = []
    for row in rows:
        item = dict(row)
        payload = json.loads(item.pop('payload_json'))
        # Keep plot/CIF arrays in downloadable artifacts, not history UI responses.
        if kind == 'evaluation':
            payload = _trial(payload)
        elif kind == 'review':
            payload = {'review_started_at_ns': payload.get('review_started_at_ns'), 'full': _trial(payload.get('full')), 'removals': [_trial(r) for r in payload.get('removals', [])]}
        elif kind == 'result':
            payload = {k:v for k,v in payload.items() if k in {'mode','stage','status','interpretation','error','match_run_id'}}
        item['payload'] = payload
        items.append(item)
    return {'items': items, 'total': total, 'offset': offset}
