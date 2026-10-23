"""Portable XRD configuration; never capture machine mounts or execution state."""
def remap_config(value, ids):
    result = dict(value)
    for key in ('source_node_id', 'owner_node_id', 'agent_node_id', 'cif_node_id'):
        if key in result:
            result[key] = ids.get(result[key], '')
    for key in ('workflow_match_run_id', 'workflow_preopt_run_id', 'intensity_csv', 'cif'):
        if key in result:
            result[key] = ''
    result.pop('workflow_started_at_ms', None)
    if 'workflow_stage' in result:
        result['workflow_stage'] = 'search'
    if 'selected_candidate_ids' in result:
        result['selected_candidate_ids'] = []
    if result.get('model') == 'PyWPEM':
        result['model'] = 'OAW_XRDfit'
    if result.get('next_options') is not None:
        result['next_options'] = remap_options(result['next_options'], ids)
    return result

def remap_options(value, ids):
    result = {k: value[k] for k in ('budget', 'max_phases', 'evaluate_baseline') if k in value}
    if 'agent_node_id' in value:
        result['agent_node_id'] = ids.get(value['agent_node_id'], '')
    return result

def capture_harness(value):
    return {'next_options': {k:v for k,v in value.get('next_options', {}).items()
                            if k in ('budget', 'max_phases', 'evaluate_baseline', 'agent_node_id')},
            'options': {}, 'run_id': '', 'state': {'status': 'idle'}}

def remap_harness(value, ids):
    result = capture_harness(value)
    result['next_options'] = remap_options(result['next_options'], ids)
    return result

from matcreator.xrd.compat import NodeTemplateHandler, NodeTemplateDependency

class XRDTemplateHandler(NodeTemplateHandler):
    def dependencies(self, config):
        return (NodeTemplateDependency('runtime_provider', 'research.xrd'),)
