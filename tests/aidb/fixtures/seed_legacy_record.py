"""Seed a historical AIDB row for the same-frame compatibility regression."""
import json
import os
from pathlib import Path
import sys

script = Path(sys.argv[1])
sys.path.insert(0, str(script.parent))
from aidb_client import discover_aidb_python, discover_aidb_root

if os.environ.get('_MATCREATOR_TEST_AIDB') != '1':
    root = discover_aidb_root()
    python = discover_aidb_python(root)
    env = dict(os.environ, _MATCREATOR_TEST_AIDB='1')
    env['PYTHONPATH'] = str(root) + os.pathsep + env.get('PYTHONPATH', '')
    os.execve(str(python), [str(python), '-B', str(Path(__file__).resolve()), *sys.argv[1:]], env)

from aidb_ops import database_lock, workspace_config
from ai_ready_db.ai_ready_db_Renaissance.local.local_workspace import LocalASEWorkspace
from ai_ready_db.core.normalization import normalize_json_record

config, _ = workspace_config(sys.argv[2], reading=False)
record = normalize_json_record(json.load(sys.stdin), 'prototype_vasp_files')
with database_lock(config):
    LocalASEWorkspace(config).write_record(record, 'fine_tune_working', 'local_calculation', 'local_only')
