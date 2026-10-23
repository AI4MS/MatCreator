"""Immutable scientific frames; the index is atomically replaced for live readers."""
import asyncio
import base64
import hashlib
import json
from pathlib import Path


def write(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    temp.replace(path)


class FrameWriter:
    def __init__(self, run, stage, observed):
        self.run = Path(run)
        self.directory = self.run / 'frames'
        self.directory.mkdir(exist_ok=True)
        self.index = {'run_id': self.run.name.removeprefix('oaw-'), 'stage': stage,
                      'observed': observed, 'frames': []}

    def append(self, candidate_id, label, *, cif=None, peaks=None, calculated=None,
               metric=None, quality=None, iteration=None, state='snapshot', error=None):
        number = len(self.index['frames'])
        value = dict(candidate_id=candidate_id, label=label, iteration=iteration,
                     state=state, metric=metric, quality=quality, error=error)
        if cif:
            raw = cif.encode('utf-8')
            value['cif'] = {'filename': f'frame-{number:05d}.cif',
                'source_base64': base64.b64encode(raw).decode(), 'sha256': hashlib.sha256(raw).hexdigest()}
        if peaks is not None:
            value['peaks'] = peaks
        if calculated is not None:
            value['calculated'] = calculated
        write(self.directory / f'{number:05d}.json', value)
        self.index['frames'].append({**{k:v for k,v in value.items() if k not in ('cif','peaks','calculated')}, 'index': number})
        write(self.run / 'frames.json', self.index)


async def search_frames(run, result):
    """Download every returned COD candidate, bounded to four concurrent requests."""
    from . import structures
    from .pipeline import decoded_cif
    from .library import expand_library_slots
    snapshot = json.loads((run / 'input-snapshot.json').read_text(encoding='utf-8'))
    library_ids = {i['node_id'] for i in expand_library_slots(snapshot) if i['value']['kind'] == 'library'}
    pattern = next(i['value']['points'] for i in snapshot if i['value']['kind'] == 'pattern')
    writer = FrameWriter(run, 'search', pattern)
    semaphore = asyncio.Semaphore(4)
    async def load(c):
        async with semaphore:
            try:
                meta = c.get('metadata') or {}
                cod = meta.get('reference_code', '')
                library = meta.get('library_node_id')
                if library in library_ids and c['node_id'] == f'{library}:{cod}':
                    document = await structures.prepare_cod_structure({}, {'cod_id': cod})
                    source = decoded_cif(document['structure'])
                else:
                    ids = {x['node_id'] for x in c.get('cifs', [])}
                    matches = [i['value'] for i in snapshot if i['node_id'] in ids and i['value'].get('reference_node_id') == c['node_id']]
                    if len(matches) != 1:
                        raise ValueError('没有唯一关联的 CIF')
                    source = decoded_cif(matches[0])
                return source['text'], None
            except Exception as exc:
                return None, str(exc)
    # Preserve ranking order even when downloads complete out of order.
    loaded = await asyncio.gather(*(load(c) for c in result.get('candidates', [])))
    for c, (text, error) in zip(result.get('candidates', []), loaded):
        score = c.get('score')
        writer.append(c['node_id'], c['filename'], cif=text, peaks=c.get('peaks', []),
            quality=score, metric={'name': '峰匹配分数', 'value': None if score is None else score * 100, 'unit': '%'},
            state='initial', error=error)
    # Also publish an empty index when no candidates match.
    write(run / 'frames.json', writer.index)


def read_frames(directory):
    try:
        index = json.loads((directory / 'frames.json').read_text(encoding='utf-8'))
        index['frames'] = [json.loads((directory / 'frames' / f'{i:05d}.json').read_text(encoding='utf-8'))
                           for i in range(len(index['frames']))]
        return index
    except (OSError, ValueError):
        return None
