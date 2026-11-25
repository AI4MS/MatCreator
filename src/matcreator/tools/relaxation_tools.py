"""Chat entry for a pending VASP result choice."""
import asyncio
from typing import Literal

from google.adk.tools.tool_context import ToolContext

from matcreator.aidb.relaxation import current_choice, resolve_choice
from matcreator.agents.graph_logger import AgentGraphLogger
from matcreator.agents.execution_graph_state import get_execution_graph, set_execution_graph


async def vasp_relaxation_choice(
    action: Literal["detail", "select", "continue", "revise", "cancel"],
    choice_id: str,
    tool_context: ToolContext,
    ordinal: int | None = None,
    record_id: str = "",
) -> dict:
    """Handle the user's current VASP result choice using its supplied choice_id.

    detail reads one candidate and leaves the choice pending. select finishes
    the step by delivering that record. Use its one-based ordinal or exact
    record ID from the current result. If more than one candidate fits the
    user's description, clarify before calling select or detail. continue
    releases the current prepared inputs to the original approved submit path.
    revise invalidates this choice before preparing changed inputs. cancel
    closes this choice before ending the task or switching to a new one.
    Call only in response to the user's latest explicit choice, never to choose
    on the user's behalf.
    """
    result = await asyncio.to_thread(resolve_choice, tool_context, action, choice_id, ordinal, record_id)
    if result["status"] not in {"ready_to_continue", "reused", "cancelled", "revise", "stale_input", "stale_binding"}:
        return result

    choice = await asyncio.to_thread(current_choice, tool_context)
    if not choice or choice["id"] != choice_id:
        return result
    if result["status"] != "ready_to_continue" and choice.get("step_id"):
        reuse = result["status"] == "reused"
        summary = "已交付复用的 VASP 结果和微调数据。" if reuse else "本次 VASP 候选已失效。"
        await asyncio.to_thread(AgentGraphLogger(tool_context.state["session_id"]).log_node_complete,
                                choice["step_id"], "success" if reuse else "cancelled", summary=summary)
    graph = get_execution_graph(tool_context.state)
    if graph and choice["scope"]["graph_id"] == graph.get("graph_id"):
        node = graph["nodes"].get(choice["scope"]["node_id"])
        if node and node["status"] in {"waiting", "pending", "running"}:
            if node.get("aidb_choice_id") not in (None, choice_id):
                return result
            if result["status"] == "ready_to_continue":
                node["status"] = "pending"
                result["next"] = "Call resume_execution after the original approval requirements are satisfied."
            elif result["status"] == "reused":
                node.update(status="success", result=(
                    f"已复用 {result['delivery']['record_id']}。末帧每胞总能量 "
                    f"{result['energy_ev_per_cell']} eV，POSCAR 和 EXTXYZ 已交付。"
                    + ("能带数据和图已交付。" if result.get("calculation_type") == "nscf" else "")
                    + ("计算产物：" + "、".join(result["artifact_paths"]) if result["artifact_paths"] else "")
                ), aidb_delivery=result["delivery"])
            else:
                node.update(status="cancelled", result="本次 VASP 候选已失效。")
            node.pop("aidb_choice_id", None)
            set_execution_graph(tool_context.state, graph)
    return result
