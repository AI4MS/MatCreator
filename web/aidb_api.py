"""Session-owned AIDB reads and downloads."""
import asyncio
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel
from matcreator.aidb.service import SessionAIDB, AIDBError


class RowExport(BaseModel):
    binding_id: str


def _get_completion(*, user_id, session_id, job_id):
    from matcreator.aidb.managed_vasp import get_managed_vasp_completion

    return get_managed_vasp_completion(user_id=user_id, session_id=session_id, job_id=job_id)


def create_aidb_router(session_exists, get_workdir, *, get_completion=_get_completion):
    router = APIRouter(prefix='/api/users/{user_id}/sessions/{session_id}/aidb')

    def service(user_id, session_id):
        if not session_exists(user_id, session_id):
            raise HTTPException(status_code=404, detail='Session not found')
        return SessionAIDB(get_workdir(session_id, user_id=user_id), session_id)

    async def read(operation):
        try:
            return await asyncio.to_thread(operation)
        except AIDBError as exc:
            raise HTTPException(status_code=409, detail={'status': exc.status, 'message': str(exc)}) from None
        except OSError:
            raise HTTPException(status_code=503, detail='AIDB 读取失败') from None

    @router.get('/results/{result_id}')
    async def result(user_id: str, session_id: str, result_id: str):
        bound = service(user_id, session_id)
        return await read(lambda: bound.result(result_id))

    async def completion_receipt(user_id, session_id, job_id):
        receipt = await read(lambda: get_completion(user_id=user_id, session_id=session_id, job_id=job_id))
        if receipt is None:
            raise HTTPException(status_code=404, detail='未找到该作业的完成回执')
        return receipt

    @router.get('/completions/{job_id}')
    async def completion(user_id: str, session_id: str, job_id: str):
        bound = service(user_id, session_id)
        receipt = await completion_receipt(user_id, session_id, job_id)
        result = {
            'kind': 'aidb_completion',
            'result_id': job_id,
            'binding_id': receipt.get('binding_id'),
            'compute_status': receipt['compute_status'],
            'archive_status': receipt['status'],
            'reason': receipt.get('reason'),
            'summary': receipt.get('summary'),
            'calculation_type': receipt.get('calculation_type'),
            'outcome': receipt.get('outcome'),
            'artifact_paths': receipt.get('artifact_paths', []),
            'artifact_delivery_issue': receipt.get('artifact_delivery_issue'),
            'labeling_input': receipt.get('labeling_input'),
            'eos_member': receipt.get('eos_member'),
            'records': [],
        }
        if result['archive_status'] == 'verified':
            try:
                archived = await read(lambda: bound.detail(receipt['record_id'], binding_id=receipt['binding_id']))
                result['records'] = archived['records']
            except HTTPException as exc:
                result['readback_issue'] = exc.detail['message'] if isinstance(exc.detail, dict) else exc.detail
        return JSONResponse(result, headers={'Cache-Control': 'no-store'})

    @router.post('/completions/{job_id}/records/{record_id}/export')
    async def export_completion(user_id: str, session_id: str, job_id: str, record_id: str, body: RowExport):
        bound = service(user_id, session_id)
        receipt = await completion_receipt(user_id, session_id, job_id)
        if (receipt['status'] != 'verified' or receipt.get('record_id') != record_id
                or receipt.get('binding_id') != body.binding_id):
            raise HTTPException(status_code=409, detail={
                'status': 'not_ready', 'message': receipt.get('reason') or '该记录未在本次作业中核实',
            })
        formats = ['extxyz', 'poscar', 'html']
        if receipt.get('temperature_available') or receipt.get('temperature_requested'):
            formats.append('temperature')
        verify = None
        if receipt.get('calculation_type') == 'nscf':
            from matcreator.aidb.managed_vasp import retained_band_artifacts

            verify = lambda record: retained_band_artifacts(record, user_id)
        return await read(lambda: bound.export(
            record_id, formats, binding_id=body.binding_id, verify=verify))

    @router.get('/completions/{job_id}/artifact')
    async def download_artifact(user_id: str, session_id: str, job_id: str, path: str):
        bound = service(user_id, session_id)
        receipt = await completion_receipt(user_id, session_id, job_id)
        relative = Path(path)
        file = bound.workspace / relative
        if (path not in receipt.get('artifact_paths', []) or relative.is_absolute()
                or '..' in relative.parts or file.resolve() != file or not file.is_file()):
            raise HTTPException(status_code=404, detail='未找到该作业的计算产物')
        return FileResponse(file, filename=file.name, headers={'Cache-Control': 'no-store'})

    @router.get('/records/{record_id}')
    async def detail(user_id: str, session_id: str, record_id: str, binding_id: str):
        bound = service(user_id, session_id)
        return await read(lambda: bound.detail(record_id, binding_id=binding_id))

    @router.post('/results/{result_id}/records/{record_id}/export')
    async def export_row(user_id: str, session_id: str, result_id: str, record_id: str, body: RowExport):
        bound = service(user_id, session_id)
        return await read(lambda: bound.export(
            record_id, ['extxyz', 'poscar', 'html'], result_id=result_id, binding_id=body.binding_id))

    @router.get('/exports/{export_id}/{fmt}')
    async def download(user_id: str, session_id: str, export_id: str, fmt: str):
        bound = service(user_id, session_id)
        file = await read(lambda: bound.export_file(export_id, fmt))
        if 'path' in file:
            return FileResponse(file['path'], filename=file['filename'], headers={'Cache-Control': 'no-store'})
        return Response(file['content'], media_type=file['media_type'], headers={
            'Content-Disposition': f'attachment; filename="{file["filename"]}"',
            'Cache-Control': 'no-store',
        })

    return router
