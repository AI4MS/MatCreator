"""XRD tools share MC Web's authoritative service, never spawn duplicate workers."""
import json, os, re
import httpx
from matcreator.ports import get_web_port

def _url():
    return f'http://127.0.0.1:{get_web_port()}/api/xrd'
def _compact(value):
    if isinstance(value,dict):
        return {k:_compact(v) for k,v in value.items() if k not in {'archive','source_base64','points','observed','calculated','frame_sets','frames','plot'}}
    if isinstance(value,list): return [_compact(v) for v in value]
    return value
async def _request(path='',body=None):
    async with httpx.AsyncClient(timeout=600) as client:
        r=await client.get(_url()+path) if body is None else await client.post(_url()+path,json=body)
        r.raise_for_status();return r.json()
async def xrd_list_workspaces() -> dict:
    """List persistent XRD workspaces. Reuse the user-specified workspace; never guess input identity."""
    return {'workspaces':await _request('/workspaces')}
async def xrd_get_results(workspace_id: str) -> dict:
    """Read XRD execution status, candidates, convergence and lineage. A low residual does not prove phase identity or mass fraction."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", workspace_id): raise ValueError("Invalid workspace_id")
    info=await _request('/workspaces/'+workspace_id)
    return {'workspace_id':workspace_id,**_compact(info),'multiphase':_compact(await _request('/workspaces/'+workspace_id+'/multiphase'))}
async def xrd_action(workspace_id: str, action: str, arguments_json: str='{}') -> dict:
    """Operate the XRD workbench after user authorization / an approved Standard plan.

    Actions: start (request_id REQUIRED, config optional; workflow_stage search/preopt/fit),
    stop, update_config, ensure_input (kind pattern/reference/cif/library), document
    (action import/configure/associate, arguments, revision, target),
    history_list/history_inspect/history_records, multiphase_start,
    multiphase_decision (draft_candidate_ids), multiphase_evaluate (candidate_ids,
    reason, selection_token, construction_path), multiphase_baseline,
    multiphase_finish, multiphase_review (run_id, combinations), multiphase_stop.
    Start is idempotent: reuse the SAME request_id after timeout/reconnect. Poll
    xrd_get_results; never resubmit a running or detached job. Multiphase must use
    returned decision tokens and measured evidence. Exhaust Agent budget before
    baseline, then finish. Results retain source/run IDs, hashes and claim scope.
    """
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", workspace_id): raise ValueError("Invalid workspace_id")
    arguments=json.loads(arguments_json)
    if not isinstance(arguments,dict): raise ValueError('arguments_json must be an object')
    return _compact(await _request('/workspaces/'+workspace_id+'/command',{'action':action,'arguments':arguments}))
