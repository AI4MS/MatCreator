"""Public tool schema/routing contract; runtime imports use the normal tool registry."""
from google.adk.tools.function_tool import FunctionTool
from matcreator.tools.aidb_tools import aidb_query, aidb_detail, aidb_bind


def test_aidb_tools_expose_agent_callable_schema_without_session_override():
    for function in (aidb_query, aidb_detail, aidb_bind):
        declaration = FunctionTool(function)._get_declaration()
        assert declaration.name == function.__name__
        schema = declaration.parameters_json_schema or declaration.parameters.model_dump()
        assert set(schema['properties']) == ({'conditions_json'} if function is aidb_query else {'record_id'} if function is aidb_detail else {'config_path'})


def test_normal_chat_and_step_execution_expose_session_bound_aidb_tools():
    from matcreator.agents.thinking_agent.agent import thinking_agent
    from matcreator.agents.execution_agent.step_executor import build_step_executor_agent
    from matcreator.llm_cards import LLMCard
    expected={'aidb_query','aidb_detail','aidb_bind','aidb_export'}
    assert expected <= {getattr(tool,'name',None) for tool in thinking_agent.tools}
    card=LLMCard(name='test',model='openai/test',base_url='http://localhost',api_key='test')
    assert expected <= {getattr(tool,'name',None) for tool in build_step_executor_agent(card).tools}
