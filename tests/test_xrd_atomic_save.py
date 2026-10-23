import json
from pathlib import Path

import pytest

from matcreator.xrd.vendor import multiphase_snapshot as snapshot


def test_transient_destination_lock_preserves_old_json_until_commit(tmp_path, monkeypatch):
    target = tmp_path / 'state.json'
    target.write_text('{"old": true}', encoding='utf-8')
    replace = Path.replace
    calls = []
    def locked_once(source, destination):
        calls.append(source)
        if len(calls) == 1:
            assert json.loads(target.read_text()) == {'old': True}
            raise PermissionError('destination temporarily open')
        return replace(source, destination)
    monkeypatch.setattr(Path, 'replace', locked_once)
    monkeypatch.setattr(snapshot.time, 'sleep', lambda _: None)
    snapshot._save(target, {'new': True})
    assert len(calls) == 2
    assert json.loads(target.read_text()) == {'new': True}
    assert list(tmp_path.iterdir()) == [target]


def test_persistent_lock_fails_boundedly_without_losing_old_state(tmp_path, monkeypatch):
    target = tmp_path / 'state.json'
    target.write_text('{"old": true}', encoding='utf-8')
    attempts = []
    def locked(source, destination):
        attempts.append(source)
        raise PermissionError('locked')
    monkeypatch.setattr(Path, 'replace', locked)
    monkeypatch.setattr(snapshot.time, 'sleep', lambda _: None)
    with pytest.raises(PermissionError):
        snapshot._save(target, {'new': True})
    assert len(attempts) == 8
    assert json.loads(target.read_text()) == {'old': True}
    assert list(tmp_path.iterdir()) == [target]
