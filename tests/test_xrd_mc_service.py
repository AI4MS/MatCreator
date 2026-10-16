"""Integration checks use a tiny synthetic spectrum, never existing research inputs."""
import asyncio, base64, json, math, os
from pathlib import Path
import pytest
from matcreator.xrd import service as module

@pytest.fixture
def xrd(tmp_path,monkeypatch):
    monkeypatch.setattr(module,'ROOT',tmp_path)
    monkeypatch.setenv('OAW_XRD_RUN_ROOT',str(tmp_path/'runs'))
    monkeypatch.setenv('OAW_XRD_ROOT',str(tmp_path/'engine'))
    monkeypatch.setenv('OAW_XRD_PYTHON',os.environ.get('MC_TEST_XRD_PYTHON',os.sys.executable))
    instance=module.XrdService();monkeypatch.setattr(module,'service',instance)
    return instance

def test_workspace_ids_and_revision_guards(xrd):
    async def run():
        w=xrd.create(); owner=w['id']; target=xrd.ensure(owner,'pattern')
        assert xrd.ensure(owner,'pattern')==target
        with pytest.raises(ValueError): xrd.load('../escape')
        with pytest.raises(ValueError): await xrd.action(owner,'document',{'target':target,'action':'import','revision':7,'arguments':{}})
        with pytest.raises(ValueError): await xrd.action(owner,'start',{})
        with pytest.raises(ValueError): await xrd.action(owner,'update_config',{'library_top_n':0})
        assert xrd.load(owner)['inputs'][target]['revision']==0
    asyncio.run(run())

def test_actual_search_is_idempotent_and_history_survives_new_input(xrd):
    async def run():
        w=xrd.create('Synthetic integration test'); owner=w['id'];target=xrd.ensure(owner,'pattern')
        points=[[10+i*.01,100+1000*math.exp(-((10+i*.01-20.04)/.07)**2)+500*math.exp(-((10+i*.01-30)/.08)**2)] for i in range(3001)]
        raw='\n'.join(f'{x},{y}' for x,y in points).encode()
        await xrd.action(owner,'document',{'target':target,'action':'import','revision':0,'arguments':{'filename':'synthetic.csv','source_base64':base64.b64encode(raw).decode()}})
        reference=xrd.ensure(owner,'reference');d=xrd.load(owner)
        d['inputs'][reference]={'revision':1,'value':{'kind':'reference','filename':'synthetic-reference','sha256':'test','metadata':{},'peaks':[{'two_theta':20,'d':1.540593/(2*math.sin(math.radians(10))),'intensity':100,'hkl':[1,0,0]}]}}
        xrd.persist(d)
        receipt=await xrd.action(owner,'start',{'request_id':'test-search','config':{'library_engine':'native'}})
        repeat=await xrd.action(owner,'start',{'request_id':'test-search','config':{'library_engine':'native'}})
        assert receipt==repeat
        with pytest.raises(ValueError): await xrd.action(owner,'start',{'request_id':'test-search','config':{'library_top_n':4}})
        await asyncio.wait_for(xrd.tasks[owner],60)
        info=await xrd.info(owner)
        assert not info['error'],info['error']
        result=info['details']['result']
        assert len(result['observed_peaks'])==2
        assert len(result['unexplained_peaks'])==1
        assert result['candidates'][0]['fit_input_status']=='missing_cif'
        assert abs(result['candidates'][0]['matches'][0]['delta']-.04)<.011
        history=xrd.resource(owner,'history_list',{})
        assert history['total']==1 and history['items'][0]['status']=='completed'
        # Simulate losing the HTTP owner after its supervised worker completed.
        run_dir=Path(os.environ['OAW_XRD_RUN_ROOT'])/('oaw-'+receipt['run_id'])
        manifest=json.loads((run_dir/'oaw.json').read_text(encoding='utf-8'))
        manifest['status']='running';module.save(run_dir/'oaw.json',manifest)
        state=xrd.load(owner);state['active_run_id']=receipt['run_id'];xrd.persist(state);xrd.tasks.clear()
        assert (await xrd.info(owner))['status']=='idle'
        assert json.loads((run_dir/'oaw.json').read_text(encoding='utf-8'))['recovered'] is True
        assert xrd.resource(owner,'history_list',{})['total']==1
        other=xrd.create()['id']
        with pytest.raises(ValueError): xrd.resource(other,'history_inspect',{'run_id':receipt['run_id']})
        await xrd.action(owner,'document',{'target':target,'action':'import','revision':1,'arguments':{'filename':'second.csv','source_base64':base64.b64encode(raw).decode()}})
        assert xrd.resource(owner,'history_list',{})['total']==1
        assert 'result' not in (await xrd.info(owner))['details']
    asyncio.run(run())

def test_detached_run_blocks_duplicate_submission(xrd):
    async def run():
        data=xrd.create();data['active_run_id']='previous-run';xrd.persist(data)
        assert (await xrd.info(data['id']))['status']=='waiting'
        with pytest.raises(ValueError,match='已有运行'):
            await xrd.action(data['id'],'start',{'request_id':'not-a-retry'})
    asyncio.run(run())

def test_multiphase_blocks_overwrite_and_single_phase_start(xrd):
    async def run():
        data=xrd.create();data['harness']['state']['status']='running';xrd.persist(data)
        for action,args in [('update_config',{}),('ensure_input',{'kind':'pattern'}),('document',{}),('start',{'request_id':'other'})]:
            with pytest.raises(ValueError,match='多相分析'):
                await xrd.action(data['id'],action,args)
    asyncio.run(run())

def test_multiphase_stop_interrupts_pending_preparation(xrd,monkeypatch):
    async def run():
        owner=xrd.create()['id'];entered=asyncio.Event()
        async def waiting(*args):
            entered.set();await asyncio.Event().wait()
        monkeypatch.setattr(xrd,'_action',waiting)
        pending=asyncio.create_task(xrd.action(owner,'multiphase_start',{}))
        await entered.wait()
        await asyncio.wait_for(xrd.action(owner,'multiphase_stop',{}),1)
        assert pending.cancelled() and owner not in xrd.operations
    asyncio.run(run())

def test_http_api_imports_structure_with_mc_format_and_disables_server_mode(tmp_path,monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from web.xrd_api import router
    app=FastAPI();app.include_router(router)
    from ase import Atoms
    from ase.io import write
    import io
    atoms=Atoms('Si',positions=[[0,0,0]],cell=[5.43]*3,pbc=True)
    text=io.BytesIO();write(text,atoms,format='cif')
    client=TestClient(app)
    response=client.post('/api/xrd/structure',json={'source_base64':base64.b64encode(text.getvalue()).decode()})
    assert response.status_code==200,response.text
    assert response.json()['n_atoms']==1 and 'Lattice=' in response.json()['structure_string']
    bad=client.post('/api/xrd/structure',json={'source_base64':'garbage!!!'})
    assert bad.status_code==400
    monkeypatch.setenv('MATCREATOR_MODE','server')
    assert client.get('/api/xrd/workspaces').status_code==403

def test_review_http_contract_preserves_selected_combinations(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from web.xrd_api import router,service
    received=[]
    async def action(owner,action,args):
        received.append((owner,action,args));return {'status':'running'}
    monkeypatch.setattr(service,'action',action)
    monkeypatch.setenv('MATCREATOR_MODE','local')
    app=FastAPI();app.include_router(router);client=TestClient(app)
    path='/api/xrd/workspaces/existing/command'
    old=client.post(path,json={'action':'multiphase_review','arguments':'existing-run'})
    assert old.status_code==422 and not received
    args={'run_id':'existing-run','combinations':[['phase-a','phase-b'],['phase-c']]}
    good=client.post(path,json={'action':'multiphase_review','arguments':args})
    assert good.status_code==200
    assert received==[('existing','multiphase_review',args)]


def test_review_reports_its_own_error_and_retains_controller_history(xrd,monkeypatch):
    data=xrd.create();data['jev_error']='previous controller failure';xrd.persist(data)
    state={'pywpem_review':{'status':'running'}}
    monkeypatch.setattr(module.multi,'_ui_overview',lambda *_:{'state':state})
    projected=xrd.multiphase(data['id'])
    assert projected['error'] is None
    assert projected['jev_error']=='previous controller failure'
    state['pywpem_review']={'status':'failed','error':'current review failure'}
    assert xrd.multiphase(data['id'])['error']=='current review failure'
    state['pywpem_review']={'status':'pending'}
    assert xrd.multiphase(data['id'])['error']=='previous controller failure'
