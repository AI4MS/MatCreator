"""Scientific silicon handoff for tracked Bohrium jobs.

Submission, monitoring and collection remain owned by remote-job. This module
does not archive results or upload to a cloud database.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Literal

from monty.json import MontyEncoder

from google.adk.tools.tool_context import ToolContext


def _digest(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def capture_vasp_input_hashes(source: Path) -> dict:
    """Capture materialized VASP inputs before tracked submission, without contents."""
    if not source.is_dir() or not all((source / name).is_file() for name in ('INCAR', 'POSCAR', 'POTCAR')):
        return {}
    files = [source / name for name in ('INCAR', 'POSCAR', 'POTCAR', 'KPOINTS') if (source / name).exists()]
    if any(p.is_symlink() for p in files):
        raise ValueError('VASP inputs must be regular files, not symbolic links.')
    return {p.name: _digest(p) for p in files}


def _input_directory(spec: dict, workspace: Path) -> Path:
    path = (Path(spec['input_root']) / spec['input_path']).resolve()
    if not path.is_relative_to(workspace):
        raise ValueError('Original inputs are outside this workspace.')
    return path


def _same_structure(actual, expected) -> bool:
    import numpy as np
    return (actual.species == expected.species
        and np.allclose(actual.lattice.matrix, expected.lattice.matrix, atol=1e-5, rtol=0)
        and np.allclose(actual.frac_coords, expected.frac_coords, atol=1e-5, rtol=0))


def _output_directory(artifacts: list, workspace: Path) -> Path:
    paths = [Path(a['destination']).resolve() for a in artifacts if a.get('destination')]
    if not paths or any(not p.is_relative_to(workspace) for p in paths):
        raise ValueError('Collected outputs are unavailable in this workspace.')
    candidates = {p if p.is_dir() else p.parent for p in paths}
    matches = [p for p in candidates if (p / 'vasprun.xml').is_file()]
    if len(matches) == 1:
        return matches[0]
    if len(candidates) == 1:
        return candidates.pop()
    raise ValueError('Cannot identify a unique VASP output directory.')


def _validate(directory: Path, calculation_type: str, input_directory: Path, input_hashes: dict) -> dict:
    from pymatgen.io.vasp.inputs import Incar, Poscar
    from pymatgen.io.vasp.outputs import Vasprun
    from pymatgen.symmetry.analyzer import SpacegroupAnalyzer

    required = ['vasprun.xml', 'OUTCAR', 'OSZICAR', 'CONTCAR', 'INCAR', 'POSCAR']
    missing = [name for name in required if not (directory / name).is_file()]
    if missing:
        raise ValueError(f'Missing VASP outputs: {", ".join(missing)}; scientific completion unknown.')
    if not all(input_hashes.get(name) for name in ('INCAR', 'POSCAR', 'POTCAR')):
        raise ValueError('Submission-time VASP input hashes unavailable; provenance unknown.')
    for name, digest in input_hashes.items():
        if name not in {'INCAR', 'POSCAR', 'POTCAR', 'KPOINTS'}:
            raise ValueError('Unrecognized VASP input provenance.')
        if not (input_directory / name).is_file() or _digest(input_directory / name) != digest:
            raise ValueError(f'Original {name} changed since tracked submission.')
        if (name != 'POTCAR' or (directory / name).exists()) and (not (directory / name).is_file() or _digest(directory / name) != digest):
            raise ValueError(f'Retained {name} differs from submitted job input.')
    run = Vasprun(directory / 'vasprun.xml', parse_dos=False, parse_eigen=False,
        parse_projected_eigen=False, parse_potcar_file=False, exception_on_bad_xml=True)
    incar = Incar.from_file(directory / 'INCAR')
    if not run.ionic_steps or any(not step.get('electronic_steps') for step in run.ionic_steps):
        raise ValueError('Electronic/ionic step evidence missing.')
    if not run.converged_electronic or any(len(step['electronic_steps']) >= int(run.parameters['NELM']) for step in run.ionic_steps):
        raise ValueError('Electronic convergence not reached.')
    outcar = (directory / 'OUTCAR').read_text(errors='replace')
    if 'General timing and accounting informations for this job' not in outcar:
        raise ValueError('VASP termination evidence missing from OUTCAR.')
    nsw = int(run.parameters.get('NSW', 0))
    ibrion = int(run.parameters.get('IBRION', -1))
    if calculation_type == 'relaxation':
        if nsw <= 1 or ibrion not in {1, 2} or not run.converged_ionic or 'reached required accuracy' not in outcar:
            raise ValueError('Ionic relaxation convergence not established.')
    elif nsw != 0 or ibrion != -1:
        raise ValueError('Expected a separate static total-energy step (NSW=0, IBRION=-1).')
    if (run.parameters.get('LDAU', False) or run.parameters.get('LHFCALC', False)
        or run.incar.get('ML_LMLFF', False) or run.parameters.get('METAGGA', '') not in {'', 'None', 'NONE'}
        or run.parameters.get('IVDW', 0) or run.parameters.get('LUSE_VDW', False)
        or incar.get('IVDW', 0) or incar.get('LUSE_VDW', False)):
        raise ValueError('Only ordinary PBE is supported; corrections/hybrid/meta-GGA detected.')
    if str(run.parameters.get('GGA', 'PE')).upper() != 'PE' or not run.potcar_symbols or any(not s.startswith('PAW_PBE Si ') for s in run.potcar_symbols):
        raise ValueError('Ordinary PBE Si pseudopotential evidence missing or incompatible.')
    titles = re.findall(r'^\s*TITEL\s*=\s*(.+?)\s*$', (input_directory / 'POTCAR').read_text(errors='replace'), re.M)
    if [' '.join(t.split()) for t in titles] != [' '.join(t.split()) for t in run.potcar_symbols]:
        raise ValueError('Submitted POTCAR identity disagrees with actual VASP output.')
    for structure in (run.initial_structure, run.final_structure):
        if not structure.is_ordered or set(str(s) for s in structure.species) != {'Si'} or SpacegroupAnalyzer(structure, symprec=0.01).get_space_group_number() != 227:
            raise ValueError('Output is outside fixed diamond bulk silicon scope.')
    initial = Poscar.from_file(directory / 'POSCAR').structure
    final = Poscar.from_file(directory / 'CONTCAR').structure
    for actual, expected in ((initial, run.initial_structure), (final, run.final_structure)):
        if not _same_structure(actual, expected):
            raise ValueError('POSCAR/CONTCAR disagrees with vasprun.xml structure.')
    if calculation_type == 'static' and not _same_structure(initial, final):
        raise ValueError('Static calculation changed its input structure.')
    energy = float(run.final_energy)
    if not math.isfinite(energy):
        raise ValueError('Final total energy unavailable or non-finite.')
    return {'structure': run.final_structure.as_dict(), 'initial_structure': run.initial_structure.as_dict(),
        'total_energy': {'value': energy, 'unit': 'eV'},
        'conditions': {'functional': 'PBE', 'domain': 'bulk', 'structure_model': 'diamond',
            'software': 'VASP', 'version': run.vasp_version, 'incar': dict(incar),
            'parameters': run.parameters, 'potcar_symbols': run.potcar_symbols,
            'potcar_sha256': input_hashes['POTCAR'], 'input_sha256': input_hashes,
            'kpoints': (directory / 'KPOINTS').read_text() if (directory / 'KPOINTS').is_file() else None,
            'actual_kpoints': getattr(run, 'actual_kpoints', None),
            'actual_kpoint_weights': getattr(run, 'actual_kpoints_weights', None)},
        'completion': {'electronic_converged': True,
            'ionic_converged': True if calculation_type == 'relaxation' else None,
            'ionic_steps': len(run.ionic_steps), 'outcar_termination': True},
        'artifacts': {p.name: {'path': str(p), 'sha256': _digest(p)} for p in directory.iterdir() if p.is_file() and p.name != 'silicon-result.json'}}


def collect_silicon_vasp_result(job_id: str, destination_path: str,
    calculation_type: Literal['relaxation', 'static'], tool_context: ToolContext, relaxation_job_id: str = '') -> dict:
    """Collect an existing tracked Si Batch Job and verify scientific completion.

    Never submits or replaces a job. A platform success alone is not a
    scientific success. Replays use the original durable collection paths.
    """
    from matcreator.agents.execution_agent.remote_job_tools import (
        get_remote_job_status, collect_remote_job_outputs, _service)
    if calculation_type not in {'relaxation', 'static'}:
        return {'status': 'invalid', 'job_id': job_id, 'message': 'Expected relaxation or static.'}
    job = get_remote_job_status(job_id, tool_context)
    if job.get('status') == 'error':
        return job
    if job['provider'] != 'bohr_batchjob' or job.get('user_control') or job['status'] not in {'succeeded', 'collected'}:
        return {'status': 'needs_replanning', 'job_id': job_id, 'platform_status': job['status'],
            'message': f"Existing job is {job['status']}; do not resubmit.", 'error': job.get('error')}
    collected = collect_remote_job_outputs(job_id, destination_path, tool_context)
    if collected.get('status') != 'collected':
        return {**collected, 'status': 'needs_replanning'}
    workspace = Path(tool_context.state['workspace_dir']).resolve()
    try:
        directory = _output_directory(collected['artifacts'], workspace)
    except ValueError as exc:
        return {'status': 'invalid', 'job_id': job_id, 'message': str(exc)}
    report = {'job_id': job_id, 'batchjob_id': job['external_id'], 'calculation_type': calculation_type,
        'output_path': str(directory), 'archived': False}
    try:
        spec = _service().store.get_job(job_id)['specification']
        report['submission'] = {k: spec[k] for k in ('image', 'machine_type', 'sku_id', 'project_id',
            'command', 'input_path', 'out_files', 'max_run_time', 'max_wait_time') if k in spec}
        input_directory = _input_directory(spec, workspace)
        report.update(_validate(directory, calculation_type, input_directory, spec.get('vasp_input_sha256', {})))
        if calculation_type == 'static':
            if not relaxation_job_id or relaxation_job_id == job_id:
                raise ValueError('Provide the preceding verified relaxation_job_id.')
            parent = get_remote_job_status(relaxation_job_id, tool_context)
            if parent.get('status') != 'collected' or parent.get('provider') != 'bohr_batchjob' or parent.get('user_control'):
                raise ValueError('Preceding relaxation is not collected in this session.')
            parent_job = _service().store.get_job(relaxation_job_id)
            parent_dir = _output_directory(parent_job['artifacts'], workspace)
            parent_spec = parent_job['specification']
            parent_input = _input_directory(parent_spec, workspace)
            parent_result = _validate(parent_dir, 'relaxation', parent_input, parent_spec.get('vasp_input_sha256', {}))
            from pymatgen.core import Structure
            relaxed = Structure.from_dict(parent_result['structure'])
            static_input = Structure.from_dict(report['initial_structure'])
            if not _same_structure(relaxed, static_input):
                raise ValueError('Static input does not match the verified relaxed structure.')
            if report['conditions']['potcar_sha256'] != parent_result['conditions']['potcar_sha256']:
                raise ValueError('Static and relaxation pseudopotentials differ.')
            report['relaxation_source'] = {'job_id': relaxation_job_id, 'batchjob_id': parent['external_id'],
                'output_path': str(parent_dir), 'total_energy': parent_result['total_energy']}
        report['status'] = 'success'
    except Exception as exc:
        report.update(status='invalid', message=str(exc))
    report_path = directory / 'silicon-result.json'
    report_path.write_text(json.dumps(report, cls=MontyEncoder, ensure_ascii=False, indent=2), encoding='utf-8')
    return {key: report[key] for key in ('status', 'job_id', 'output_path', 'archived')} | {
        'report_path': str(report_path), 'message': report.get('message', 'Scientific completion verified; ready for T3, not archived.')}
