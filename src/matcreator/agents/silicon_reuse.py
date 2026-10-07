"""Conservative reuse of the fixed diamond-Si/PBE workflow from public exports."""
from datetime import datetime
import json
import math

from pymatgen.core import Structure
from pymatgen.symmetry.analyzer import SpacegroupAnalyzer

from matcreator.tools.silicon_vasp import _same_structure


def _validate(record, task):
    meta = record.get('source_metadata') or {}
    conditions = meta.get('conditions') or {}
    completion = meta.get('completion') or {}
    stage = record.get('calculation_type')
    if meta.get('test_only') or meta.get('status') != 'success':
        raise ValueError('successful scientific status absent')
    if completion.get('electronic_converged') is not True or completion.get('outcar_termination') is not True:
        raise ValueError('electronic completion evidence absent')
    if stage not in {'relaxation', 'static'} or (stage == 'relaxation' and completion.get('ionic_converged') is not True):
        raise ValueError('requested calculation completion absent')
    structure = Structure.from_dict(record['structure'])
    if (not structure.is_ordered or set(str(s) for s in structure.species) != {'Si'}
        or not all(structure.lattice.pbc)
        or SpacegroupAnalyzer(structure, symprec=0.01).get_space_group_number() != 227):
        raise ValueError('structure is not diamond bulk Si')
    if record.get('functional') != 'PBE' or any(conditions.get(k) != v for k, v in
        {'functional': 'PBE', 'domain': 'bulk', 'structure_model': 'diamond'}.items()):
        raise ValueError('method or system differs from ordinary PBE bulk diamond')
    incar = conditions.get('incar') or {}
    parameters = conditions.get('parameters') or {}
    if not incar or record.get('calculation_parameters') != incar:
        raise ValueError('basic calculation conditions absent or inconsistent')
    for values in (incar, parameters):
        if (values.get('LDAU') or values.get('LHFCALC') or values.get('ML_LMLFF')
            or values.get('METAGGA', '') not in {'', 'None', 'NONE'}
            or values.get('IVDW') or values.get('LUSE_VDW') or values.get('XC')
            or str(values.get('GGA', 'PE')).upper() not in {'PE', '--'}):
            raise ValueError('ordinary PBE excludes U, hybrid and other corrections')
    if not conditions.get('potcar_sha256') or not conditions.get('potcar_symbols') or not all(
        s.startswith('PAW_PBE Si ') for s in conditions['potcar_symbols']):
        raise ValueError('PBE potential evidence absent')
    if not conditions.get('kpoints') and not conditions.get('actual_kpoints'):
        raise ValueError('k-point conditions absent')
    if not isinstance(incar.get('ENCUT'), (int, float)) or incar['ENCUT'] <= 0:
        raise ValueError('cutoff condition absent')
    if 'encut' in task['known'] and incar['ENCUT'] != task['known']['encut']:
        raise ValueError('cutoff differs from current request')
    for key, value in task['known'].get('incar_constraints', {}).items():
        if incar.get(key, parameters.get(key)) != value:
            raise ValueError(f'{key} differs from current request or is unknown')
    if 'kpoint_mesh' in task['known']:
        from pymatgen.io.vasp.inputs import Kpoints
        mesh = Kpoints.from_str(conditions.get('kpoints') or '').kpts
        if len(mesh) != 1 or list(mesh[0]) != task['known']['kpoint_mesh']:
            raise ValueError('k-point mesh differs from current request')
    if task['known'].get('structure_path'):
        if not _same_structure(structure, Structure.from_file(task['known']['structure_path'])):
            raise ValueError('given structure differs; no generic equivalence assumed')
    if not isinstance(record.get('energy'), (int, float)) or isinstance(record['energy'], bool) or not math.isfinite(record['energy']):
        raise ValueError('total energy absent or non-finite')
    if meta.get('energy_unit') != 'eV' or meta.get('energy_basis') != 'cell':
        raise ValueError('energy unit or basis unsupported/absent')
    if not record.get('source_dataset') or not record.get('source_id') or not meta.get('job_id'):
        raise ValueError('source identity absent')
    return structure


def _compatible_pair(parent, child):
    pm, cm = parent['source_metadata'], child['source_metadata']
    relation = cm.get('relaxation_source') or {}
    if cm.get('parent_record_id') != parent['record_id'] or relation.get('job_id') != pm['job_id']:
        return False
    pc, cc = pm['conditions'], cm['conditions']
    # These stages deliberately have different ionic/smearing/k-point settings.
    # Preserve both; require common method/potential/cutoff/spin and final cell.
    return (pc['potcar_sha256'] == cc['potcar_sha256']
        and all(pc['incar'].get(k) == cc['incar'].get(k) for k in ('ENCUT', 'ISPIN', 'LDAU', 'LHFCALC'))
        and _same_structure(Structure.from_dict(parent['structure']), Structure.from_dict(child['structure'])))


def _signature(records):
    return json.dumps([{'structure': r['structure'], 'stage': r['calculation_type'],
        'incar': r['source_metadata']['conditions']['incar'],
        'kpoints': r['source_metadata']['conditions'].get('kpoints'),
        'actual_kpoints': r['source_metadata']['conditions'].get('actual_kpoints'),
        'potential': r['source_metadata']['conditions']['potcar_sha256']} for r in records], sort_keys=True)


def select_reuse(records, task):
    """Separate candidates from applicability, without a database connection."""
    valid, candidates = [], []
    for record in records:
        candidate = {'record_id': record.get('record_id'), 'calculation_type': record.get('calculation_type'),
            'energy': record.get('energy'), 'functional': record.get('functional'),
            'source_dataset': record.get('source_dataset'), 'source_id': record.get('source_id'),
            'structure': record.get('structure'), 'conditions': (record.get('source_metadata') or {}).get('conditions'),
            'completion': (record.get('source_metadata') or {}).get('completion'),
            'energy_unit': (record.get('source_metadata') or {}).get('energy_unit')}
        try:
            _validate(record, task)
            valid.append(record)
            candidate.update(compatible=True, reason='scientific content and basic conditions verified')
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            candidate.update(compatible=False, reason=str(exc))
        candidates.append(candidate)
    base = {'candidates': candidates, 'record_ids': [], 'new_calculation_needed': False}
    if not task['targets'] or any(k in task['unknown'] for k in ('structure_model', 'domain', 'functional')):
        return {**base, 'status': 'needs_clarification', 'reason': '请明确目标、结构、体系及方法；检索默认值不能作为复用条件。'}
    relax = [r for r in valid if r['calculation_type'] == 'relaxation']
    static = [r for r in valid if r['calculation_type'] == 'static']
    options = []
    if 'relaxation' in task['targets']:
        if 'total_energy' in task['targets']:
            options = [[p, c] for p in relax for c in static if _compatible_pair(p, c)]
        if not options and not task.get('require_static'):
            options = [[p] for p in relax]
    elif 'total_energy' in task['targets']:
        options = [[c] for c in static]
        if not options and not task.get('require_static'):
            options = [[p] for p in relax]
    if not options:
        return {**base, 'status': 'unavailable', 'new_calculation_needed': True,
            'reason': '候选尚未核验为满足当前目标的可复用结果；保留缺失/不符证据。'}
    if len({_signature(option) for option in options}) > 1:
        return {**base, 'status': 'needs_clarification', 'reason': '完整候选的结构或计算条件有实质差异，请选择科学条件。'}
    if len(options) > 1:
        try:
            times = [datetime.fromisoformat(option[-1]['source_metadata']['archived_at']) for option in options]
            if any(t.tzinfo is None for t in times) or len(set(times)) != len(times):
                raise ValueError('archive order ambiguous')
            options = [options[max(range(len(times)), key=times.__getitem__)]]
        except (KeyError, ValueError, TypeError):
            return {**base, 'status': 'needs_clarification', 'reason': '持久归档时间不足以确定最近成功结果，请澄清。'}
    chosen = options[0]
    energy = chosen[-1]
    return {**base, 'status': 'recalculate' if task['recalculate'] else 'reused',
        'record_ids': [r['record_id'] for r in chosen], 'structure': chosen[0]['structure'],
        'total_energy': {'value': energy['energy'], 'unit': 'eV', 'basis': 'cell'},
        'results': [{'record_id': r['record_id'], 'calculation_type': r['calculation_type'],
            'structure': r['structure'], 'total_energy': {'value': r['energy'], 'unit': 'eV', 'basis': 'cell'},
            'source_dataset': r['source_dataset'], 'source_id': r['source_id'],
            'source_metadata': r['source_metadata']} for r in chosen],
        'reason': '公开读回核验成功、结构/方法/目标兼容；同条件优先最近成功归档，不代表精度最高。',
        'new_calculation_needed': task['recalculate']}
