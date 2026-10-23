"""MC adapter integration; synthetic harness and mock HTTP, no cloud/science."""
import asyncio,json
import httpx,pytest
from matcreator.xrd import jev,service as sm
from test_xrd_jev_controller import FakeHarness,response_body,CONFIG

def test_jev_config_redacts_and_preserves_key(tmp_path,monkeypatch):
    monkeypatch.delenv('TYPESAFE_API_KEY',raising=False)
    assert not jev.config(tmp_path)['configured']
    with pytest.raises(ValueError):jev.config(tmp_path,private=True)
    assert jev.config(tmp_path,{'api_key':'test-secret'})['configured']
    jev.config(tmp_path,{'model':'jev-latest','api_key':''})
    assert jev.config(tmp_path,private=True).api_key=='test-secret'
    assert 'test-secret' not in json.dumps(jev.config(tmp_path))
    with pytest.raises(ValueError):jev.config(tmp_path,{'base_url':'http://example.com'})
    with pytest.raises(ValueError):jev.config(tmp_path,{'base_url':'https://user:pass@example.com'})

@pytest.mark.parametrize('cancel',[False,True])
def test_service_jev_driver_scoped_lifecycle(tmp_path,monkeypatch,cancel):
    async def exercise():
        monkeypatch.setattr(sm,'ROOT',tmp_path)
        service=sm.XrdService();owner=service.create()['id']
        jev.config(tmp_path,{'api_key':'fake-key'})
        harness=FakeHarness(remaining=1,status='idle',baseline=False)
        async def info(_):return {'status':'idle'}
        monkeypatch.setattr(service,'info',info)
        monkeypatch.setattr(sm.multi,'configure',lambda value,options:{**value,'options':options})
        async def prepare(value,args):
            harness.status='running';return {'prepared':{**value,'run_id':'mock-run','state':harness.state()}}
        monkeypatch.setattr(sm.multi,'prepare_start',prepare)
        monkeypatch.setattr(sm.multi,'_read',lambda value:value)
        monkeypatch.setattr(sm.multi,'tool_summary',lambda doc:doc['value']['state'])
        cached={}
        async def operate(value,args,action):
            result=await harness.invoke_tool(CONFIG.agent_id,'operation:xrd_multiphase_'+action,{'target':'harness',**args})
            if action=='decision':cached['decision']=result
            return {'prepared':{**value,'state':harness.state()}}
        async def decision(*args):return cached['decision']
        monkeypatch.setattr(sm.multi,'_operate',operate);monkeypatch.setattr(sm.multi,'_decision',decision)
        entered=asyncio.Event()
        async def respond(request):
            entered.set()
            if cancel:await asyncio.Event().wait()
            body=json.loads(request.content);keys=body['questions']['next_action']['criteria']
            return httpx.Response(200,json=response_body(keys))
        original=jev.run
        async def runner(*args):
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:await original(*args,client=client)
        monkeypatch.setattr(jev,'run',runner)
        await service.action(owner,'multiphase_start',{'agent_node_id':'jev'})
        await asyncio.wait_for(entered.wait(),2)
        if cancel:
            with pytest.raises(ValueError,match='Jev'):await service.action(owner,'multiphase_start',{'agent_node_id':'jev'})
            await asyncio.wait_for(service.action(owner,'multiphase_stop',{}),2)
            assert harness.status=='cancelled'
        else:
            await asyncio.wait_for(service.jev_tasks[owner],2)
            assert harness.status=='completed'
        log=(service.path(owner).parent/'mock-run.jev.jsonl').read_text(encoding='utf-8')
        assert 'fake-key' not in log
        assert ('cancelled' if cancel else 'structured_decision') in log
        with pytest.raises(ValueError):await jev.Provider(service,owner).invoke_tool(owner,'operation:xrd_multiphase_read',{'target':'another'})
    asyncio.run(exercise())
