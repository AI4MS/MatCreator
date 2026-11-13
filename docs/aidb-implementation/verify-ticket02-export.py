"""Verify all three exports against a fresh read from an isolated real AIDB database."""
import argparse
from hashlib import sha256
from html import unescape
import json
from pathlib import Path
import re

from ase.io import read
import dpdata
import numpy as np

from matcreator.aidb.service import InstalledSkillQuery, SessionAIDB


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('config', type=Path)
    parser.add_argument('installed_script', type=Path)
    parser.add_argument('record_id')
    parser.add_argument('output', type=Path)
    args = parser.parse_args()

    paths = json.loads(args.config.read_text())['ase_sqlite']
    database = [Path(paths[key]) for key in ('path', 'json_sidecar_path')]
    before = {path.name: sha256(path.read_bytes()).hexdigest() for path in database}
    service = SessionAIDB(args.output, 'ticket02-verify', query_database=InstalledSkillQuery(args.installed_script.resolve()))
    service.bind(args.config)
    snapshot = service.detail(args.record_id)
    record = snapshot['record']
    reference = service.publish(snapshot)
    formats = ['extxyz', 'poscar', 'html']
    direct = service.export(args.record_id, formats)
    row = service.export(args.record_id, formats, result_id=reference['result_id'], binding_id=reference['binding_id'])
    assert direct['status'] == row['status'] == 'complete'
    files = {}
    for fmt in formats:
        data = service.export_file(row['export_id'], fmt)
        assert data == service.export_file(direct['export_id'], fmt)
        files[fmt] = args.output / data['filename']
        files[fmt].write_text(data['content'], encoding='utf-8')

    structure = record['structure']
    atoms = read(files['extxyz'], format='extxyz')
    poscar = read(files['poscar'], format='vasp')
    symbols = [site['species'][0]['element'] for site in structure['sites']]
    for decoded in (atoms, poscar):
        assert decoded.get_chemical_symbols() == symbols
        np.testing.assert_allclose(decoded.cell, structure['lattice']['matrix'], atol=1e-8)
        np.testing.assert_allclose(decoded.positions, [site['xyz'] for site in structure['sites']], atol=1e-8)
    assert atoms.pbc.tolist() == structure['lattice']['pbc']
    assert atoms.info['record_id'] == record['record_id']
    assert atoms.info['frame_index'] == record['frame_index']
    assert atoms.info['energy_definition'] == record['source_metadata']['vasp_facts']['labels']['energy_definition']
    assert atoms.info['energy_unit'] == 'eV'
    assert atoms.info['energy_basis'] == 'cell_total'
    assert atoms.info['force_unit'] == 'eV/Angstrom'
    assert atoms.get_potential_energy() == record['energy']
    np.testing.assert_allclose(atoms.get_forces(), record['forces'], atol=1e-8)
    np.testing.assert_allclose(atoms.get_stress(voigt=False), record['stress'])
    np.testing.assert_allclose(atoms.info['virial'], record['virial'])
    assert atoms.info['virial_unit'] == 'eV'

    html = files['html'].read_text(encoding='utf-8')
    full_record = re.search(r'完整 JSON[^<]+</summary><pre>(.*?)</pre>', html, re.DOTALL)
    assert json.loads(unescape(full_record[1])) == record

    system = dpdata.LabeledSystem(str(files['extxyz']), fmt='extxyz')
    deepmd = args.output / 'deepmd'
    system.to('deepmd/npy', str(deepmd))
    roundtrip = dpdata.LabeledSystem(str(deepmd), fmt='deepmd/npy')
    for key in ('cells', 'coords', 'energies', 'forces', 'virials'):
        np.testing.assert_allclose(roundtrip[key], system[key], atol=1e-6)
    np.testing.assert_allclose(roundtrip['energies'], [record['energy']])
    np.testing.assert_allclose(roundtrip['forces'], [record['forces']], atol=1e-8)
    np.testing.assert_allclose(roundtrip['virials'], [record['virial']])

    after = {path.name: sha256(path.read_bytes()).hexdigest() for path in database}
    assert before == after
    print(json.dumps({
        'record_id': args.record_id,
        'frame_index': record['frame_index'],
        'formats': {key: str(path) for key, path in files.items()},
        'database_sha256': after,
        'extxyz_sha256': sha256(files['extxyz'].read_bytes()).hexdigest(),
        'deepmd': str(deepmd),
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
