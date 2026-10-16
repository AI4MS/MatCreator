"""Local MC HTTP transport; the scientific service is also exposed to Agent tools."""
import asyncio, base64, io, os, zipfile
from pathlib import Path
from fastapi.responses import Response
from fastapi import APIRouter, HTTPException, Body
from pydantic import BaseModel, Field
from matcreator.xrd.service import service
router=APIRouter(prefix='/api/xrd',tags=['XRD'])
class Command(BaseModel):
    action: str
    arguments: dict = Field(default_factory=dict)

def local_only():
    if os.environ.get('MATCREATOR_MODE','local')!='local':
        raise HTTPException(403,'XRD adapter currently requires MC local mode')

@router.get('/workspaces')
async def workspaces():
    local_only(); return service.list()
@router.post('/workspaces')
async def create(body: dict = Body(default={})):
    local_only(); return service.create(str(body.get('name','XRD 工作台')))
@router.get('/workspaces/{owner}')
async def state(owner: str):
    local_only()
    try: return await service.info(owner)
    except (ValueError,KeyError) as exc: raise HTTPException(404,str(exc))
@router.get('/workspaces/{owner}/inputs')
async def inputs(owner: str):
    local_only(); return service.inputs(owner)
@router.get('/workspaces/{owner}/document')
async def document(owner: str,target: str=''):
    local_only(); d=service.load(owner)
    try: return d['inputs'][target] if target and target!=owner else d['document']
    except KeyError: raise HTTPException(404,'输入不存在')
@router.get('/workspaces/{owner}/multiphase')
async def multiphase(owner: str):
    local_only(); return service.multiphase(owner)
@router.get('/workspaces/{owner}/source/{target}')
async def source(owner: str,target: str):
    local_only()
    try:
        from urllib.parse import quote
        value=service.load(owner)['inputs'][target]['value']
        return Response(base64.b64decode(value['source_base64']),media_type='application/octet-stream',headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote(value['filename'],safe='')})
    except (ValueError,KeyError) as exc:raise HTTPException(404,str(exc))
@router.get('/workspaces/{owner}/archive/{run_id}')
async def archive(owner: str,run_id: str):
    local_only(); service.load(owner)
    def pack():
        from matcreator.xrd.vendor.pipeline import read_owned_run
        directory,manifest,_=read_owned_run(Path(os.environ['OAW_XRD_RUN_ROOT']),run_id,owner,completed=False)
        if manifest.get('status')=='running': raise ValueError('运行尚未结束')
        files=[p for p in directory.rglob('*') if p.is_file() and p.name!='input.json']
        if any(not p.resolve().is_relative_to(directory.resolve()) for p in files): raise ValueError('归档路径越界')
        if sum(p.stat().st_size for p in files)>30*1024*1024: raise ValueError('归档超过 30 MiB，请从本地运行目录读取')
        buffer=io.BytesIO()
        with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as zipped:
            for p in files:zipped.write(p,str(p.relative_to(directory)))
        return buffer.getvalue()
    try:return Response(await asyncio.to_thread(pack),media_type='application/zip',headers={'Content-Disposition':f'attachment; filename="xrd-{run_id}.zip"'})
    except (ValueError,KeyError,OSError) as exc:raise HTTPException(400,str(exc))
@router.post('/workspaces/{owner}/command')
async def command(owner: str,body: Command):
    local_only()
    try:
        if body.action.startswith('history_'): return service.resource(owner,body.action,body.arguments)
        if body.action=='multiphase_frame': return service.multiphase(owner,body.arguments)
        return await service.action(owner,body.action,body.arguments)
    except (ValueError,KeyError,TypeError) as exc: raise HTTPException(400,str(exc))
@router.post('/structure')
async def structure(body: dict = Body(...)):
    local_only()
    try:
        import io
        from ase.io import read
        raw=base64.b64decode(body.get('source_base64',''),validate=True)
        if len(raw)>2*1024*1024: raise ValueError('CIF 最大 2 MiB')
        atoms=read(io.StringIO(raw.decode('utf-8')),format='cif')
        from ase.io import write
        output=io.StringIO();write(output,atoms,format='extxyz')
        return {'structure_string':output.getvalue(),'n_atoms':len(atoms)}
    except Exception as exc: raise HTTPException(400,str(exc))

@router.get('/jev/config')
async def jev_config():
    local_only()
    from matcreator.xrd import jev
    from matcreator.xrd.service import ROOT
    return jev.config(ROOT)

@router.post('/jev/config')
async def save_jev_config(body: dict = Body(...)):
    local_only()
    from matcreator.xrd import jev
    from matcreator.xrd.service import ROOT
    try: return jev.config(ROOT,body)
    except ValueError as exc: raise HTTPException(400,str(exc))
