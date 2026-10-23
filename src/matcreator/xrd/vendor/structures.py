"""Retrieve public COD structures without sending experimental data to COD."""
import asyncio
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from urllib.parse import urljoin, urlsplit
from uuid import uuid4

import httpx
from pydantic import BaseModel, Field
from matcreator.xrd.compat import NodeDocumentDownload, ResourceValidationError

MAX_CIF_BYTES = 2 * 1024 * 1024
COD_ORIGIN = 'https://www.crystallography.net'


class CandidateStructure(BaseModel):
    cod_id: str = Field(pattern=r'^[1-9][0-9]{6}$')
    filename: str
    source_base64: str
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    source_url: str
    downloaded_at: str
    local_path: str
    metadata: dict = Field(default_factory=dict)


class MatchDocument(BaseModel):
    structure: CandidateStructure | None = None


def cache_root():
    root = Path(os.environ.get('OAW_XRD_ROOT', str(Path(__file__).resolve().parents[5] / 'XRD')))
    return Path(os.environ.get('OAW_XRD_STRUCTURE_ROOT', str(root / 'structures/cod'))).resolve()


def cod_url(cod_id):
    if not isinstance(cod_id, str) or not re.fullmatch(r'[1-9][0-9]{6}', cod_id):
        raise ResourceValidationError('需要有效的 7 位 COD 编号')
    return f'{COD_ORIGIN}/cod/{cod_id}.cif'


def validate_cif(raw, cod_id):
    if not raw or len(raw) > MAX_CIF_BYTES or b'\x00' in raw:
        raise ResourceValidationError('COD CIF 为空、超过 2 MiB 或不是文本文件')
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError as exc:
        raise ResourceValidationError('COD 返回的 CIF 不是有效 UTF-8 文本') from exc
    blocks = re.findall(r'^data_(\S+)\s*$', text, re.MULTILINE)
    required = ('_cell_length_a', '_cell_length_b', '_cell_length_c',
                '_cell_angle_alpha', '_cell_angle_beta', '_cell_angle_gamma',
                '_atom_site_fract_x', '_atom_site_fract_y', '_atom_site_fract_z')
    if blocks != [cod_id] or any(not re.search(r'^' + tag + r'(?:\s|$)', text, re.MULTILINE) for tag in required):
        raise ResourceValidationError('COD 返回的内容缺少晶胞或原子坐标，或条目编号与候选不一致')
    return text


async def download_cif(cod_id):
    """Only fixed HTTPS COD endpoints; never a caller-supplied URL or path."""
    url = cod_url(cod_id)
    async with httpx.AsyncClient(timeout=httpx.Timeout(20, connect=10), follow_redirects=False,
                                 headers={'User-Agent': 'OAW-XRD/0.2', 'Accept': 'chemical/x-cif, text/plain'}) as client:
        for _ in range(4):
            async with client.stream('GET', url) as response:
                if response.is_redirect:
                    url = urljoin(url, response.headers.get('location', ''))
                    target = urlsplit(url)
                    if (target.scheme != 'https' or target.hostname not in {'www.crystallography.net', 'crystallography.net'}
                            or target.port not in (None, 443) or target.username or target.password
                            or target.path != f'/cod/{cod_id}.cif' or target.query or target.fragment):
                        raise ResourceValidationError('COD 下载重定向到了不支持的地址')
                    continue
                response.raise_for_status()
                declared = response.headers.get('content-length', '')
                if declared.isdigit() and int(declared) > MAX_CIF_BYTES:
                    raise ResourceValidationError('COD CIF 超过 2 MiB')
                data = bytearray()
                async for block in response.aiter_bytes():
                    data.extend(block)
                    if len(data) > MAX_CIF_BYTES:
                        raise ResourceValidationError('COD CIF 超过 2 MiB')
                raw = bytes(data)
                validate_cif(raw, cod_id)
                return raw
    raise ResourceValidationError('COD 下载重定向次数过多')


def read_cached(cod_id):
    directory = cache_root() / cod_id
    try:
        metadata = json.loads((directory / 'current.json').read_text(encoding='utf-8'))
        digest = metadata['sha256']
        if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
            return None
        path = directory / f'{digest}.cif'
        if path.stat().st_size > MAX_CIF_BYTES:
            return None
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != digest:
            return None
        validate_cif(raw, cod_id)
        return make_structure(cod_id, raw, path, metadata['downloaded_at'])
    except (OSError, ValueError, KeyError, TypeError, ResourceValidationError):
        return None


def make_structure(cod_id, raw, path, downloaded_at):
    return CandidateStructure(cod_id=cod_id, filename=f'COD-{cod_id}.cif',
        source_base64=base64.b64encode(raw).decode('ascii'), sha256=hashlib.sha256(raw).hexdigest(),
        source_url=cod_url(cod_id), downloaded_at=downloaded_at, local_path=str(path),
        metadata={'source': 'COD', 'association': 'candidate_only'}).model_dump(mode='json')


def atomic_write(path, data):
    temporary = path.with_name(f'.{path.name}.{uuid4().hex}.tmp')
    try:
        temporary.write_bytes(data)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def save_cached(cod_id, raw):
    validate_cif(raw, cod_id)
    digest = hashlib.sha256(raw).hexdigest()
    directory = cache_root() / cod_id
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f'{digest}.cif'
    atomic_write(path, raw)
    now = datetime.now(timezone.utc).isoformat()
    atomic_write(directory / 'current.json', json.dumps({'sha256': digest, 'downloaded_at': now,
                 'source_url': cod_url(cod_id)}, indent=2).encode('utf-8'))
    return make_structure(cod_id, raw, path, now)


async def prepare_cod_structure(value, arguments):
    cod_id = arguments.get('cod_id')
    cod_url(cod_id)  # Validate before constructing a path or making a request.
    try:
        structure = await asyncio.to_thread(read_cached, cod_id)
        if structure is None:
            raw = await asyncio.wait_for(download_cif(cod_id), timeout=40)
            structure = await asyncio.to_thread(save_cached, cod_id, raw)
        return {'structure': structure}
    except (httpx.TimeoutException, TimeoutError) as exc:
        raise ResourceValidationError('COD 下载超时，请重试；已有匹配结果已保留') from exc
    except httpx.HTTPStatusError as exc:
        raise ResourceValidationError(f'COD 下载失败（HTTP {exc.response.status_code}），请稍后重试') from exc
    except httpx.HTTPError as exc:
        raise ResourceValidationError('无法连接 COD，请检查网络后重试') from exc
    except OSError as exc:
        raise ResourceValidationError(f'无法保存本地 CIF：{exc}') from exc


def apply_cod_structure(value, prepared):
    return {**value, 'structure': CandidateStructure.model_validate(prepared['structure']).model_dump(mode='json')}


def download_structure(value):
    structure = value.get('structure')
    if not structure:
        raise ResourceValidationError('请先下载一个候选结构')
    return NodeDocumentDownload(structure['filename'], base64.b64decode(structure['source_base64']), 'chemical/x-cif')
