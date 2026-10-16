"""Local TypeSafe configuration and workspace-scoped controller bridge."""
import asyncio, json, os, re, time
from types import SimpleNamespace
from .jev_controller import TypeSafeModel, execute_typesafe, _ACTIONS, _PREFIX

DEFAULTS={'model':'jev-1.13.0','base_url':'https://api.typesafe.ai','api_key_env':'TYPESAFE_API_KEY'}

def config(root, changes=None, *, private=False):
    path=root/'jev-config.json'
    value={**DEFAULTS,**(json.loads(path.read_text(encoding='utf-8')) if path.exists() else {})}
    if changes is not None:
        for key in DEFAULTS:
            if key in changes:value[key]=str(changes[key]).strip()
        if changes.get('api_key'):value['api_key']=str(changes['api_key']).strip()
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',value['api_key_env']):raise ValueError('无效的凭据环境变量名')
        if not re.fullmatch(r'jev-[A-Za-z0-9.-]+',value['model']):raise ValueError('请输入 Jev 模型 ID')
        endpoint=TypeSafeModel(value['model'],value['base_url']).endpoint
        if not endpoint.startswith('https://'):raise ValueError('Jev 连接必须使用 HTTPS')
        root.mkdir(parents=True,exist_ok=True)
        temp=path.with_suffix('.tmp');temp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(path)
    key=value.get('api_key') or os.environ.get(value['api_key_env'],'')
    if private:
        if not key:raise ValueError('请先在 Jev 模型设置中配置 API key')
        return TypeSafeModel(value['model'],value['base_url'],key)
    return {k:value[k] for k in DEFAULTS}|{'configured':bool(key)}

class Provider:
    def __init__(self,service,owner):self.service=service;self.owner=owner
    async def invoke_tool(self,agent_id,capability,args):
        if agent_id!=self.owner or args.get('target')!=self.owner or capability not in {'operation:'+_PREFIX+a for a in _ACTIONS}:
            raise ValueError('Jev 工具越过当前工作台范围')
        action=capability.removeprefix('operation:'+_PREFIX)
        if action=='read':
            from .vendor import multiphase_object as multi
            return multi.tool_summary({'value':multi._read(self.service.load(self.owner)['harness'])})
        return await self.service._action(self.owner,'multiphase_'+action,{k:v for k,v in args.items() if k!='target'})

async def run(service,owner,model,client=None):
    run_id=service.load(owner)['harness']['run_id']
    path=service.path(owner).parent/(run_id+'.jev.jsonl')
    definitions=[SimpleNamespace(name=_PREFIX+a,capability_id='operation:'+_PREFIX+a,input_schema={'required':['target'],'properties':{'target':{'enum':[owner]}}}) for a in _ACTIONS]
    cfg=SimpleNamespace(agent_id=owner,system_instruction='Choose the next legal ADD or SUBMIT to minimize the measured XRD objective. Preserve plausible weak impurity hypotheses. Model confidence is not phase identity probability.')
    def record(kind,payload):
        with path.open('a',encoding='utf-8') as out:out.write(json.dumps({'time_ns':time.time_ns(),'kind':kind,'payload':payload},ensure_ascii=False)+'\n')
    try:
        async for event in execute_typesafe(Provider(service,owner),cfg,SimpleNamespace(run_id=run_id),model,definitions,client=client):record(event.kind,event.payload)
    except asyncio.CancelledError:
        record('cancelled',{'reason':'controller_cancelled'});raise
    except Exception as exc:
        # The controller sanitizes provider errors; never persist arbitrary request headers.
        message=str(exc) if isinstance(exc,ValueError) else type(exc).__name__
        record('failed',{'error':message})
        data=service.load(owner);data['jev_error']=message;service.persist(data)
