"""Read-only QualX/COD reference library adapter; no vendor code or data bundled."""
import hashlib
from contextlib import closing
import math
from pathlib import Path
import sqlite3
import re

ELEMENTS = set('H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr Rf Db Sg Bh Hs Mt Ds Rg Cn Nh Fl Mc Lv Ts Og'.split())

def allowed_elements(text):
    symbols = set(re.split(r'[,，;\s]+', text.strip())) - {''}
    if not symbols.issubset(ELEMENTS):
        raise ValueError('元素条件请使用空格或逗号分隔的正确元素符号，例如 Li Ti P O')
    return symbols


def connect(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    db.execute('PRAGMA query_only=ON')
    db.row_factory = sqlite3.Row
    return db


def inspect_library(path):
    path = Path(path).expanduser().resolve(strict=True)
    if not path.is_file():
        raise ValueError('请选择 QualX / POW_COD 的 .sq 主数据库文件')
    with closing(connect(path)) as db:
        columns = {r['name'] for r in db.execute('PRAGMA table_info(id)')}
        if not {'id', 'chemical_formula', 'dvalue', 'intensita'}.issubset(columns):
            raise ValueError('不支持的数据库结构：需要 QualX / POW_COD SQLite 峰表；不能直接读取 ICDD 二进制库')
        count = db.execute('SELECT count(*) FROM id').fetchone()[0]
        info = dict(db.execute('SELECT * FROM infodb LIMIT 1').fetchone() or {})
    if not count:
        raise ValueError('数据库为空')
    digest = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b''):
            digest.update(block)
    stat = path.stat()
    return {'kind': 'library', 'filename': path.name, 'path': str(path),
            'sha256': digest.hexdigest(), 'size_bytes': stat.st_size, 'mtime_ns': stat.st_mtime_ns,
            'count': count, 'metadata': {'source': 'QualX / POW_COD', 'version': str(info.get('date', '')),
                'type': str(info.get('type', '')), 'reference_kind': 'calculated'}, 'points': [], 'peaks': []}


def validate_library(doc):
    if doc.get('slots'):
        for mounted in doc['slots']:
            if mounted:
                validate_library(mounted)
        return
    path = Path(doc.get('path', ''))
    if not path.is_file():
        raise ValueError('参考谱库文件不存在，请在谱库卡片中配置本机 .sq 路径')
    stat = path.stat()
    if stat.st_size != doc.get('size_bytes') or stat.st_mtime_ns != doc.get('mtime_ns'):
        raise ValueError('谱库文件已变更，请重新点击「加载谱库」确认版本')


def expand_library_slots(inputs):
    result = []
    for item in inputs:
        slots = item['value'].get('slots')
        if item['value']['kind'] == 'library' and slots:
            for index, mounted in enumerate(slots):
                if mounted:
                    result.append({**item, 'node_id':f"{item['node_id']}:slot{index+1}", 'value':mounted})
        else:
            result.append(item)
    return result


def iter_references(item, allowed=None, reference_ids=None):
    """Stream every record. Native intensities may peak at 1000, not 100."""
    import numpy as np
    doc = item['value']
    validate_library(doc)
    with closing(connect(doc['path'])) as db:
        # Filter small metadata first: rejected entries must not load their large
        # peak arrays. Keep ID traversal order and the exact existing ranking.
        if reference_ids is None:
            query = ('SELECT rowid AS record_rowid, id, chemical_formula FROM id ORDER BY id'
                     if allowed else 'SELECT id, chemical_formula, dvalue, intensita FROM id ORDER BY id')
            rows = db.execute(query)
        else:
            # Only read the records returned by the connected library's search
            # process. Parameter binding also handles non-numeric reference IDs.
            ids = sorted(set(reference_ids))
            def selected_rows():
                for start in range(0, len(ids), 400):
                    batch = ids[start:start + 400]
                    placeholders = ','.join('?' for _ in batch)
                    found = list(db.execute(
                        f'SELECT rowid AS record_rowid, id, chemical_formula, dvalue, intensita FROM id WHERE id IN ({placeholders}) ORDER BY id', batch))
                    if {str(row['id']) for row in found} != set(batch):
                        raise ValueError('QualX 返回的候选不属于当前连接的谱库，请检查数据库配置')
                    yield from found
            rows = selected_rows()
        for row in rows:
            if allowed and not set(re.findall(r'[A-Z][a-z]?',row['chemical_formula'] or '')).issubset(allowed):
                yield {'excluded':True}
                continue
            if allowed and reference_ids is None:
                row = db.execute('SELECT id, chemical_formula, dvalue, intensita FROM id WHERE rowid=?',
                                 (row['record_rowid'],)).fetchone()
            def numbers(value):
                if isinstance(value, bytes):
                    value = value.decode('ascii')
                return np.fromstring(str(value or '').strip().rstrip(','), sep=',')
            d = numbers(row['dvalue'])
            intensity = numbers(row['intensita'])
            if len(d) != len(intensity) or not len(d) or not np.isfinite(d).all() or not np.isfinite(intensity).all() or (d <= 0).any() or (intensity < 0).any() or intensity.max() <= 0:
                yield {'invalid_record':str(row['id'])}
                continue
            intensity = intensity / intensity.max() * 100
            yield {'node_id': f"{item['node_id']}:{row['id']}", 'revision': item['revision'],
                'value': {'filename': f"COD {row['id']} · {row['chemical_formula']}",
                    'metadata': {'formula': row['chemical_formula'] or '', 'reference_code': str(row['id']),
                        'source': doc['metadata']['source'], 'library_node_id': item['node_id'],
                        'reference_kind': 'calculated', 'url': f"https://www.crystallography.net/cod/{row['id']}.html"},
                    '_d': d, '_intensity': intensity}}
