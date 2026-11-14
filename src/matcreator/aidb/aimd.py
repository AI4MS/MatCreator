"""Prepare fixed-cell VASP AIMD and interpret its observed temperatures."""
from __future__ import annotations

from math import isfinite
from pathlib import Path
import re


TEMPERATURE = re.compile(
    r"^[ \t]*kin\.[ \t]+lattice[ \t]+EKIN_LAT[ \t]*=.*?\(temperature[ \t]+([0-9]+(?:\.[0-9]+)?)[ \t]+K\)",
    re.M,
)
SEED = re.compile(r"^[ \t]*RANDOM_SEED[ \t]*=[ \t]*(\d+)[ \t]+(\d+)[ \t]+(\d+)[ \t]*$", re.M)
MD_RUNTIME_TAGS = (
    "MDALGO", "TEBEG", "TEEND", "POTIM", "ANDERSEN_PROB", "LANGEVIN_GAMMA", "LANGEVIN_GAMMA_L",
    "LATTICE_CONSTRAINTS", "PMASS", "PSTRESS", "NHC_NCHAINS", "NHC_PERIOD", "NHC_NRESPA",
    "NHC_NS", "CSVR_PERIOD", "NSUBSYS", "TSUBSYS", "PSUBSYS", "ISYM",
)
MD_TYPE = "molecular_dynamics"
CURVE_FILENAME = "temperature-curve.html"


def md_runtime_conflicts(incar, runtime):
    """The effective Nosé mass can be calculated by VASP for SMASS=0."""
    conflicts = [tag for tag in MD_RUNTIME_TAGS if tag in incar and incar[tag] != runtime.get(tag)]
    if "SMASS" in incar:
        supplied, used = incar["SMASS"], runtime.get("SMASS")
        if used != supplied and not (supplied == 0 and runtime.get("MDALGO") in (0, 2) and _number(used, positive=True)):
            conflicts.append("SMASS")
    return conflicts


def md_matches(incar, parameters, sampling, frame_index):
    seed = incar.get("RANDOM_SEED")
    ns = incar.get("NSW")
    return (
        sampling.get("ensemble") in {"NVE", "NVT", "NpH", "NpT"}
        and type(ns) is int and ns == sampling.get("planned_ionic_steps") == sampling.get("source_ionic_steps")
        and frame_index == ns - 1
        and _valid_seed(seed) and sampling.get("initial_random_seed") == seed
        and _number(incar.get("POTIM"), positive=True) and sampling.get("step_fs") == incar["POTIM"]
        and _number(incar.get("TEBEG")) and sampling.get("target_start_kelvin") == incar["TEBEG"]
        and _number(incar.get("TEEND")) and sampling.get("target_end_kelvin") == incar["TEEND"]
        and not md_runtime_conflicts(incar, parameters)
    )


def _valid_seed(seed):
    return (
        isinstance(seed, (tuple, list)) and len(seed) == 3
        and all(type(value) is int for value in seed)
        and 0 <= seed[0] < 900_000_000 and 0 <= seed[1] < 1_000_000 and seed[2] >= 0
    )


def _number(value, *, minimum=0, positive=False):
    return type(value) in (int, float) and isfinite(value) and (value > minimum if positive else value >= minimum)


def prepare_aimd_nvt(
    workspace, structure_file, output_dir, *, temperature_kelvin, timestep_fs, steps,
    random_seed=(248489752, 0, 0), incar_overrides=None,
):
    """Write a new MPMDSet directory for one constant-temperature Nosé–Hoover run."""
    from pymatgen.core import Structure
    from pymatgen.io.vasp.sets import MPMDSet

    root = Path(workspace).resolve()
    source = (root / structure_file).resolve(strict=True)
    target = (root / output_dir).resolve()
    if not source.is_relative_to(root) or not source.is_file():
        raise ValueError("AIMD 结构文件必须位于当前工作区")
    if not target.is_relative_to(root) or target == root or target.exists():
        raise ValueError("请为 AIMD 输入指定工作区内的新目录")
    if (not _number(temperature_kelvin, positive=True) or not _number(timestep_fs, positive=True)
            or type(steps) is not int or steps < 1 or not _valid_seed(random_seed)):
        raise ValueError("AIMD 需要有效的目标温度、时间步长、步数和随机种子")

    structure = Structure.from_file(source)
    if not structure.is_ordered or not 1 <= len(structure) <= 1000:
        raise ValueError("AIMD 需要包含 1 到 1000 个原子的有序结构")
    settings = dict(incar_overrides or {})
    fixed = {
        "IBRION": 0, "ISIF": 2, "ISYM": 0, "MDALGO": 2, "SMASS": 0, "NBLOCK": 1,
        "TEBEG": temperature_kelvin, "TEEND": temperature_kelvin, "POTIM": timestep_fs, "NSW": steps,
        "RANDOM_SEED": list(random_seed), "ISTART": 0, "ICHARG": 2, "ML_LMLFF": False,
    }
    inconsistent = sorted(tag for tag in fixed if tag in settings and settings[tag] != fixed[tag])
    if inconsistent:
        raise ValueError("AIMD 参数与所请求的 NVT 设置冲突：" + ", ".join(inconsistent))
    incar = {"ENCUT": 520, "LWAVE": False, **settings, **fixed}
    spin = incar.get("ISPIN", 1) == 2
    inputs = MPMDSet(
        structure, start_temp=temperature_kelvin, end_temp=temperature_kelvin, nsteps=steps, time_step=timestep_fs,
        spin_polarized=spin, user_incar_settings=incar,
    )
    inputs.write_input(target)
    return target


def observed_temperature(section):
    matches = TEMPERATURE.findall(section)
    if len(matches) == 1 and _number(value := float(matches[0])):
        return value
    return None


def md_initial_seed(incar, before_first_integration):
    seed = incar.get("RANDOM_SEED")
    if not _valid_seed(seed):
        return None
    match = SEED.search(before_first_integration)
    return list(seed) if match and tuple(map(int, match.groups())) == tuple(seed) else None


def temperature_series(record):
    """Return only a complete series from the MD frame's paired-source evidence."""
    facts = (record.get("source_metadata") or {}).get("vasp_facts") or {}
    sampling = facts.get("sampling") or {}
    values = sampling.get("temperature_series_kelvin")
    hashes = (facts.get("source") or {}).get("sha256") or {}
    dt = sampling.get("step_fs")
    steps = sampling.get("planned_ionic_steps")
    if (record.get("calculation_type") != MD_TYPE or sampling.get("stored_scope") != "final_frame"
            or not re.fullmatch(r"[a-f0-9]{64}", hashes.get("OUTCAR") or "")
            or type(steps) is not int or steps <= 0 or sampling.get("source_ionic_steps") != steps
            or record.get("frame_index") != steps - 1 or not _number(dt, positive=True)
            or not isinstance(values, list) or len(values) != steps
            or any(not _number(value) for value in values)
            or sampling.get("observed_temperature_kelvin") != values[-1]):
        raise ValueError("缺少与此末帧对应的完整 OUTCAR 温度记录")
    return dt, values, hashes["OUTCAR"]


def temperature_curve_html(record):
    dt, values, digest = temperature_series(record)
    low, high = min(values), max(values)
    margin = max((high - low) * 0.12, 1)
    low, high = max(0, low - margin), high + margin
    points = [(80 + 560 * index / max(len(values) - 1, 1), 250 - 210 * (value - low) / (high - low))
              for index, value in enumerate(values)]
    line = " ".join(f"{x:.2f},{y:.2f}" for x, y in points)
    markers = "".join(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="3" fill="#146c83"/>' for x, y in points)
    ticks = "".join(f'<text x="74" y="{y}" text-anchor="end">{value:.1f}</text>'
                    for y, value in ((45, high), (150, (high + low) / 2), (255, low)))
    last_tick = f'<text x="640" y="272" text-anchor="middle">{dt * len(values):g}</text>' if len(values) > 1 else ""
    table = "".join(f"<tr><td>{step}</td><td>{step * dt:g}</td><td>{value}</td></tr>"
                    for step, value in enumerate(values, 1))
    return (
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>VASP AIMD 温度曲线</title>'
        '<style>body{margin:2em auto;padding:0 1em;max-width:48em;font:16px system-ui}'
        'svg{display:block;width:100%}td,th{padding:.3em 1em;text-align:right}p{overflow-wrap:anywhere}</style>'
        f'<h1>VASP AIMD 温度曲线</h1><p>OUTCAR SHA-256 {digest}</p>'
        '<svg viewBox="0 0 680 310" role="img" aria-label="温度（K）随模拟时间（fs）变化">'
        '<path d="M80 40V250H640" fill="none" stroke="#666"/>'
        '<path d="M80 40H640M80 145H640" stroke="#ddd"/>'
        f'<text x="80" y="22">温度（K）</text>{ticks}<text x="80" y="272" text-anchor="middle">{dt:g}</text>'
        f'{last_tick}<text x="360" y="300" text-anchor="middle">模拟时间（fs）</text>'
        f'<polyline points="{line}" fill="none" stroke="#146c83" stroke-width="2"/>{markers}</svg>'
        '<table><thead><tr><th>积分步</th><th>时间（fs）</th><th>温度（K）</th></tr></thead>'
        f'<tbody>{table}</tbody></table></html>'
    )
