"""Validate and render a VASP band path and its sampled eigenvalues."""
from __future__ import annotations

import json
from math import isfinite
import re
from tempfile import TemporaryDirectory

import numpy as np

from matcreator.aidb.service import AIDBError


BAND_FILES = ("bands.json", "bands.svg")


def band_input_path(kpoints):
    from pymatgen.io.vasp import Kpoints

    labels = kpoints.labels or [None] * len(kpoints.kpts)
    points = np.asarray(kpoints.kpts, dtype=float)
    if kpoints.style == Kpoints.supported_modes.Line_mode:
        count = kpoints.num_kpts
        if count < 2 or len(points) < 2 or len(points) % 2:
            raise AIDBError("无效的 VASP k 路径分段", "invalid_output")
        points = np.concatenate([np.linspace(start, end, count) for start, end in zip(points[::2], points[1::2])])
        branches = [(index, index + count - 1) for index in range(0, len(points), count)]
        labels = [label for first, last in zip(labels[::2], labels[1::2])
                  for label in [first, *([None] * (count - 2)), last]]
        weights = np.ones(len(points))
        cartesian = (kpoints.coord_type or "").lower().startswith(("c", "k"))
    else:
        if kpoints.style not in {Kpoints.supported_modes.Reciprocal, Kpoints.supported_modes.Cartesian}:
            raise AIDBError("能带计算需要显式 k 路径", "invalid_output")
        # Adjacent named points separate explicitly listed branches in the
        # pymatgen path convention. Keep unnamed explicit paths continuous.
        breaks = [i for i in range(1, len(points)) if labels[i - 1] and labels[i]]
        boundaries = [0, *breaks, len(points)]
        branches = [(start, stop - 1) for start, stop in zip(boundaries, boundaries[1:])]
        # Two-point paths and adjacent named vertices are valid samples.
        if any(start == stop for start, stop in branches):
            branches = [(0, len(points) - 1)]
        weights = np.asarray(kpoints.kpts_weights, dtype=float)
        cartesian = kpoints.style == Kpoints.supported_modes.Cartesian
    if cartesian:
        # VASP Cartesian KPOINTS are in 2π / a units, with a the POSCAR
        # scaling factor. That factor is unavailable in the result XML.
        raise AIDBError("请使用倒易晶格坐标定义 VASP k 路径", "invalid_output")
    if (points.ndim != 2 or points.shape != (len(labels), 3) or len(points) < 2
            or not np.isfinite(points).all() or not np.isfinite(weights).all()
            or weights.shape != (len(points),) or np.any(weights < 0) or not np.sum(weights) > 0):
        raise AIDBError("无效的 VASP k 路径坐标或权重", "invalid_output")
    return points, weights / np.sum(weights), labels, branches


def verify_band_path(kpoints, parsed):
    points, weights, labels, branches = band_input_path(kpoints)
    actual = np.asarray(parsed.actual_kpoints, dtype=float)
    actual_weights = np.asarray(parsed.actual_kpoints_weights, dtype=float)
    if (actual.shape != points.shape or not np.allclose(actual, points, rtol=0, atol=1e-7)
            or actual_weights.shape != weights.shape
            or not np.allclose(actual_weights, weights, rtol=0, atol=1e-7)):
        raise AIDBError("实际 VASP k 路径与 KPOINTS 不一致", "source_conflict")
    return actual, actual_weights, labels, branches


def read_bands(parsed, kpoints, outcar):
    from pymatgen.electronic_structure.core import Spin

    points, weights, labels, branches = verify_band_path(kpoints, parsed)
    bands = parsed.eigenvalues or {}
    expected_spins = {Spin.up, Spin.down} if parsed.parameters.get("ISPIN") == 2 else {Spin.up}
    nbands = parsed.parameters.get("NBANDS")
    if (type(nbands) is not int or nbands < 1 or bands.keys() != expected_spins
            or any(values.shape != (len(points), nbands, 2) or not np.isfinite(values).all()
                   for values in bands.values())):
        raise AIDBError("vasprun.xml 缺少完整的能带本征值和占据数", "invalid_output")
    fermi = re.findall(r"^[ \t]*E-fermi[ \t]*:[ \t]+(\S+)", outcar, re.M)
    fermi_ev = float(fermi[-1]) if fermi else float("nan")
    if not isfinite(fermi_ev):
        raise AIDBError("OUTCAR 缺少有限的能带费米能", "invalid_output")
    if parsed.efermi is not None and not np.isclose(parsed.efermi, fermi_ev, rtol=0, atol=6e-5):
        raise AIDBError("XML 与 OUTCAR 的能带费米能不一致", "source_conflict")
    reciprocal = parsed.initial_structure.lattice.reciprocal_lattice
    cartesian = reciprocal.get_cartesian_coords(points)
    distance = np.zeros(len(points))
    for start, end in branches:
        if start > 0:
            distance[start] = distance[start - 1]
        distance[start + 1:end + 1] = distance[start] + np.cumsum(
            np.linalg.norm(np.diff(cartesian[start:end + 1], axis=0), axis=1))
    if distance[-1] <= 0:
        raise AIDBError("能带 k 路径长度为零", "invalid_output")
    return {
        "energy_unit": "eV",
        "distance_unit": "1/Angstrom",
        "fermi_energy_ev": fermi_ev,
        "nkpoints": len(points),
        "nbands": nbands,
        "kpoints_reciprocal": points.tolist(),
        "weights": weights.tolist(),
        "labels": labels,
        "segments": [list(branch) for branch in branches],
        "distance": distance.tolist(),
        "spins": {spin.name: {"energies_ev": array[:, :, 0].tolist(), "occupations": array[:, :, 1].tolist()}
                  for spin, array in bands.items()},
    }


def write_band_files(folder, band, source):
    from matplotlib import rc_context
    from matplotlib.backends.backend_svg import FigureCanvasSVG
    from matplotlib.figure import Figure

    distance = np.asarray(band["distance"])
    fermi = band["fermi_energy_ev"]
    fig = Figure(figsize=(8, 5), layout="constrained")
    FigureCanvasSVG(fig)
    axes = fig.add_subplot()
    for spin, values in band["spins"].items():
        energies = np.asarray(values["energies_ev"]) - fermi
        color = {"up": "#1f4b99", "down": "#bd4838"}[spin]
        for start, end in band["segments"]:
            segment = slice(start, end + 1)
            axes.plot(distance[segment], energies[segment], color=color, linewidth=0.8)
    ticks = {}
    for x, label in zip(distance, band["labels"]):
        if label:
            name = "Γ" if label == r"\Gamma" else label
            ticks.setdefault(float(x), {})[name] = None
    if ticks:
        axes.set_xticks(list(ticks), [" / ".join(names) for names in ticks.values()])
        axes.grid(axis="x", color="#d4dae5", linewidth=0.7)
    axes.axhline(0, color="#454b55", linestyle="--", linewidth=0.8)
    axes.set(xlim=(0, distance[-1]), xlabel="k path", ylabel="Energy relative to Fermi (eV)")
    if len(band["spins"]) > 1:
        axes.plot([], [], color="#1f4b99", label="Spin up")
        axes.plot([], [], color="#bd4838", label="Spin down")
        axes.legend()
    with TemporaryDirectory(prefix=".bands-", dir=folder) as temporary:
        from pathlib import Path

        staged = Path(temporary)
        (staged / BAND_FILES[0]).write_text(json.dumps({**band, "source": source}, allow_nan=False), encoding="utf-8")
        with rc_context({"svg.hashsalt": "matcreator-vasp-bands", "text.usetex": False}):
            fig.savefig(staged / BAND_FILES[1], format="svg", metadata={"Date": None})
        for name in BAND_FILES:
            (staged / name).replace(folder / name)
