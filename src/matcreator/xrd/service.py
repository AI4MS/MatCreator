"""Persistent XRD workspaces and a single execution owner for UI and Agent tools."""
from __future__ import annotations
import asyncio, base64, hashlib, json, os, re, sqlite3, sys, time, uuid
from pathlib import Path
from types import SimpleNamespace
from .vendor import MatchConfig, MatchWorkspaceDocument, InputDocument, configure_library
from .vendor.documents import import_input, associate
from .vendor.runtime import XRDRuntime
from .vendor import structures, history, sql_history, multiphase_object as multi

ROOT = Path(os.environ.get('MATCREATOR_XRD_HOME', str(Path(os.environ.get('MATCREATOR_HOME', '~/.matcreator')).expanduser() / 'xrd'))).resolve()
os.environ.setdefault('OAW_XRD_ROOT', str(ROOT / 'engine-runtime'))
os.environ.setdefault('OAW_XRD_RUN_ROOT', str(ROOT / 'runs'))
os.environ.setdefault('OAW_XRD_PYTHON', os.environ.get('MATCREATOR_XRD_PYTHON', sys.executable))
ROOT.mkdir(parents=True, exist_ok=True)
# Reuse the original journal implementation with MC-owned per-workspace storage.
def journal(owner):
    validate_id(owner)
    return ROOT / 'workspaces' / owner / 'history.sqlite3'
sql_history.database = journal

def validate_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', value):
        raise ValueError('无效的 XRD 工作台编号')
    return value

def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)

class Capabilities:
    async def list_tools(self, owner):
        workspace = service.load(owner)
        return [SimpleNamespace(name='read_xrd_input', capability_id='inputs', input_schema={'properties':{'target':{'enum':list(workspace['inputs'])}}})]
    async def invoke_tool(self, owner, capability, args):
        doc = service.load(owner)['inputs'][args['target']]
        return {'node_id': args['target'], **doc}
    async def read_own_document(self, owner):
        return service.load(owner)['document']

class XrdService:
    def __init__(self):
        self.runtime = XRDRuntime(Capabilities())
        self.tasks = {}
        self.locks = {}
        self.recovery_locks = {}
        self.operations = {}
        self.jev_tasks = {}
    def path(self, owner):
        return ROOT / 'workspaces' / validate_id(owner) / 'workspace.json'
    def load(self, owner):
        p=self.path(owner)
        if not p.exists(): raise KeyError('XRD 工作台不存在')
        return json.loads(p.read_text(encoding='utf-8'))
    def persist(self, data):
        save(self.path(data['id']), data)
    def create(self, name='XRD 工作台'):
        owner='xrd-'+uuid.uuid4().hex
        data={'id':owner,'name':name[:100], 'created_at_ns':time.time_ns(), 'config':MatchConfig().model_dump(),
              'document':{'value':MatchWorkspaceDocument().model_dump(),'revision':0},'inputs':{},
              'harness':multi.HarnessDocument().model_dump(), 'requests':{}, 'active_run_id':''}
        self.persist(data)
        with sqlite3.connect(journal(owner)) as con: sql_history.initialize(con)
        return data
    def list(self):
        return [{'id':d['id'],'name':d['name'],'created_at_ns':d['created_at_ns']} for p in (ROOT/'workspaces').glob('*/workspace.json') if (d:=json.loads(p.read_text(encoding='utf-8')))]
    def configuration(self, data):
        return SimpleNamespace(agent_id=data['id'],provider_config=data['config'])
    async def info(self, owner):
        async with self.recovery_locks.setdefault(owner, asyncio.Lock()):
            return await self._info(owner)
    async def _info(self, owner):
        data=self.load(owner)
        await self.runtime.create_agent(self.configuration(data))
        info=await self.runtime.get_agent(owner,include_archive=False)
        last=info.details.get('last_run',{})
        if last.get('run_id') and last.get('status')!='running':
            info.details['archive_url']=f"/api/xrd/workspaces/{owner}/archive/{last['run_id']}"
        active=self.tasks.get(owner)
        status='running' if active and not active.done() else 'idle'
        if data.get('error') and status=='idle': status='error'
        # A detached worker remains busy until its durable manifest has a terminal state.
        if data.get('active_run_id') and (not active or active.done()):
            run=Path(os.environ['OAW_XRD_RUN_ROOT']) / ('oaw-'+data['active_run_id'])
            if (run/'oaw.json').exists():
                m=json.loads((run/'oaw.json').read_text(encoding='utf-8'))
                if m.get('status')=='running':
                    status='waiting'
                    outcome=run/'worker-status.json'
                    if outcome.exists():
                        final=json.loads(outcome.read_text(encoding='utf-8'))
                        if final.get('status') in {'completed','failed'}:
                            m.update(status=final['status'],finished_at_ns=final['finished_at_ns'],recovered=True)
                            if final['status']=='completed' and m.get('workflow_stage')=='search':
                                from .vendor.frames import search_frames
                                await search_frames(run,json.loads((run/'result.json').read_text(encoding='utf-8')))
                            save(run/'oaw.json',m); sql_history.capture(run,artifacts=True)
                            data['active_run_id']='';self.persist(data)
                            return await self._info(owner)
            else: status='waiting'
        return {'card':{'id':owner,'name':data['name'],'type':'xrd.match','config':data['config'],'status':status},
                'status':status,'details':info.details,'error':data.get('error','')}
    def inputs(self, owner):
        d=self.load(owner)
        values=[{'id':key,'name':v['value'].get('filename') or v['value']['kind'], 'kind':v['value']['kind'],
                 'ready':bool(v['value'].get('points') or v['value'].get('peaks') or v['value'].get('text')),
                 'detail':v['value'].get('sha256','')} for key,v in d['inputs'].items()]
        if d['document']['value'].get('path'): values.append({'id':owner,'name':'参考谱库','kind':'library','ready':True,'detail':d['document']['value'].get('filename','')})
        return values
    def ensure(self, owner, kind):
        if kind not in {'pattern','reference','cif','library'}: raise ValueError('无效输入类型')
        if kind=='library': return owner
        data=self.load(owner)
        if kind=='pattern':
            existing=next((key for key,d in data['inputs'].items() if d['value']['kind']==kind),None)
            if existing: return existing
        key=kind+'-'+uuid.uuid4().hex
        data['inputs'][key]={'value':InputDocument(kind=kind).model_dump(),'revision':0}
        self.persist(data)
        return key
    async def action(self, owner, action, args):
        validate_id(owner)
        if action=='multiphase_stop':
            driver=self.jev_tasks.get(owner)
            if driver and driver is not asyncio.current_task() and not driver.done():
                driver.cancel(); await asyncio.gather(driver,return_exceptions=True)
            pending=self.operations.get(owner)
            if pending and not pending.done():
                pending.cancel()
                await asyncio.gather(pending,return_exceptions=True)
            if not self.load(owner)['harness'].get('run_id'):
                return self.multiphase(owner)
        driver=self.jev_tasks.get(owner)
        if driver and not driver.done() and driver is not asyncio.current_task() and action.startswith('multiphase_') and action!='multiphase_stop':
            raise ValueError('Jev 正在控制此工作台，请等待或停止当前运行')
        tracked=action in {'multiphase_start','multiphase_evaluate','multiphase_baseline','multiphase_review'}
        current=asyncio.current_task()
        if tracked:
            pending=self.operations.get(owner)
            if pending and not pending.done(): raise ValueError('此工作台正在处理多相操作，请等待或停止')
            self.operations[owner]=current
        try:
            return await self._action(owner,action,args)
        finally:
            if tracked and self.operations.get(owner) is current: self.operations.pop(owner,None)
    async def _action(self, owner, action, args):
        lock=self.locks.setdefault(owner, asyncio.Lock())
        async with lock:
            data=self.load(owner)
            if action in {'update_config','ensure_input','document','start'}:
                if multi._read(data['harness']).get('state',{}).get('status')=='running':
                    raise ValueError('多相分析仍在运行，请先停止再修改输入或启动其他分析')
            if action=='update_config':
                if (await self.info(owner))['status'] in {'running','waiting'}: raise ValueError('请等待当前分析结束')
                data['config']=MatchConfig.model_validate({**data['config'],**args}).model_dump()
                self.persist(data); return data['config']
            if action=='ensure_input': return self.ensure(owner,args.get('kind','pattern'))
            if action=='document':
                if (await self.info(owner))['status'] in {'running','waiting'}: raise ValueError('请先停止当前分析再修改输入')
                key=args.get('target') or owner
                doc=data['document'] if key==owner else data['inputs'][key]
                if args.get('revision') is not None and args['revision']!=doc['revision']: raise ValueError('输入已更新，请刷新后重试')
                operation=args['action']; value=doc['value']; params=args.get('arguments',{})
                if operation=='import':
                    if (await self.info(owner))['status'] in {'running','waiting'}: raise ValueError('请先停止当前分析再导入新输入')
                    value=import_input(value,params)
                    if value['kind']=='pattern':
                        data['config'].update(workflow_started_at_ms=int(time.time()*1000),workflow_stage='search',workflow_match_run_id='',workflow_preopt_run_id='',selected_candidate_ids=[])
                        data['harness']=multi.HarnessDocument(next_options=data['harness'].get('next_options',{})).model_dump()
                elif operation=='configure': value=await asyncio.to_thread(configure_library,value,params)
                elif operation=='fetch_cod': value=structures.apply_cod_structure(value,await structures.prepare_cod_structure(value,params))
                elif operation=='associate': value=associate(value,params)
                else: raise ValueError('不支持的输入操作')
                doc.update(value=value,revision=doc['revision']+1); self.persist(data); return doc
            if action=='start':
                request=args.get('request_id')
                if not isinstance(request,str) or not request or len(request)>128: raise ValueError('启动分析必须提供稳定 request_id')
                digest=hashlib.sha256(json.dumps(args.get('config',{}),sort_keys=True).encode()).hexdigest()
                if request in data['requests']:
                    receipt=data['requests'][request]
                    if receipt['digest']!=digest: raise ValueError('同一 request_id 对应了不同参数')
                    return receipt
                if (await self.info(owner))['status'] in {'running','waiting'}: raise ValueError('此工作台已有运行中的任务；请读取现有状态')
                config=MatchConfig.model_validate({**data['config'],**args.get('config',{})}).model_dump()
                if config['low_angle']>=config['high_angle']: raise ValueError('角度下限必须小于上限')
                data['config']=config; data['error']=''; run_id=uuid.uuid4().hex
                receipt={'run_id':run_id,'request_id':request,'digest':digest}
                data['requests'][request]=receipt; data['active_run_id']=run_id; self.persist(data)
                self.tasks[owner]=asyncio.create_task(self.execute(owner,run_id,config))
                return receipt
            if action=='stop':
                task=self.tasks.get(owner)
                if task and not task.done():
                    task.cancel()
                    await asyncio.gather(task,return_exceptions=True)
                elif (await self.info(owner))['status']=='waiting':
                    raise ValueError('该任务来自先前服务进程；需核对运行记录后管理，不能猜测或重提')
                return await self.info(owner)
            if action=='multiphase_options':
                data['harness']=multi.save_options(data['harness'],args)
            elif action=='multiphase_start':
                if (await self.info(owner))['status'] in {'running','waiting'}: raise ValueError('单相分析仍在运行')
                from . import jev
                use_jev=args.get('agent_node_id')=='jev'
                jev_model=jev.config(ROOT,private=True) if use_jev else None
                options={k:v for k,v in args.items() if k in multi.MultiphaseConfig.model_fields}
                options.update(owner_node_id=owner,optimizer_label=('Jev '+jev_model.model) if use_jev else 'MatCreator Agent')
                data['harness']=multi.configure(data['harness'],options)
                self.persist(data)
                prepared=await multi.prepare_start(data['harness'],{'_agent_node_id':'jev' if use_jev else 'matcreator'})
                data['harness']=prepared['prepared']
            elif action in {'multiphase_decision','multiphase_evaluate','multiphase_finish','multiphase_stop','multiphase_baseline'}:
                prepared=await multi._operate(data['harness'],args,action.removeprefix('multiphase_'))
                data['harness']=prepared['prepared']
            elif action=='multiphase_review':
                data['harness']=(await multi.prepare_review(data['harness'],args))['prepared']
            else: raise ValueError('未知 XRD 操作')
            self.persist(data)
            if action=='multiphase_start' and use_jev:
                data['jev_error']='';self.persist(data)
                self.jev_tasks[owner]=asyncio.create_task(jev.run(self,owner,jev_model))
            if action=='multiphase_decision': return await multi._decision(data['harness']['state'],args.get('draft_candidate_ids',[]))
            return multi.tool_summary({'value':multi._read(data['harness'])})
    async def execute(self, owner, run_id, config):
        event_path=self.path(owner).parent / (run_id+'.events.jsonl')
        try:
            async for event in self.runtime.execute(SimpleNamespace(agent_id=owner,provider_config=config),SimpleNamespace(run_id=run_id),None):
                with event_path.open('a',encoding='utf-8') as out:
                    out.write(json.dumps({'time_ns':time.time_ns(),'kind':event.kind,'payload':event.payload},ensure_ascii=False)+'\n')
        except asyncio.CancelledError: raise
        except Exception as exc:
            d=self.load(owner);d['error']=str(exc);self.persist(d)
        finally:
            d=self.load(owner);d['active_run_id']='';self.persist(d)
    def multiphase(self, owner, args=None):
        data=self.load(owner)
        if args: return multi._ui_frame(data['harness'],args)
        value=multi._ui_overview(data['harness'],{})
        state=value.get('state',{})
        review=state.get('pywpem_review',{})
        error=review.get('error') if review.get('status') in {'running','completed','failed','cancelled'} else data.get('jev_error') or state.get('error')
        return {**state,'jev_error':data.get('jev_error',''), 'error':error, 'available_agents':[{'agent_node_id':'jev','name':'TypeSafe / Jev'},{'agent_node_id':'matcreator','name':'MatCreator Agent'}], 'saved_options':data['harness'].get('next_options',{})}
    def resource(self, owner, action, args):
        context=SimpleNamespace(node_id=owner,cancelled=asyncio.Event(),actor_id='matcreator')
        if action not in history.actions(): raise ValueError('未知历史操作')
        return history.actions()[action].handler(context,args)
service=XrdService()
