"""Small answerable facts for agents; full records remain in the UI snapshot."""
from itertools import islice


def _bounded(value, budget=None, depth=0):
    """Bound each facts section; advertise every abbreviated collection/string."""
    if budget is None:
        budget = [128, 4096]  # visited values and text characters per section
    budget[0] -= 1
    collection = isinstance(value, (dict, list))
    if budget[0] < 0 or budget[1] <= 0 or depth > 6:
        return {'truncated': True, 'item_count': len(value) if collection else None}
    if isinstance(value, str):
        size = min(512, budget[1])
        budget[1] -= min(len(value), size)
        return value if len(value) <= size else {
            'truncated': True, 'character_count': len(value), 'preview': value[:size]}
    if not collection:
        return value
    items = {} if isinstance(value, dict) else []
    entries = value.items() if isinstance(value, dict) else enumerate(value)
    limit = 24 if isinstance(value, dict) else 12
    for key, item in islice(entries, limit):
        if budget[0] <= 0 or budget[1] <= 0:
            break
        if isinstance(items, dict):
            if len(key) > min(128, budget[1]):
                break
            budget[1] -= len(key)
            items[key] = _bounded(item, budget, depth + 1)
        else:
            items.append(_bounded(item, budget, depth + 1))
    return items if len(items) == len(value) else {
        'truncated': True, 'item_count': len(value), 'items': items}


def _first(*values):
    return next((value for value in values if value is not None), None)


def _shape(value):
    if not isinstance(value, list):
        return [] if value is not None else None
    if not value:
        return [0]
    shapes = [_shape(item) for item in value]
    return [len(value), *shapes[0]] if all(s == shapes[0] for s in shapes) else [len(value), 'ragged']


def _availability(container, key, path):
    value = container.get(key)
    result = {'status': 'not_provided' if key not in container else 'null' if value is None else 'available',
              'source_field': path + key}
    if isinstance(value, list):
        result.update(shape=_shape(value), values_omitted=True)
    elif isinstance(value, dict):
        result.update(item_count=len(value), values_omitted=True)
    else:
        result['value'] = value
    return result


def record_facts(record, *, detail=False, frame_verified=False):
    meta = record.get('source_metadata') or {}
    saved = meta.get('vasp_facts') or {}
    method = saved.get('method') or {}
    labels = saved.get('labels') or {}
    source = saved.get('source') or {}
    sampling = saved.get('sampling') or {}
    params = record.get('calculation_parameters') or {}
    structure = record.get('structure') or {}
    initial = saved.get('initial_structure') or {}
    kind = record.get('calculation_type')
    non_md = kind is not None and kind != 'molecular_dynamics'
    facts = {
        'structure': {'nsites': record.get('nsites'), 'frame_index': record.get('frame_index'),
                      'frame_identity': 'verified' if frame_verified else 'unverified',
                      'phase': saved.get('phase'),
                      'spacegroup': _first(saved.get('spacegroup'), (saved.get('symmetry') or {}).get('symbol')),
                      'symmetry': saved.get('symmetry')},
        'method': {'version': _first(method.get('version'), meta.get('version')),
                   'functional': record.get('functional'),
                   'functional_evidence': method.get('functional_evidence'),
                   'functional_input': method.get('functional_input'),
                   'functional_runtime': method.get('functional_runtime'),
                   'input_encut_ev': _first(method.get('input_encut_ev'), params.get('ENCUT')),
                   'kpoints': _first(method.get('kpoints'), meta.get('kpoints')),
                   'potcar_symbols': _first(method.get('potcar_symbols'), meta.get('potcar_symbols')),
                   'LDAU': params.get('LDAU'), 'hubbard_u': record.get('hubbard_u'),
                   'spin': method.get('spin')},
        'labels': {
            'energy': {'present': record.get('energy') is not None, 'value': record.get('energy'),
                       'unit': 'eV/cell', 'definition': labels.get('energy_definition')},
            'electronic_converged': labels.get('electronic_converged'),
            'ionic_converged': labels.get('ionic_converged') if kind == 'relaxation'
                else 'not_applicable' if kind else None,
        },
        'sampling': {'calculation_type': kind,
                     **{key: 'not_applicable' if non_md else sampling.get(key) for key in
                        ('ensemble', 'target_start_kelvin', 'target_end_kelvin',
                         'observed_temperature_kelvin', 'step_fs')},
                     'cell_mode': sampling.get('cell_mode'),
                     'source_ionic_steps': sampling.get('source_ionic_steps'),
                     **{key: sampling[key] for key in ('planned_ionic_steps', 'stored_scope', 'unknown_reasons')
                        if key in sampling}},
        'source': {'source_dataset': record.get('source_dataset'), 'source_id': record.get('source_id'),
                   'sample_name': source.get('sample_name'), 'public_source': source.get('public_source'),
                   'file_sha256': _first(source.get('file_sha256'), source.get('sha256'),
                       {'vasprun.xml': meta['sha256']} if meta.get('sha256') is not None else None)},
        'training_suitability': 'not_assessed',
    }
    for key, unit in [('forces', 'eV/angstrom'), ('stress', record.get('stress_unit')),
                      ('virial', record.get('virial_unit'))]:
        value = record.get(key)
        facts['labels'][key] = {'present': value is not None, 'shape': _shape(value),
                               'unit': unit, 'values_omitted': value is not None}
    facts['labels']['stress']['convention'] = record.get('stress_convention')
    evidence = source.get('field_evidence') or {}
    optional = {key: _availability(record, key, '')
                for key in ('charge', 'magmom', 'bandgap', 'dos', 'band_structure')}
    optional.update({key: _availability(saved, key, 'source_metadata.vasp_facts.')
                     for key in ('density_g_cm3', 'fermi_energy_ev', 'paw_population_e_per_atom')})
    optional['charge'].update(evidence=evidence.get('charge'), oxidation_state='not_established')
    optional['magmom']['evidence'] = evidence.get('magmom')
    spin = method.get('spin') or {}
    optional['magmom']['applicability'] = 'not_applicable' if (
        record.get('magmom') is None and spin.get('ISPIN') == 1
        and spin.get('LNONCOLLINEAR') is False and spin.get('LSORBIT') is False) else 'not_assessed'
    optional['magnetism'] = _first(method.get('magnetism'), 'not_assessed')
    optional['paw_population_e_per_atom'].update(quantity='PAW_projected_electron_population',
        unit='e/atom', evidence=evidence.get('paw_population'), oxidation_state='not_established')
    optional['density_g_cm3']['unit'] = 'g/cm^3'
    optional['fermi_energy_ev']['unit'] = 'eV'
    facts['optional_results'] = optional
    if detail:
        facts['structure']['lattice_angstrom'] = (structure.get('lattice') or {}).get('matrix')
        facts['structure']['initial_structure'] = {
            'spacegroup': _first(initial.get('spacegroup'), (initial.get('symmetry') or {}).get('symbol')),
            'symmetry': initial.get('symmetry'), 'source': initial.get('source'),
            'unknown_reason': initial.get('unknown_reason'),
            'lattice_angstrom': ((initial.get('structure') or {}).get('lattice') or {}).get('matrix'),
        }
        facts['method']['calculation_parameters'] = params
        facts['source']['field_evidence'] = source.get('field_evidence')
        facts['source']['xml_outcar_checks'] = source.get('xml_outcar_checks')
        facts['sampling']['input_evidence'] = sampling.get('input_evidence')
        facts['sampling']['ensemble_evidence'] = sampling.get('ensemble_evidence')
        facts['sampling']['temperature_evidence'] = sampling.get('temperature_evidence')
    return {key: _bounded(value) for key, value in facts.items()}
