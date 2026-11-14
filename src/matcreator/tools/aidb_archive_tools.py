"""Conversation entry point for saving existing VASP results."""
import asyncio

from google.adk.tools.tool_context import ToolContext

from matcreator.aidb.archive import archive_vasp_result
from matcreator.aidb.service import AIDBError
from matcreator.tools.aidb_tools import session_service


def _archive(directories, tool_context):
    try:
        session = session_service(tool_context)
    except (AIDBError, OSError, ValueError) as exc:
        return {'status': getattr(exc, 'status', 'failed'), 'message': str(exc)}
    if not directories or (len(directories) == 1 and not directories[0].strip()):
        return {'status': 'needs_clarification', 'message': '请指定当前工作区中的 VASP 结果目录'}
    invocation = getattr(tool_context, '_invocation_context', None)
    user_id = str(getattr(invocation, 'user_id', '') or tool_context.state.get('user_id') or 'default')
    items = []
    for directory in directories:
        item = archive_vasp_result(session, directory, owner_id=user_id)
        if item.get('result'):
            item['result']['user_id'] = user_id
        items.append(item)
    summary = dict.fromkeys(('saved', 'existing', 'failed', 'unverified'), 0)
    for item in items:
        status = item['status']
        if item.get('readback_verified'):
            summary[status] += 1
        elif status in ('source_unverified', 'write_unverified', 'readback_failed', 'readback_mismatch'):
            summary['unverified'] += 1
        else:
            summary['failed'] += 1
    return {'operation': 'archive', 'status': items[0]['status'] if len(items) == 1 else 'batch_result',
            'items': items, 'summary': summary}


async def aidb_archive_vasp(directories: list[str], tool_context: ToolContext) -> dict:
    """Save the verified final frame of existing VASP relaxation, static SCF or MD runs to this chat's AIDB.
    Only call for an explicit request to save existing outputs. Pass the user's directories, relative
    to the current workspace or absolute within it. Each requires a paired vasprun.xml and OUTCAR
    (either can be gzip compressed). This never submits VASP or changes the original outputs.
    Report each directory as new (saved), existing, or unverified/failed from its receipt. Successful
    receipts include a verified record ID and shared clickable result. Use summary for batch counts.
    A failed readback or mismatch
    is an incomplete archive even if the database accepted a write. No additional detail call is
    needed merely to verify the save; readback is part of this operation.
    """
    return await asyncio.to_thread(_archive, directories, tool_context)
