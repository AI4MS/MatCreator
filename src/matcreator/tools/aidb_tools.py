"""Chat tools use the same durable binding as result-row actions."""
import asyncio
import json
import os
from pathlib import Path
from google.adk.tools.tool_context import ToolContext
from matcreator.aidb.service import AIDBError, SessionAIDB
from matcreator.workspace import get_session_workdir


def session_service(tool_context, *, query_timeout=None):
    state = tool_context.state
    session_id = state.get('session_id')
    if not session_id:
        raise AIDBError('缺少父会话身份', 'needs_configuration')
    service = SessionAIDB(state.get('workdir') or state.get('workspace_dir') or get_session_workdir(session_id), session_id,
                          query_timeout=query_timeout)
    try:
        service.binding()
    except AIDBError as exc:
        if exc.status != 'needs_configuration':
            raise
        from matcreator.config import load_config
        selected = os.environ.get('AI_READY_DB_CONFIG') or (load_config().get('env') or {}).get('AI_READY_DB_CONFIG')
        if selected:
            service.bind(selected)
    return service


def _read(tool_context, operation):
    try:
        service = session_service(tool_context)
        result = operation(service)
        output = service.publish(result)
        invocation = getattr(tool_context, '_invocation_context', None)
        output['user_id'] = str(getattr(invocation, 'user_id', '') or tool_context.state.get('user_id') or 'default')
        return output
    except (AIDBError, OSError, ValueError) as exc:
        return {'status': getattr(exc, 'status', 'failed'), 'message': str(exc)}


async def aidb_bind(config_path: str, tool_context: ToolContext) -> dict:
    """Select the user's explicit AIDB config for this chat; old result actions expire. Never invent a path."""
    try:
        state = tool_context.state
        service = SessionAIDB(state.get('workdir') or state.get('workspace_dir') or get_session_workdir(state.get('session_id')), state.get('session_id'))
        return await asyncio.to_thread(service.bind, config_path)
    except (AIDBError, OSError, ValueError) as exc:
        return {'status': getattr(exc, 'status', 'failed'), 'message': str(exc)}


async def aidb_query(conditions_json: str, tool_context: ToolContext) -> dict:
    """Query installed ai-ready-db with explicit user conditions. Formula means exact reduced composition;
    elements means contains ALL elements. Intersect functional, calculation_type, has_forces,
    has_stress, has_hubbard_u, source_dataset/source_id/record_id. Crystal post-filters:
    spacegroup + structure_role initial/final, target_kelvin (constant setpoint, never observed temperature).
    Ambiguous requests need clarification. Unknown filters return unsupported. No editing/deletion.
    Returned reference renders summary rows with fresh-read detail actions in the normal chat.
    Answer from returned facts and coverage; native matches, visible rows and verified frames differ.
    Label presence does not establish alignment, convergence, compatibility or training admission.
    The bounded summary is not an exhaustive comparison; never infer only the functional differs.
    Omission from facts is not missing/not-applicable data: never claim a field was not saved or
    not extracted because it is unlisted. Use optional_results availability/evidence; not_provided
    describes this returned record, not raw-output capability. PAW populations are not oxidation
    states, and spin settings or null magmom do not establish the material's magnetism.
    """
    return await asyncio.to_thread(_read, tool_context, lambda service: service.query(json.loads(conditions_json)))


async def aidb_detail(record_id: str, tool_context: ToolContext) -> dict:
    """Fresh-read one ID from this chat's bound database and expand its full record presentation.
    Resolve ordinal references against this round's query results; clarify ambiguous references.
    This tool directly reads and opens details: answer from its method, cell, label and source facts.
    No execution plan, raw JSONL inspection or extra approval is needed for this supported read.
    Large arrays are omitted from model facts but preserved in the full UI snapshot. Null means
    unknown/not saved, false remains false, and not_applicable is distinct. A truncated collection
    is not exhaustive. Label presence alone does not establish training suitability.
    Omission from facts is not missing/not-applicable data: never claim a field was not saved or
    not extracted because it is unlisted. Use optional_results availability/evidence; not_provided
    describes this returned record, not raw-output capability. PAW populations are not oxidation
    states, and spin settings or null magmom do not establish the material's magnetism.
    Reading never selects a calculation candidate, submits VASP, or writes to the database.
    """
    return await asyncio.to_thread(_read, tool_context, lambda service: service.detail(record_id))


async def aidb_export(record_id: str, formats: list[str], tool_context: ToolContext,
                      result_id: str = '') -> dict:
    """Download selected AIDB record formats from a fresh ID read in this chat.
    Formats are extxyz (energy and force data), poscar (final structure), and html (full details).
    Use temperature for a verified AIMD temperature curve.
    Use extxyz for fine-tuning data. When the user refers to a result row or ordinal, pass its
    actual record_id and result_id; clarify ambiguous references. With an explicit ID, result_id
    may be omitted. No shell script, execution plan, or separate approval is needed.
    The chat displays the returned download buttons. Describe only files in the receipt and
    report per-format issues. If training data is unavailable, POSCAR and HTML may still be
    available. Successful file generation does not establish training suitability.
    """
    def run():
        from matcreator.aidb.relaxation import reuse_delivery_for_turn

        try:
            if delivery := reuse_delivery_for_turn(tool_context, record_id, formats):
                return delivery
            service = session_service(tool_context)
            if 'extxyz' in formats:
                requested = [*formats, 'poscar', 'html']
                output = service.export(record_id, requested, result_id=result_id or None)
            else:
                output = service.export(record_id, formats, result_id=result_id or None)
            invocation = getattr(tool_context, '_invocation_context', None)
            output['user_id'] = str(getattr(invocation, 'user_id', '') or tool_context.state.get('user_id') or 'default')
            return output
        except (AIDBError, OSError, ValueError) as exc:
            return {'status': getattr(exc, 'status', 'failed'), 'message': '导出尚未就绪：' + str(exc)}

    return await asyncio.to_thread(run)
