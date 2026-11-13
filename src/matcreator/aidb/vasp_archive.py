"""Validate and map one VASP output pair without executing a calculation."""
from __future__ import annotations

from datetime import datetime
import gzip
import hashlib
from io import StringIO
from math import isfinite
import re
from xml.etree import ElementTree

from matcreator.aidb.aimd import md_initial_seed, observed_temperature
from matcreator.aidb.service import AIDBError


def output_file(folder, name):
    paths = [path for suffix in ('', '.gz') if (path := folder / (name + suffix)).is_file()]
    if len(paths) != 1 or not paths[0].resolve().is_relative_to(folder):
        raise AIDBError(f'结果目录需要唯一且位于该目录内的 {name} 或 {name}.gz', 'invalid_output')
    return paths[0]


def file_digest(path, *, uncompressed=False):
    opener = gzip.open if uncompressed and path.suffix == '.gz' else open
    with opener(path, 'rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def workflow_input_evidence(folder, parsed):
    from pymatgen.io.vasp.inputs import Incar, Kpoints, Poscar
    import numpy as np

    evidence = {}
    for name, parser in (('INCAR', Incar), ('POSCAR', Poscar), ('KPOINTS', Kpoints), ('POTCAR', None)):
        paths = [path for suffix in ('', '.gz') if (path := folder / (name + suffix)).is_file()]
        if not paths:
            continue
        if len(paths) != 1 or not paths[0].resolve().is_relative_to(folder):
            evidence[name] = {'verified': False, 'reason': '输入文件路径有歧义'}
            continue
        path = paths[0]
        try:
            opener = gzip.open if path.suffix == '.gz' else open
            with opener(path, 'rb') as stream:
                content = stream.read()
            item = {'sha256': hashlib.sha256(content).hexdigest(), 'verified': False}
            if name == 'POTCAR':
                item['reason'] = '输出中的赝势名称不足以核实 POTCAR 完整内容'
            else:
                candidate = parser.from_str(content.decode('utf-8'))
                if name == 'INCAR':
                    echoed = dict(parsed.incar)
                    unverified = sorted(key for key in set(candidate) | set(echoed) if candidate.get(key) != echoed.get(key))
                    item.update(verified=not unverified, unverified_tags=unverified, compared_with='vasprun.xml · incar')
                elif name == 'POSCAR':
                    original, initial = candidate.structure, parsed.initial_structure
                    same = (original.species == initial.species
                            and np.allclose(original.lattice.matrix, initial.lattice.matrix, rtol=0, atol=1e-7)
                            and np.allclose(original.frac_coords, initial.frac_coords, rtol=0, atol=1e-7))
                    item.update(verified=bool(same), compared_with='vasprun.xml · initialpos',
                                full_input_identity=False)
                else:
                    # Explicit-list inputs have extra coordinate/weight conventions. Only attest verified regular meshes.
                    mesh = ('generation_style', 'kpoints', 'usershift')
                    runtime, supplied = parsed.kpoints.as_dict(), candidate.as_dict()
                    same = (candidate.num_kpts == parsed.kpoints.num_kpts == 0
                            and all(runtime.get(key) == supplied.get(key) for key in mesh))
                    item.update(verified=same, compared_with='vasprun.xml · kpoints')
            evidence[name] = item
        except (AttributeError, IndexError, KeyError, OSError, TypeError, ValueError):
            evidence[name] = {'verified': False, 'reason': '无法核对可选输入文件'}
    return evidence


def md_sampling(params, incar, version, step_count, last_results, atom_count, species_count):
    """Retain observed and configured sampling values with their own evidence."""
    def finite_number(key, minimum=0, *, strictly_positive=False):
        value = params.get(key)
        if (type(value) in (int, float) and isfinite(value) and value >= minimum
                and (not strictly_positive or value > 0)):
            return value
        return None

    begin = finite_number('TEBEG')
    end = finite_number('TEEND')
    step_fs = finite_number('POTIM', strictly_positive=True)
    observed = observed_temperature(last_results)
    algorithm = params.get('MDALGO')
    isif = params.get('ISIF')
    constant = begin is not None and begin > 0 and begin == end
    ensemble, keys = md_ensemble(params, algorithm, isif, constant, version, atom_count, species_count)
    cell_mode = {
        0: '形状、体积固定', 1: '形状、体积固定', 2: '形状、体积固定',
        3: '允许调整形状、体积', 4: '允许调整形状，体积固定',
    }.get(isif)
    evidence_keys = ('IBRION', 'MDALGO', 'ISIF', *keys, 'TEBEG', 'TEEND', 'POTIM', 'NSW')
    input_evidence = {key: incar[key] for key in evidence_keys if key in incar}
    ensemble_evidence = {key: params[key] for key in evidence_keys if key in params}
    fields = {
        'ensemble': (ensemble, 'vasprun.xml 的运行参数不足以判定系综或对应非恒温程序'),
        'target_start_kelvin': (begin, 'vasprun.xml 缺少可核实的 TEBEG'),
        'target_end_kelvin': (end, 'vasprun.xml 缺少可核实的 TEEND'),
        'observed_temperature_kelvin': (observed, 'OUTCAR 末步缺少唯一可核实的瞬时温度'),
        'step_fs': (step_fs, 'vasprun.xml 缺少有效的 POTIM'),
        'cell_mode': (cell_mode, 'vasprun.xml 的 ISIF 无法核实晶胞自由度'),
    }
    return {
        **{key: value for key, (value, _) in fields.items()},
        'unknown_reasons': {key: reason for key, (value, reason) in fields.items() if value is None},
        'source_ionic_steps': step_count, 'planned_ionic_steps': params['NSW'],
        'input_evidence': input_evidence, 'ensemble_evidence': ensemble_evidence,
        'temperature_evidence': 'OUTCAR · 末步 EKIN_LAT temperature（K）' if observed is not None else None,
    }


def md_ensemble(params, algorithm, isif, constant, version, atom_count, species_count):
    if algorithm == 3:
        keys = ('LANGEVIN_GAMMA', 'LANGEVIN_GAMMA_L', 'LATTICE_CONSTRAINTS')
        gamma = params.get('LANGEVIN_GAMMA')
        if isinstance(gamma, (int, float)):
            gamma = [gamma]
        if not isinstance(gamma, list) or len(gamma) != species_count:
            return None, keys
        cold = all(value == 0 for value in gamma)
        heated = all(value > 0 for value in gamma)
        if isif in (0, 1, 2):
            return ('NVE' if cold else 'NVT' if heated and constant else None), keys
        constraints = params.get('LATTICE_CONSTRAINTS')
        lattice_gamma = params.get('LANGEVIN_GAMMA_L')
        if isif == 3 and (constraints is None or constraints == [True, True, True]):
            if cold and lattice_gamma == 0:
                return 'NpH', keys
            if heated and constant and lattice_gamma is not None and lattice_gamma > 0:
                return 'NpT', keys
        return None, keys
    if isif not in (0, 1, 2):
        return None, ()
    if algorithm in (0, 2):
        smass = params.get('SMASS')
        return ('NVE' if smass == -3 else 'NVT' if smass is not None and smass >= 0 and constant else None), ('SMASS',)
    if algorithm == 1 and (params.get('TEBEG') or 0) >= 0.1:
        probability = params.get('ANDERSEN_PROB')
        return ('NVE' if probability == 0 else 'NVT' if probability is not None
                and 0 < probability <= 1 and constant else None), ('ANDERSEN_PROB',)
    match = re.match(r'(\d+)\.(\d+)\.(\d+)', version)
    release = tuple(map(int, match.groups())) if match else ()
    if algorithm == 4 and release >= (6, 4, 0):
        chains, period = params.get('NHC_NCHAINS'), params.get('NHC_PERIOD')
        return ('NVE' if chains == 0 or period == 0 else 'NVT' if chains is not None and period is not None
                and 1 <= chains <= 20 and period > 0 and constant else None), ('NHC_NCHAINS', 'NHC_PERIOD')
    if algorithm == 5 and release >= (6, 4, 3):
        period = params.get('CSVR_PERIOD')
        return ('NVE' if period == 0 else 'NVT' if period is not None and period > 0 and constant else None), ('CSVR_PERIOD',)
    if algorithm == 13:
        keys = ('NSUBSYS', 'TSUBSYS', 'PSUBSYS')
        boundaries, temperatures, probabilities = (params.get(key) for key in keys)
        if not all(isinstance(value, list) for value in (boundaries, temperatures, probabilities)):
            return None, keys
        if len(boundaries) == 2 and boundaries[-1] < atom_count:
            boundaries = [*boundaries, atom_count]
        if (len(boundaries) in (2, 3) and boundaries[-1] == atom_count
                and 0 < boundaries[0] and sorted(set(boundaries)) == boundaries
                and len(boundaries) == len(temperatures) == len(probabilities)
                and min(temperatures) > 0 and len(set(temperatures)) == 1
                and all(0 < probability <= 1 for probability in probabilities)):
            return 'NVT', keys
        return None, keys
    return None, ()


def parse_vasp_result(folder, *, verify_inputs=None, include_temperature=False, submitted_incar=None,
                      band_kpoints=None, capture_bands=None):
    from ase.io.vasp import read_vasp_out
    from ase.stress import full_3x3_to_voigt_6_stress
    from ase.units import GPa
    from monty.json import jsanitize
    import numpy as np
    from pymatgen.io.vasp.outputs import Outcar, Vasprun
    from pymatgen.symmetry.analyzer import SpacegroupAnalyzer

    xml, outcar = (output_file(folder, name) for name in ('vasprun.xml', 'OUTCAR'))
    files = (xml, outcar)
    raw_hashes = {path.name: file_digest(path) for path in files}
    hashes = {name: file_digest(path, uncompressed=True)
              for name, path in zip(('vasprun.xml', 'OUTCAR'), files)}
    try:
        # Vasprun's parser requires a closed XML document by default.
        parsed = Vasprun(xml, parse_dos=False, parse_eigen=False, parse_potcar_file=False)
        opener = gzip.open if outcar.suffix == '.gz' else open
        with opener(outcar, 'rt', encoding='utf-8') as stream:
            output = stream.read()
        # Vasprun's generic parameter parser coerces the generator clock to a float.
        opener = gzip.open if xml.suffix == '.gz' else open
        with opener(xml, 'rb') as stream:
            generator = next(element for _, element in ElementTree.iterparse(stream) if element.tag == 'generator')
        date = '.'.join(generator.findtext("i[@name='date']").split())
        time = generator.findtext("i[@name='time']").strip()
        headers = re.findall(r'^\s*executed on [^\n]*? date (\S+)\s+(\S+)', output, re.M)
        version = generator.findtext("i[@name='version']").strip()
        start_format = '%Y.%m.%d %H:%M:%S'
        same_start = len(headers) == 1 and datetime.strptime(' '.join(headers[0]), start_format) == datetime.strptime(
            f'{date} {time}', start_format)
        if not same_start or not output.split() or output.split()[0] != 'vasp.' + version:
            raise AIDBError('XML 与 OUTCAR 的运行开始时间或 VASP 版本不一致', 'source_conflict')
        # Limit convergence and accounting markers to the final calculation step.
        sections = re.split(r'^[ \t]*POSITION[ \t]+TOTAL-FORCE', output, flags=re.M)
        if len(sections) < 2:
            raise AIDBError('OUTCAR 缺少末帧位置和逐原子力', 'invalid_output')
        last_results, footer_marker, footer = sections[-1].partition('General timing and accounting informations for this job:')
        if not footer_marker or not re.search(r'^\s*Voluntary context switches:\s+\d+[ \t]*$', footer, re.M):
            raise AIDBError('OUTCAR 缺少完整的运行结束统计', 'invalid_output')
        params = jsanitize(dict(parsed.parameters), strict=True)
        nsw, ibrion, icharg = (params.get(key) for key in ('NSW', 'IBRION', 'ICHARG'))
        md = ibrion == 0 and isinstance(nsw, int) and nsw > 0
        nscf = nsw == 0 and ibrion == -1 and icharg == 11
        step = parsed.ionic_steps[-1]
        structure = step['structure']
        # Parse the exact inspected OUTCAR snapshot. ASE also expects a named stream.
        with StringIO(output) as stream:
            stream.name = outcar.name
            atoms = read_vasp_out(stream, index=-1)
        if {path.name: file_digest(path) for path in files} != raw_hashes:
            raise AIDBError('VASP 原始输出在解析期间发生变化', 'invalid_output')
        # MD finalpos may contain the next integration position. Only the last
        # calculation structure has electronic labels and a paired OUTCAR frame.
        energy = float(step['electronic_steps'][-1]['e_0_energy'] if md else parsed.final_energy)
        force = np.asarray(step.get('forces'), dtype=float)
        if (not np.isfinite(energy) or force.shape != (len(structure), 3)
                or not np.isfinite(force).all() or not np.isfinite(structure.lattice.matrix).all()
                or structure.volume <= 0 or not np.isfinite(structure.frac_coords).all()):
            raise AIDBError('XML 末帧缺少有效的晶胞、坐标、总能或逐原子力', 'invalid_output')
        displacement = atoms.get_scaled_positions(wrap=False) - structure.frac_coords
        displacement -= np.rint(displacement)
        checks = {
            'version': True, 'start time': True,
            'atom order': atoms.get_chemical_symbols() == [str(site.specie) for site in structure],
            'final energy': abs(atoms.get_potential_energy() - energy) <= 1e-6,
            'final lattice': np.allclose(atoms.cell.array, structure.lattice.matrix, rtol=0, atol=1e-6),
            'final positions': np.allclose(displacement @ structure.lattice.matrix, 0, rtol=0, atol=2e-5),
            'final forces': np.allclose(atoms.get_forces(apply_constraint=False), force, rtol=0, atol=1e-6),
        }
        if not md:
            checks['final XML structure'] = parsed.final_structure == structure
        # A fixed-charge diagonalization does not provide a self-consistent stress label.
        stress = None if nscf else step.get('stress')
        stress_symmetrized = False
        if stress is not None:
            stress = np.asarray(stress, dtype=float)
            if (stress.shape != (3, 3) or not np.isfinite(stress).all()
                    or not np.allclose(stress, stress.T, rtol=0, atol=1e-5)):
                raise AIDBError('XML 末帧应力张量无效', 'invalid_output')
            # Small asymmetry can occur in the XML stress at the scale of the
            # five-decimal OUTCAR kbar values. Keep the independent comparison
            # before retaining the mean of the transposed XML components.
            stress_symmetrized = not np.allclose(stress, stress.T, rtol=0, atol=1e-6)
            checks['final stress'] = np.allclose(
                atoms.get_stress(apply_constraint=False),
                full_3x3_to_voigt_6_stress(-stress * 0.1 * GPa), rtol=0, atol=1e-7,
            )
    except AIDBError:
        raise
    except (ArithmeticError, AttributeError, IndexError, KeyError, OSError, RuntimeError, StopIteration, SyntaxError,
            TypeError, ValueError) as exc:
        raise AIDBError(f'VASP 输出不完整或无法解析：{exc}', 'invalid_output') from exc

    conflicts = [key for key, matches in checks.items() if not matches]
    if conflicts:
        raise AIDBError(f'XML 与 OUTCAR 末帧不一致：{", ".join(conflicts)}', 'source_conflict')
    if stress_symmetrized:
        stress = (stress + stress.T) / 2
    if not isinstance(icharg, int) or (icharg >= 10 and not nscf) or parsed.incar.get('ML_LMLFF'):
        raise AIDBError('不支持该 VASP 计算的实际运行参数', 'unsupported_calculation')
    sampling = {'source_ionic_steps': len(parsed.ionic_steps), 'stored_scope': 'final_frame'}
    completion = 'XML 闭合；OUTCAR 运行结束统计'
    if md:
        if len(parsed.ionic_steps) != nsw or len(sections) - 1 != nsw:
            raise AIDBError(
                f'MD 积分阶段未获核实：计划 {nsw} 步，XML 有 {len(parsed.ionic_steps)} 步，OUTCAR 有 {len(sections) - 1} 帧',
                'invalid_output',
            )
        kind = 'molecular_dynamics'
        ionic = None
        incar = jsanitize(dict(parsed.incar), strict=True)
        sampling.update(md_sampling(params, incar, version,
                                    len(parsed.ionic_steps), last_results, len(structure), len(parsed.potcar_symbols)))
        # VASP writes the first RNG state either before or just after the
        # first POSITION header, before updating the state at the first step.
        if include_temperature:
            seed = md_initial_seed(submitted_incar or incar, '\n'.join(sections[:2]))
            if seed is not None:
                sampling['initial_random_seed'] = seed
            temperatures = [observed_temperature(section) for section in (*sections[1:-1], last_results)]
            if all(value is not None for value in temperatures):
                sampling['temperature_series_kelvin'] = temperatures
        completion += '；XML 计算步数与 OUTCAR 帧数均等于实际 NSW'
    elif nscf:
        if len(parsed.ionic_steps) != 1 or len(sections) != 2:
            raise AIDBError('NSCF 固定电荷对角化阶段未获核实', 'invalid_output')
        kind = 'nscf'
        ionic = None
    elif nsw == 0:
        kind = 'static'
        ionic = None
    elif isinstance(nsw, int) and nsw > 0 and ibrion in (1, 2, 3):
        kind = 'relaxation'
        ionic = (params.get('EDIFFG') != 0
                 and 'reached required accuracy - stopping structural energy minimisation' in last_results)
    else:
        raise AIDBError('当前保存支持自洽静态计算、结构弛豫与分子动力学', 'unsupported_calculation')
    last_electronic = sections[-2]
    electronic = bool(parsed.converged_electronic and step['electronic_steps'] and (
        'aborting loop because EDIFF is reached' in last_electronic
        or (parsed.incar.get('ALGO', '').lower() == 'exact' and parsed.incar.get('NELM') == 1)
    ))
    if not electronic or ionic is False:
        raise AIDBError('末步电子收敛未获确认' if not electronic else '结构弛豫离子收敛未获确认', 'not_converged')

    params['LDAU'] = bool(parsed.is_hubbard)
    kpoints = jsanitize(parsed.kpoints.as_dict(), strict=True)
    paw_population = None
    final_charge = re.compile(r'^[ \t]*total charge[ \t]*$', re.M)
    if params.get('LORBIT') == 11 and (final_charge.search(last_electronic) or final_charge.search(last_results)):
        try:
            charge = Outcar(outcar).charge
            if len(charge) == len(structure):
                candidate = [row['tot'] for row in charge]
                if np.isfinite(candidate).all():
                    paw_population = candidate
        except (AttributeError, IndexError, KeyError, OSError, ValueError):
            pass
    band = None
    if nscf:
        from pymatgen.io.vasp import Kpoints
        from matcreator.aidb.band_structure import read_bands

        try:
            if band_kpoints is None:
                band_kpoints = Kpoints.from_file(output_file(folder, 'KPOINTS'))
            eigen = Vasprun(xml, parse_dos=True, parse_eigen=True, parse_potcar_file=False)
            band = read_bands(eigen, band_kpoints, output)
        except (ArithmeticError, AttributeError, IndexError, KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
            raise AIDBError(f'VASP 能带结果无法解析：{exc}', 'invalid_output') from exc
    if {path.name: file_digest(path) for path in files} != raw_hashes:
        raise AIDBError('VASP 原始输出在解析期间发生变化', 'invalid_output')
    spin = {key: params.get(key) for key in ('ISPIN', 'LNONCOLLINEAR', 'LSORBIT')}
    method_inputs = {key: parsed.incar[key] for key in ('GGA', 'METAGGA', 'XC') if key in parsed.incar}
    method_runtime = {key: params.get(key) for key in ('GGA', 'METAGGA', 'LMETAGGA', 'LHFCALC')}
    lexch = re.findall(r'^\s*LEXCH\s*=\s*([A-Z]+)\s*$', output, re.M)
    pbe_header = (bool(lexch) and set(lexch) == {'PE'} and not method_inputs
                  and str(params.get('GGA')) == '--'
                  and all(params.get(key) is False for key in ('LHFCALC', 'LMETAGGA', 'LDAU')))

    def crystal(item):
        symprec = 0.001
        dataset = SpacegroupAnalyzer(item, symprec=symprec).get_symmetry_dataset()
        symmetry = {'symbol': dataset.international, 'number': dataset.number,
                    'algorithm': 'spglib', 'symprec_angstrom': symprec} if dataset else None
        return {'structure': item.as_dict(), 'symmetry': symmetry,
                'spacegroup': symmetry['symbol'] if symmetry else None}

    if verify_inputs is not None:
        verify_inputs(parsed, kind, params, lexch)

    if band is not None and capture_bands is not None:
        capture_bands(band)

    final = crystal(structure)
    initial = crystal(parsed.initial_structure)
    initial['source'] = 'vasprun.xml · initialpos'
    frame_index = len(parsed.ionic_steps) - 1
    fingerprint = hashlib.sha256(f"{hashes['vasprun.xml']}:{hashes['OUTCAR']}".encode()).hexdigest()
    return {
        'source_id': f'vasp:{fingerprint}:{frame_index}', 'frame_index': frame_index,
        'structure': final.pop('structure'), 'energy': energy, 'forces': force.tolist(), 'charge': paw_population,
        'stress': (-stress).tolist() if stress is not None else None,
        'stress_unit': 'kbar', 'stress_convention': 'tensile_positive',
        'calculation_type': kind, 'calculation_parameters': params,
        'provenance': {'input_stress_unit': 'kbar', 'input_stress_convention': 'vasp_compressive_positive',
                       'stress_sign_conversion': -1,
                       **({'stress_symmetrization': 'mean_of_transposed_xml_components'}
                          if stress_symmetrized else {})} if stress is not None else {},
        'source_metadata': {'software': 'VASP', 'vasp_facts': {
            **final, 'initial_structure': initial,
            'paw_population_e_per_atom': paw_population,
            **({'band_structure': {key: band[key] for key in ('nkpoints', 'nbands', 'fermi_energy_ev')},
                'fermi_energy_ev': band['fermi_energy_ev']} if band is not None else {}),
            'sampling': {'type': kind, **sampling},
            'method': {'code': 'VASP', 'version': version, 'functional_input': method_inputs,
                       'functional_runtime': method_runtime, 'input_encut_ev': parsed.incar.get('ENCUT'),
                       'potcar_symbols': parsed.potcar_symbols, 'kpoints': kpoints, 'spin': spin},
            'labels': {'energy_definition': 'e_0_energy', 'energy_unit': 'eV/cell', 'force_unit': 'eV/Angstrom',
                       'electronic_converged': electronic, 'ionic_converged': ionic},
            'source': {'sha256': hashes, 'file_sha256': raw_hashes, 'started': f'{date} {time}',
                       'run_fingerprint': fingerprint, 'xml_outcar_checks': {key: bool(value) for key, value in checks.items()},
                       'workflow_inputs': workflow_input_evidence(folder, parsed),
                       'field_evidence': {'structure_labels': 'vasprun.xml · 末个 calculation；OUTCAR · 末个 POSITION',
                                          'electronic_convergence': 'vasprun.xml · 末步 SCF；OUTCAR · EDIFF',
                                          'ionic_convergence': 'OUTCAR · reached required accuracy' if ionic else None,
                                          'paw_population': 'OUTCAR · 最后一个 total charge 表的 tot 列（LORBIT=11）'
                                          if paw_population is not None else None,
                                          'charge': 'OUTCAR · 最后一个 total charge 表的 tot 列（PAW 投影）'
                                          if paw_population is not None else None,
                                          'completion': completion}},
        }},
        'hubbard_u': dict(parsed.hubbards) if parsed.is_hubbard else None, 'pbe_header': pbe_header,
    }
