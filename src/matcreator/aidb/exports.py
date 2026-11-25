"""Generate downloads from one record returned by the installed AIDB reader."""
from html import escape
from io import StringIO
import json
import math
import re

import numpy as np
from ase.calculators.singlepoint import SinglePointCalculator
from ase.io import write
from pymatgen.core import Structure
from pymatgen.io.ase import AseAtomsAdaptor
from pymatgen.io.vasp.inputs import Poscar

from matcreator.aidb.aimd import temperature_curve_html


def _numeric_array(value, shape):
    try:
        array = np.asarray(value)
        if array.shape == shape and array.dtype.kind in 'iuf' and np.isfinite(array).all():
            return array.astype(float)
    except (ValueError, TypeError, OverflowError):
        pass
    return None


def _structure(record):
    try:
        structure = Structure.from_dict(record['structure'])
        if (not structure.is_ordered or not len(structure)
                or record.get('nsites') not in (None, len(structure))
                or not np.isfinite(structure.cart_coords).all()
                or not np.isfinite(structure.lattice.matrix).all()
                or not math.isfinite(structure.volume) or structure.volume <= 0):
            raise ValueError
        return structure
    except (KeyError, TypeError, ValueError, AttributeError, IndexError, OverflowError):
        raise ValueError('缺少可核实的末帧结构') from None


def _training(record, structure):
    labels = ((record.get('source_metadata') or {}).get('vasp_facts') or {}).get('labels') or {}
    energy = record.get('energy')
    forces = _numeric_array(record.get('forces'), (len(structure), 3))
    definition = labels.get('energy_definition')
    missing = []
    if type(energy) not in (int, float) or not math.isfinite(energy) or labels.get('energy_unit') not in (None, 'eV/cell'):
        missing.append('有效的末帧每胞总能量')
    if not isinstance(definition, str) or not definition.strip():
        missing.append('总能量定义')
    if forces is None or labels.get('force_unit') not in (None, 'eV/Angstrom'):
        missing.append('有效的末帧逐原子力')
    if missing:
        raise ValueError('无法导出微调 EXTXYZ，缺少' + '、'.join(missing) + '。')

    atoms = AseAtomsAdaptor.get_atoms(structure)
    atoms.info = {'energy_definition': definition, 'energy_unit': 'eV',
                  'energy_basis': 'cell_total', 'force_unit': 'eV/Angstrom'}
    for key in ('record_id', 'frame_index', 'source_dataset', 'source_id', 'functional'):
        if record.get(key) is not None:
            atoms.info[key] = record[key]
    results = {'energy': float(energy), 'forces': forces}
    _tensor_labels(record, atoms, results)
    atoms.calc = SinglePointCalculator(atoms, **results)
    return atoms


def verified_vasp_training_atoms(frame):
    """Convert verified VASP labels to EXTXYZ atoms without claiming an AIDB row."""
    from ase.units import GPa

    structure = _structure(frame)
    record = dict(frame)
    if record.get('stress') is not None:
        if record.get('stress_unit') != 'kbar' or record.get('stress_convention') != 'tensile_positive':
            raise ValueError('无法核实 VASP 应力单位或约定')
        stress = _numeric_array(record['stress'], (3, 3))
        if stress is None:
            raise ValueError('无法核实 VASP 应力张量')
        stress *= GPa / 10
        record.update(stress=stress, stress_unit='eV/Angstrom^3',
                      virial=-structure.volume * stress, virial_unit='eV')
    return _training(record, structure)


def _training_xyz(record):
    stream = StringIO()
    write(stream, _training(record, _structure(record)), format='extxyz')
    return stream.getvalue()


def _tensor_labels(record, atoms, results):
    # The installed AIDB reader exposes normalized tensile-positive stress.
    # Check the stored virial against the same cell; never synthesize a missing tensor.
    if (type(record.get('frame_index')) is not int or record['frame_index'] < 0
            or record.get('stress_unit') != 'eV/Angstrom^3'
            or record.get('stress_convention') != 'tensile_positive'):
        return
    stress = _numeric_array(record.get('stress'), (3, 3))
    if stress is None or not np.allclose(stress, stress.T, rtol=1e-8, atol=1e-10):
        return
    if record.get('virial') is not None:
        virial = _numeric_array(record['virial'], (3, 3))
        if (record.get('virial_unit') != 'eV' or virial is None
                or not np.allclose(virial, -atoms.get_volume() * stress, rtol=1e-6, atol=1e-8)):
            return
        atoms.info.update(virial=virial, virial_unit='eV', virial_convention='-V*stress')
    results['stress'] = stress
    atoms.info.update(stress_unit='eV/Angstrom^3', stress_convention='tensile_positive')


def _detail_html(record):
    meta = record.get('source_metadata') or {}
    facts = meta.get('vasp_facts') or {}
    label_facts = facts.get('labels') or {}
    method = facts.get('method') or {}
    sampling = facts.get('sampling') or {}
    source = facts.get('source') or {}
    structure = record.get('structure') or {}
    params = record.get('calculation_parameters') or {}
    force_unit = label_facts.get('force_unit', 'eV/Å')
    if force_unit == 'eV/Angstrom':
        force_unit = 'eV/Å'

    sections = [
        ('末帧结构与采样', [
            ('末帧晶相', facts.get('phase')), ('末帧空间群', facts.get('spacegroup')),
            ('末帧判定依据', facts.get('symmetry')), ('原子数', record.get('nsites')),
            ('晶格（Å）', (structure.get('lattice') or {}).get('matrix')),
            ('末帧晶体结构（笛卡尔坐标 Å，分数坐标无量纲）', structure),
            ('采样类型', record.get('calculation_type')), ('采样条件', sampling),
            ('帧索引（从 0 起）', record.get('frame_index')),
        ]),
        ('计算方法与磁性', [
            ('程序版本', method.get('version', meta.get('version'))), ('泛函', record.get('functional')),
            ('判定依据', method.get('functional_evidence')), ('泛函输入回显', method.get('functional_input')),
            ('泛函运行参数', method.get('functional_runtime')), ('DFT+U 设置', params.get('LDAU')),
            ('U 参数', record.get('hubbard_u')), ('赝势', method.get('potcar_symbols', meta.get('potcar_symbols'))),
            ('k 点', method.get('kpoints', meta.get('kpoints'))),
            ('输入截断能（eV）', method.get('input_encut_ev', params.get('ENCUT'))),
            ('自旋计算设置', method.get('spin')), ('材料磁性', method.get('magnetism', '未评估')),
            ('逐原子投影磁矩', record.get('magmom')), ('计算参数', params),
        ]),
        ('能量、力、应力和维里张量', [
            ('电子总能', record.get('energy')), ('能量单位', label_facts.get('energy_unit', 'eV/cell')),
            ('能量定义', label_facts.get('energy_definition')),
            ('逐原子力', record.get('forces')), ('力单位', force_unit),
            ('应力', record.get('stress')), ('应力单位', record.get('stress_unit')),
            ('应力符号约定', record.get('stress_convention')), ('维里张量', record.get('virial')),
            ('维里张量单位', record.get('virial_unit')), ('末步电子收敛', label_facts.get('electronic_converged')),
            ('弛豫离子收敛', label_facts.get('ionic_converged') if record.get('calculation_type') == 'relaxation' else '不适用'),
        ]),
        ('附加结果', [
            ('密度（g/cm³）', facts.get('density_g_cm3')), ('费米能（eV）', facts.get('fermi_energy_ev')),
            ('逐原子 PAW 投影电子布居（e/原子）', facts.get('paw_population_e_per_atom')),
        ]),
        ('来源与核对依据', [
            ('来源数据集', record.get('source_dataset')), ('来源 ID', record.get('source_id')),
            ('原始文件 SHA-256', source.get('file_sha256') or source.get('sha256') or meta.get('sha256')),
            ('XML 与 OUTCAR 配对检查', source.get('xml_outcar_checks')),
            ('系综相关输入', sampling.get('input_evidence')), ('系综判定', sampling.get('ensemble_evidence')),
            ('字段来源', source.get('field_evidence')), ('公开原始输出', source.get('public_source')),
            ('归一化依据', record.get('provenance')),
        ]),
    ]
    if record.get('calculation_type') == 'nscf':
        sections.insert(1, ('NSCF 能带', [
            ('能带数据摘要', facts.get('band_structure')), ('上游 SCF 记录及重启文件', source.get('upstream_scf')),
            ('能带交付文件 SHA-256', (source.get('band_artifacts') or {}).get('sha256')),
        ]))
    if record.get('calculation_type') == 'molecular_dynamics':
        initial = facts.get('initial_structure') or {}
        sections.insert(1, ('MD 起始构型', [
            ('起始空间群', initial.get('spacegroup')), ('空间群判定', initial.get('symmetry')),
            ('POSCAR 来源与核对', initial.get('source')), ('判定状态', initial.get('unknown_reason')),
            ('起始晶体结构', initial.get('structure')),
        ]))

    def value_html(value):
        if isinstance(value, (dict, list)):
            return '<pre>' + escape(json.dumps(value, ensure_ascii=False, indent=2)) + '</pre>'
        return escape('未知／未保存' if value is None else str(value))

    def section_html(title, rows):
        fields = ''.join(f'<dt>{name}</dt><dd>{value_html(value)}</dd>' for name, value in rows)
        return f'<details><summary>{title}</summary><dl>{fields}</dl></details>'

    details = ''.join(section_html(title, rows) for title, rows in sections)
    return ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>AIDB 记录详情</title>'
            '<style>body{max-width:850px;margin:28px auto;padding:0 16px;font:15px/1.55 system-ui}'
            'details{margin:12px 0;border:1px solid #ddd;padding:10px}summary{cursor:pointer}'
            'dt{font-weight:600}dd{margin:0 0 6px;overflow-wrap:anywhere}'
            'pre{white-space:pre-wrap;overflow-wrap:anywhere}</style></head>'
            f'<body><h1>AIDB 记录详情 · {value_html(record.get("formula_reduced"))}</h1>'
            f'<p>{value_html(record["record_id"])}</p>{details}'
            f'<details><summary>完整 JSON · 按 ID 读回的记录</summary>{value_html(record)}</details></body></html>')


def export_record(record, formats):
    record_id = re.sub(r'[^A-Za-z0-9_.-]', '_', record['record_id'])
    files, issues = {}, {}
    for fmt in formats:
        try:
            if fmt == 'extxyz':
                filename, content = 'aidb-' + record_id + '.extxyz', _training_xyz(record)
            elif fmt == 'poscar':
                filename, content = 'POSCAR-' + record_id, str(Poscar(_structure(record), sort_structure=False))
            elif fmt == 'temperature':
                filename, content = 'temperature-' + record_id + '.html', temperature_curve_html(record)
            else:
                filename, content = 'aidb-' + record_id + '.html', _detail_html(record)
            files[fmt] = {'filename': filename, 'media_type': 'text/html' if fmt in ('html', 'temperature') else 'text/plain',
                          'content': content}
        except ValueError as exc:
            issues[fmt] = str(exc)
    return files, issues
