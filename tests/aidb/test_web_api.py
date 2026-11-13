from fastapi import FastAPI
from fastapi.testclient import TestClient
from matcreator.aidb.service import SessionAIDB
from web.aidb_api import create_aidb_router


def test_result_endpoint_keeps_shared_workspace_sessions_and_owners_separate(tmp_path):
    config=tmp_path/'config.json'; config.write_text('{}')
    database=lambda c,w,q: ([{'record_id':'a','formula_reduced':'Si'}],{'conditions':q,'match_count':1,'complete':True})
    first=SessionAIDB(tmp_path,'first',query_database=database)
    second=SessionAIDB(tmp_path,'second',query_database=database)
    first.bind(config); second.bind(config)
    reference=first.publish(first.query({'formula':'Si'}))
    app=FastAPI()
    app.include_router(create_aidb_router(lambda owner, session: owner == 'owner', lambda session, user_id: tmp_path))
    client=TestClient(app)
    path='/api/users/owner/sessions/first/aidb/results/'+reference['result_id']
    assert client.get(path).json()['records'][0]['formula_reduced']=='Si'
    assert client.get(path.replace('/owner/','/other/')).status_code==404
    assert client.get(path.replace('/first/','/second/')).status_code==409
    first.bind(config)
    response=client.get(path)
    assert response.status_code==409
    assert response.json()['detail']['status']=='stale_binding'


def test_matching_session_ids_use_the_requested_owners_workspace(tmp_path):
    workdirs = {}
    references = {}
    for owner, formula in [('alice', 'Si'), ('bob', 'Ge')]:
        workspace = tmp_path / owner
        workspace.mkdir()
        config = workspace / 'config.json'
        config.write_text('{}')
        service = SessionAIDB(workspace, 'shared-id', query_database=lambda c, w, q: (
            [{'record_id': q['formula'], 'formula_reduced': q['formula']}],
            {'conditions': q, 'match_count': 1, 'complete': True},
        ))
        service.bind(config)
        workdirs[owner] = workspace
        references[owner] = service.publish(service.query({'formula': formula}))['result_id']

    app = FastAPI()
    app.include_router(create_aidb_router(
        lambda owner, session: owner in workdirs and session == 'shared-id',
        lambda session, user_id: workdirs[user_id],
    ))
    client = TestClient(app)

    for owner, formula in [('alice', 'Si'), ('bob', 'Ge')]:
        base = f'/api/users/{owner}/sessions/shared-id/aidb/results/'
        response = client.get(base + references[owner])
        assert response.status_code == 200, response.text
        assert response.json()['records'][0]['formula_reduced'] == formula
        other = 'bob' if owner == 'alice' else 'alice'
        assert client.get(base + references[other]).status_code == 409
