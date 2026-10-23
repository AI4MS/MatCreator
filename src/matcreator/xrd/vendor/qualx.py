"""Subprocess bridge to an independently installed, isolated QualX3 CLI.

No QualX code or reference database is bundled here. QualX recalls candidates;
matching.py applies the same OAW ranking to library and manual references.
"""
import hashlib
from contextlib import closing
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time


class QualXError(ValueError):
    pass


def executable_path():
    override = os.environ.get('OAW_XRD_QUALX_EXECUTABLE')
    root = Path(os.environ.get('OAW_XRD_ROOT', str(Path(__file__).resolve().parents[5] / 'XRD')))
    name = 'qualx.exe' if os.name == 'nt' else 'qualx'
    candidates = [Path(override)] if override else [root / 'engines/qualx3/bin' / name]
    if not override and shutil.which('qualx'):
        candidates.append(Path(shutil.which('qualx')))
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise QualXError('未安装支持隔离检索的 QualX3。请配置 OAW_XRD_QUALX_EXECUTABLE，'
                     '或在卡片中选择「OAW 逐条检索」。安装说明见 plugins/xrd/README.md。')


def process_options():
    return {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}


def verify_executable(executable):
    try:
        probe = subprocess.run([str(executable), '--help'], capture_output=True, timeout=15, **process_options())
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise QualXError(f'无法启动 QualX3：{exc}') from exc
    help_text = (probe.stdout + probe.stderr).decode('utf-8', errors='replace')
    if probe.returncode or any(flag not in help_text for flag in ('--settings-dir', '--database', '--wavelength')):
        raise QualXError('当前 QualX3 缺少 OAW 隔离检索接口（--settings-dir / --database）。'
                         '请安装带桥接补丁的版本，或选择「OAW 逐条检索」。')
    return hashlib.sha256(executable.read_bytes()).hexdigest()


def validate_files(document):
    try:
        from .library import connect, validate_library
    except ImportError:
        from library import connect, validate_library
    validate_library(document)
    path = Path(document['path']).resolve()
    if path.suffix.lower() != '.sq':
        raise QualXError('QualX3 需要 .sq 主数据库及原配套索引文件')
    files = [Path(str(path) + suffix) for suffix in ('', '.info', '.infostat', '.search')]
    missing = [p.name for p in files if not p.is_file()]
    if missing:
        raise QualXError('QualX3 谱库缺少配套文件：' + '、'.join(missing) + '。请补齐原库，或选择「OAW 逐条检索」。')
    # QualX's SQL reader does not propagate every query error to the CLI. A
    # native four-column peak library could otherwise look like a successful
    # search with zero candidates. Validate the engine's actual requirements
    # here, leaving the native library adapter's narrower contract unchanged.
    schemas = {
        '': {'id': {'id', 'name', 'mineralname', 'chemical_formula', 'spacegroup',
                    'quality', 'rir', 'nrec', 'dvalue', 'intensita', 'n'},
             'chemical': {'id', 'chemical_element'}},
        '.search': {'top': {'id', 'n', 'dval'}},
    }
    for suffix in ('', '.info', '.infostat', '.search'):
        file = Path(str(path) + suffix)
        try:
            with closing(connect(file)) as db:
                db.execute('PRAGMA schema_version').fetchone()
                for table, required in schemas.get(suffix, {}).items():
                    columns = {row['name'] for row in db.execute(f'PRAGMA table_info("{table}")')}
                    absent = required - columns
                    if absent:
                        raise QualXError(f'QualX3 谱库结构不兼容：{file.name} 的 {table} 表缺少 '
                                         + '、'.join(sorted(absent))
                                         + '。请使用完整原配套谱库，或选择「OAW 逐条检索」。')
        except sqlite3.DatabaseError as exc:
            raise QualXError(f'QualX3 谱库无法读取：{file.name}。请检查原配套数据库文件，'
                             '或选择「OAW 逐条检索」。') from exc
    return path, [{'path': str(p), 'size_bytes': p.stat().st_size, 'mtime_ns': p.stat().st_mtime_ns} for p in files]


def parse_search_output(stdout, stderr):
    """Reject the stock CLI's successful metadata-query fallback and partial logs."""
    found = re.search(r'Found (\d+) card\(s\):', stdout)
    peaks = re.search(r'd values:\s+QList\(([^)]*)\)', stderr)
    strongest = re.search(r'Number of strongest matches:\s*(\d+)', stderr)
    if not found or not peaks or not strongest:
        raise QualXError('QualX3 没有完成有效的实验谱检索（读谱或检峰失败）。请查看本次 QualX 日志。')
    ids = re.findall(r'^\s*\[([0-9]+)\]\s', stdout, flags=re.MULTILINE)
    count = int(found[1])
    if len(ids) != count or len(set(ids)) != count:
        raise QualXError('QualX3 候选输出不完整或格式不受支持，请查看本次日志')
    timer = re.search(r'makeQueryStrongest->makeQueryInfoIdsWithFom" elapsed time:\s*"([\d.]+)', stderr)
    return ids, {'candidate_count': count, 'observed_peak_count': len(peaks[1].split(',')) if peaks[1].strip() else 0,
                 'strong_peak_candidates': int(strongest[1]),
                 'internal_search_seconds': float(timer[1]) if timer else None,
                 'candidate_limit': 3000, 'candidate_limit_reached': count >= 3000}


def search_library(pattern, item, options, *, run_dir=None, index=0):
    try:
        from .library import allowed_elements
    except ImportError:
        from library import allowed_elements
    allowed = sorted(allowed_elements(options.get('library_elements', '')))
    database, before_files = validate_files(item['value'])
    executable = executable_path()
    binary_hash = verify_executable(executable)
    points = pattern['points']
    wavelength = options['wavelength']
    if (not math.isfinite(wavelength) or wavelength <= 0 or len(points) < 20 or
            any(len(p) != 2 or not all(math.isfinite(v) for v in p) or p[1] < 0 for p in points) or
            any(points[i][0] >= points[i + 1][0] for i in range(len(points) - 1))):
        raise QualXError('实验谱需要至少 20 个递增角度、有限非负强度数据点和有效波长')
    destination = Path(run_dir) / 'qualx' / str(index) if run_dir else None
    temporary = None
    if destination:
        destination.mkdir(parents=True, exist_ok=False)
    # The vendor Fortran reader uses local8Bit filenames. Keep its transient
    # input path ASCII while preserving archives in OAW's Unicode run directory.
    if destination is None or not str(destination).isascii():
        temporary = tempfile.TemporaryDirectory(prefix='oaw-qualx-', dir=os.environ.get('OAW_XRD_QUALX_TEMP'))
        work = Path(temporary.name)
        if not str(work).isascii():
            temporary.cleanup()
            raise QualXError('请将 OAW_XRD_QUALX_TEMP 配置到仅含英文字符的临时目录，以兼容 QualX 读谱')
    else:
        work = destination
    settings = work / 'settings'
    settings.mkdir()
    xy = work / 'pattern.xy'
    xy.write_text(f'{wavelength:.17g}\n' + ''.join(f'{x:.17g} {y:.17g}\n' for x, y in points), encoding='ascii')
    command = [str(executable), '--nogui', '--search', str(xy),
               '--settings-dir', str(settings), '--database', str(database), '--wavelength', str(wavelength)]
    if allowed:
        command += ['--composition', ' AND '.join(allowed), '--contains-any']
    env = dict(os.environ, QT_FORCE_STDERR_LOGGING='1', LC_ALL='C')
    started = time.monotonic()
    info = {'engine': 'QualX3', 'library_node_id': item['node_id'], 'database_sha256': item['value']['sha256'],
            'executable': str(executable), 'executable_sha256': binary_hash, 'command': command,
            'database_files': before_files, 'wavelength': wavelength, 'allowed_elements': allowed,
            'settings': {'isolated': True, 'strongest_peaks': 3, 'min_fom': .35, 'max_entries': 3000,
                         'peak_tolerance': 'automatic', 'oaw_ranking': 'separate'}, 'status': 'running'}
    try:
        print(f"QualX3: {item['value']['filename']} ({item['value']['count']:,} references)", flush=True)
        with (work / 'stdout.log').open('wb') as stdout, (work / 'stderr.log').open('wb') as stderr:
            try:
                completed = subprocess.run(command, cwd=work, env=env, stdout=stdout, stderr=stderr,
                                           timeout=300, **process_options())
            except subprocess.TimeoutExpired as exc:
                raise QualXError('QualX3 检索超过 300 秒，已终止；请检查谱库和本次日志') from exc
        info['returncode'] = completed.returncode
        stderr = (work / 'stderr.log').read_text(encoding='utf-8', errors='replace')
        stdout = (work / 'stdout.log').read_text(encoding='utf-8', errors='replace')
        if completed.returncode:
            raise QualXError(f'QualX3 检索失败（退出码 {completed.returncode}）：{stderr[-2000:]}')
        ids, stats = parse_search_output(stdout, stderr)
        for original in before_files:
            stat = Path(original['path']).stat()
            if stat.st_size != original['size_bytes'] or stat.st_mtime_ns != original['mtime_ns']:
                raise QualXError('检索期间谱库文件发生变化，请重新挂载后再运行')
        info.update(stats, status='completed')
        print(f'QualX3 returned {len(ids)} candidates; OAW comparison follows.', flush=True)
        return ids, info
    except Exception as exc:
        info.update(status='failed', error=str(exc))
        raise
    finally:
        info['elapsed_seconds'] = round(time.monotonic() - started, 3)
        (work / 'engine.json').write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding='utf-8')
        if temporary:
            if destination:
                shutil.copytree(work, destination, dirs_exist_ok=True)
            temporary.cleanup()
