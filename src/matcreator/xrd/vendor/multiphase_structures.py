"""Transport phase-specific CIFs with explicit immutable input/fit lineage."""
import base64
import hashlib
import math


def structure_payload(candidate_id, label, text, *, cell=None, source_kind='input'):
    original = text.encode('utf-8')
    if cell is not None:
        from pymatgen.io.cif import CifFile
        if len(cell) != 6 or not all(math.isfinite(float(v)) for v in cell) or min(cell[:3]) <= 0 or not all(0 < v < 180 for v in cell[3:]):
            raise ValueError('Invalid fitted cell for phase CIF')
        angles = [math.radians(v) for v in cell[3:]]
        ca, cb, cg = map(math.cos, angles)
        if 1 + 2*ca*cb*cg-ca*ca-cb*cb-cg*cg <= 0:
            raise ValueError('Fitted cell has no positive volume')
        cif = CifFile.from_str(text)
        blocks = [b for b in cif.data.values() if '_cell_length_a' in b.data]
        if len(blocks) != 1:
            raise ValueError('Expected one structural CIF block')
        for key, value in zip(('a', 'b', 'c', 'alpha', 'beta', 'gamma'), cell):
            blocks[0].data[('_cell_length_' if key in ('a', 'b', 'c') else '_cell_angle_') + key] = f'{value:.10g}'
        text = str(cif)
    raw = text.encode('utf-8')
    # The scientific files remain available even if too large for a live canvas.
    if len(raw) > 2_000_000:
        return {'candidate_id': candidate_id, 'label': label, 'source_kind': source_kind,
                'error': '该 CIF 超过实时画布传输上限，请查看运行目录中的结构文件。'}
    return {'candidate_id': candidate_id, 'label': label, 'source_kind': source_kind,
            'input_sha256': hashlib.sha256(original).hexdigest(),
            'cif': {'filename': 'phase-' + hashlib.sha256(candidate_id.encode()).hexdigest()[:12] + '.cif',
                    'source_base64': base64.b64encode(raw).decode('ascii'), 'sha256': hashlib.sha256(raw).hexdigest()}}


def fitted_structures(candidates, combo, cells, *, source_kind):
    output = []
    for index, identifier in enumerate(combo):
        candidate = candidates[identifier]
        label = candidate.get('label', identifier)
        try:
            source = candidate.get('starting_cif') or candidate.get('source_cif')
            text = source['text']
            digest = hashlib.sha256(text.encode('utf-8')).hexdigest()
            if source.get('sha256') and digest != source['sha256']:
                raise ValueError('Input CIF SHA256 mismatch')
            output.append(structure_payload(identifier, label, text, cell=cells[index], source_kind=source_kind))
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            output.append({'candidate_id': identifier, 'label': label, 'source_kind': source_kind, 'error': str(exc)})
    return output
