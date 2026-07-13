#!/usr/bin/env python3
"""dpa4_prepare.py — Preparation-phase script for DPA4 (SeZM) finetuning jobs.

Converts raw structure data (xyz / extxyz / POSCAR / …) into deepmd/npy format
and writes an input.json ready for ``dp --pt train`` with version-specific DPA4
configuration.

DPA4-OMat24 provides five model variants: Nano, Mini, Neo, Air, Plus.
Each variant has its own matching parameters — do NOT mix across variants.
The ``--version`` flag selects the variant template (default: neo).

Training steps are computed automatically from ``--epochs`` (default 50) and
the training set size: ``numb_steps = epochs * n_train_frames`` (batch_size=1).

DPA4 jobs run exclusively on the Bohrium platform — there is no local execution.

Sub-commands
------------
  prepare-finetune   Single-task finetune of a pretrained DPA4 model
  convert-data       Convert ASE-readable files to deepmd/npy for ``dp test``

After running any sub-command the ``--workdir`` directory contains:

  input.json                  Training configuration consumed by ``dp --pt train``
  train_data/                 deepmd/npy training split
  test_data/                  deepmd/npy test split (for dp test evaluation)
  <model>                     Copy of the DPA4 pretrained model file

Execution (remote on Bohrium)
-----------------------------
  cd <workdir>

  # Finetune:
  dp --pt train input.json --finetune <model.pt> > train_log 2>&1

  # Freeze:
  dp --pt freeze -c model.ckpt.pt -o frozen

  # Test (frozen model):
  dp --pt test -m frozen.pt2 -s <test_data_dir> -d result-test -l log-test

  # Test (pretrained checkpoint — for zero-shot evaluation):
  dp --pt test -m <model.pt> -s <test_data_dir> -d result-test -l log-test

Data conversion for dp test
----------------------------
  python dpa4_prepare.py convert-data \\
      --data  test.extxyz            \\
      --outdir <out_dir>             \\
      [--mixed_type]                 \\
      [--nframes 100]

  # Then run dp test on each system dir printed in the JSON output:
  dp --pt test -m frozen.pt2 -s <system_dir> [-n <nframes>]
"""

import argparse
import glob
import json
import logging
import random
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from ase.atoms import Atoms
from ase.calculators.singlepoint import SinglePointCalculator
from ase.io import read
import dpdata

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Full periodic-table element list — used as universal type_map
# ---------------------------------------------------------------------------
ALL_TYPES: List[str] = [
    "H", "He", "Li", "Be", "B", "C", "N", "O", "F", "Ne",
    "Na", "Mg", "Al", "Si", "P", "S", "Cl", "Ar",
    "K", "Ca", "Sc", "Ti", "V", "Cr", "Mn", "Fe", "Co", "Ni",
    "Cu", "Zn", "Ga", "Ge", "As", "Se", "Br", "Kr",
    "Rb", "Sr", "Y", "Zr", "Nb", "Mo", "Tc", "Ru", "Rh", "Pd",
    "Ag", "Cd", "In", "Sn", "Sb", "Te", "I", "Xe",
    "Cs", "Ba", "La", "Ce", "Pr", "Nd", "Pm", "Sm", "Eu", "Gd",
    "Tb", "Dy", "Ho", "Er", "Tm", "Yb", "Lu",
    "Hf", "Ta", "W", "Re", "Os", "Ir", "Pt", "Au", "Hg",
    "Tl", "Pb", "Bi", "Po", "At", "Rn",
    "Fr", "Ra", "Ac", "Th", "Pa", "U", "Np", "Pu", "Am", "Cm",
    "Bk", "Cf", "Es", "Fm", "Md", "No", "Lr",
    "Rf", "Db", "Sg", "Bh", "Hs", "Mt", "Ds", "Rg", "Cn",
    "Nh", "Fl", "Mc", "Lv", "Ts", "Og",
]

# ---------------------------------------------------------------------------
# DPA4 version → input.json template mapping
#
# Each model variant has its own matching parameters. Do NOT mix across variants.
# Architecture parameters come from the DPA4-OMat24 release (v20260704).
# The descriptor / loss / optimizer / training sections are shared across all
# variants; only the architecture dimensions and learning_rate differ.
# ---------------------------------------------------------------------------

_VARIANT_PARAMS: Dict[str, Dict[str, Any]] = {
    #  variant       channels  lmax  n_blocks  so2_layers  n_focus  radial_so2_rank  start_lr
    "nano": dict(channels=32, lmax=1, n_blocks=1, so2_layers=3, n_focus=1, radial_so2_rank=1, start_lr=5e-3),
    "mini": dict(channels=32, lmax=2, n_blocks=2, so2_layers=3, n_focus=1, radial_so2_rank=1, start_lr=1e-3),
    "neo":  dict(channels=32, lmax=3, n_blocks=2, so2_layers=3, n_focus=2, radial_so2_rank=1, start_lr=5e-4),
    "air":  dict(channels=64, lmax=3, n_blocks=3, so2_layers=4, n_focus=1, radial_so2_rank=1, start_lr=4e-4),
    "plus": dict(channels=64, lmax=4, n_blocks=4, so2_layers=4, n_focus=1, radial_so2_rank=2, start_lr=3e-4),
}


def _build_template(variant: str) -> Dict[str, Any]:
    vp = _VARIANT_PARAMS[variant]
    return {
        "model": {
            "type": "SeZM",
            "type_map": [
            "H",
            "He",
            "Li",
            "Be",
            "B",
            "C",
            "N",
            "O",
            "F",
            "Ne",
            "Na",
            "Mg",
            "Al",
            "Si",
            "P",
            "S",
            "Cl",
            "Ar",
            "K",
            "Ca",
            "Sc",
            "Ti",
            "V",
            "Cr",
            "Mn",
            "Fe",
            "Co",
            "Ni",
            "Cu",
            "Zn",
            "Ga",
            "Ge",
            "As",
            "Se",
            "Br",
            "Kr",
            "Rb",
            "Sr",
            "Y",
            "Zr",
            "Nb",
            "Mo",
            "Tc",
            "Ru",
            "Rh",
            "Pd",
            "Ag",
            "Cd",
            "In",
            "Sn",
            "Sb",
            "Te",
            "I",
            "Xe",
            "Cs",
            "Ba",
            "La",
            "Ce",
            "Pr",
            "Nd",
            "Pm",
            "Sm",
            "Eu",
            "Gd",
            "Tb",
            "Dy",
            "Ho",
            "Er",
            "Tm",
            "Yb",
            "Lu",
            "Hf",
            "Ta",
            "W",
            "Re",
            "Os",
            "Ir",
            "Pt",
            "Au",
            "Hg",
            "Tl",
            "Pb",
            "Bi",
            "Po",
            "At",
            "Rn",
            "Fr",
            "Ra",
            "Ac",
            "Th",
            "Pa",
            "U",
            "Np",
            "Pu",
            "Am",
            "Cm",
            "Bk",
            "Cf",
            "Es",
            "Fm",
            "Md",
            "No",
            "Lr",
            "Rf",
            "Db",
            "Sg",
            "Bh",
            "Hs",
            "Mt",
            "Ds",
            "Rg",
            "Cn",
            "Nh",
            "Fl",
            "Mc",
            "Lv",
            "Ts",
            "Og"
        ],
            "descriptor": {
                "type": "SeZM",
                "sel": 181,
                "rcut": 6.0,
                "channels": vp["channels"],
                "n_radial": 16,
                "use_env_seed": True,
                "lmax": vp["lmax"],
                "mmax": 1,
                "n_blocks": vp["n_blocks"],
                "so2_layers": vp["so2_layers"],
                "radial_so2_mode": "degree_channel",
                "radial_so2_rank": vp["radial_so2_rank"],
                "n_focus": vp["n_focus"],
                "focus_dim": 0,
                "n_atten_head": 1,
                "message_node_so3": True,
                "ffn_neurons": 0,
                "ffn_so3_grid": True,
                "grid_mlp": False,
                "grid_branch": [0, 0, 1],
                "ffn_blocks": 1,
                "so3_readout": "mlp",
                "use_amp": False,
                "precision": "float32",
                "seed": 42,
            },
            "fitting_net": {
                "neuron": [0],
                "precision": "float32",
                "seed": 42,
            },
            "use_compile": False,
            "enable_tf32": False,
        },
        "learning_rate": {
            "type": "wsd",
            "start_lr": vp["start_lr"],
            "stop_lr": 1e-6,
            "warmup_ratio": 0.003,
            "warmup_start_factor": 0.2,
            "decay_phase_ratio": 0.65,
            "decay_type": "cosine",
        },
        "loss": {
            "type": "ener",
            "loss_func": "mae",
            "f_use_norm": True,
            "start_pref_e": 20,
            "limit_pref_e": 20,
            "start_pref_f": 20,
            "limit_pref_f": 20,
            "start_pref_v": 5,
            "limit_pref_v": 5,
        },
        "optimizer": {
            "type": "HybridMuon",
            "weight_decay": 0.001,
        },
        "training": {
            "training_data": {
                "systems": [],
                "batch_size": 1,
            },
            "numb_steps": 10000,
            "gradient_max_norm": 1.0,
            "save_freq": 1000,
            "max_ckpt_keep": 1,
            "enable_ema": False,
            "disp_file": "lcurve.out",
            "disp_freq": 100,
            "disp_avg": True,
            "disp_training": True,
            "time_training": True,
            "seed": 42,
        },
    }


_DPA4_TEMPLATES: Dict[str, Dict[str, Any]] = {
    v: _build_template(v) for v in _VARIANT_PARAMS
}

AVAILABLE_VERSIONS = list(_DPA4_TEMPLATES.keys())


# ---------------------------------------------------------------------------
# Data utilities
# ---------------------------------------------------------------------------

def _attach_singlepoint(atoms: Atoms) -> Atoms:
    """Attach a SinglePointCalculator from info/arrays if no calc is set.

    ASE's extxyz reader sometimes leaves energy/forces/virial in atoms.info /
    atoms.arrays without a calculator. dpa4_prepare and dpdata both expect a
    calculator (get_potential_energy / get_forces), so normalise here.
    """
    if atoms.calc is not None:
        return atoms
    results: Dict[str, Any] = {}
    if "energy" in atoms.info:
        results["energy"] = atoms.info["energy"]
    if "forces" in atoms.arrays:
        results["forces"] = atoms.arrays["forces"]
    if "virial" in atoms.info:
        results["stress"] = -np.array(atoms.info["virial"]).reshape(3, 3) / max(
            atoms.get_volume(), 1e-12
        )
    if results:
        atoms.calc = SinglePointCalculator(atoms, **results)
    return atoms


def _load_atoms(paths: List[Path]) -> List[Atoms]:
    frames: List[Atoms] = []
    for p in paths:
        frames.extend(read(str(p), index=":"))
    frames = [_attach_singlepoint(a) for a in frames]
    logger.info("Loaded %d frames from %d file(s)", len(frames), len(paths))
    return frames


def _split(
    atoms: List[Atoms],
    ratio: float,
    shuffle: bool,
    seed: Optional[int],
) -> Tuple[List[Atoms], Optional[List[Atoms]]]:
    if ratio <= 0 or len(atoms) <= 1:
        return atoms, None
    ratio = max(0.0, min(1.0, ratio))
    n_valid = min(int(round(len(atoms) * ratio)), len(atoms) - 1)
    if n_valid == 0:
        return atoms, None
    indices = list(range(len(atoms)))
    if shuffle:
        random.Random(seed).shuffle(indices)
    valid_idx = set(indices[:n_valid])
    train = [atoms[i] for i in indices if i not in valid_idx]
    valid = [atoms[i] for i in sorted(valid_idx)]
    logger.info("Split → %d train / %d valid", len(train), len(valid))
    return train, valid


def _get_label(atoms: Atoms, key: str, info_key: str, calc_key: str):
    """Read a DFT label from atoms.info, atoms.arrays, or the attached calculator.

    ASE extxyz files written by different tools store labels inconsistently:
      - energy  -> atoms.info['energy']  OR  calc.results['energy']
      - forces  -> atoms.arrays['forces'] OR  calc.results['forces']
      - virial  -> atoms.info['virial']   OR  calc.results['stress']*(-V)
    SinglePointCalculator-backed frames expose them through get_*() too.
    This helper unifies all sources so any DFT-labelled extxyz is accepted.
    """
    if key in atoms.arrays:
        return atoms.arrays[key]
    if info_key in atoms.info:
        return atoms.info[info_key]
    calc = atoms.calc
    if calc is not None and calc_key in calc.results:
        val = calc.results[calc_key]
        if calc_key == "stress" and "virial" not in atoms.info and "virial" not in atoms.arrays:
            vol = atoms.get_volume()
            val = -np.array(val).reshape(3, 3) * vol if vol > 0 else np.zeros((3, 3))
        return val
    raise ValueError(
        f"Required DFT label '{calc_key}' not found in atoms.info, atoms.arrays, "
        f"or attached calculator. Ensure the structure is DFT-labelled "
        f"(energy + forces + virial)."
    )


def _ase_to_labeled(atoms: Atoms) -> dpdata.LabeledSystem:
    symbols = atoms.get_chemical_symbols()
    names = list(dict.fromkeys(symbols))
    numbs = [symbols.count(n) for n in names]
    types = np.array([names.index(s) for s in symbols], dtype=int)
    energy = float(_get_label(atoms, "energy", "energy", "energy"))
    forces = np.array(_get_label(atoms, "forces", "forces", "forces"), dtype=float)
    virial = np.array(_get_label(atoms, "virial", "virial", "stress"), dtype=float).reshape(3, 3)
    data: Dict[str, Any] = {
        "atom_names": names,
        "atom_numbs": numbs,
        "atom_types": types,
        "cells": np.array([atoms.cell.array]),
        "coords": np.array([atoms.get_positions()]),
        "orig": np.zeros(3),
        "nopbc": not np.any(atoms.get_pbc()),
        "energies": np.array([energy]),
        "forces": np.array([forces]),
        "virial": np.array([virial]),
    }
    return dpdata.LabeledSystem.from_dict({"data": data})


def _export(atoms: List[Atoms], out_dir: Path, mixed_type: bool) -> List[Path]:
    if not atoms:
        raise ValueError("No structures to export to deepmd/npy.")
    out_dir.mkdir(parents=True, exist_ok=True)
    ms = dpdata.MultiSystems()
    for a in atoms:
        ms.append(_ase_to_labeled(a))
    fmt = "deepmd/npy/mixed" if mixed_type else "deepmd/npy"
    ms.to(fmt, str(out_dir))
    paths = [
        Path(p).parent
        for p in glob.glob(str(out_dir) + "/**/type.raw", recursive=True)
    ]
    logger.info("Exported %d system(s) → %s", len(paths), out_dir)
    return paths


def _rand32() -> int:
    return random.randrange(2**32)


def _randomise_seeds(cfg: Dict[str, Any]) -> Dict[str, Any]:
    cfg = json.loads(json.dumps(cfg))
    desc = cfg.get("model", {}).get("descriptor", {})
    desc["seed"] = _rand32()
    fit = cfg.get("model", {}).get("fitting_net", {})
    fit["seed"] = _rand32()
    cfg["training"]["seed"] = _rand32()
    return cfg


def _place_model(base_model: Path, workdir: Path, copy: bool) -> Path:
    """Copy a DPA4 model file into workdir."""
    src = base_model.expanduser().resolve()
    if not src.exists():
        raise ValueError(f"DPA4 base model not found: {src}")
    if src.is_dir():
        raise ValueError(
            f"DPA4 base model must be a file (not a directory), got: {src}"
        )
    dest = workdir / src.name
    if dest.exists() or dest.is_symlink():
        dest.unlink()
    shutil.copy2(src, dest)
    logger.info("Copied base model file → %s", dest)
    return dest


def _apply_lr(cfg: Dict[str, Any], args) -> None:
    lr = cfg["learning_rate"]
    if args.start_lr is not None:
        lr["start_lr"] = args.start_lr
    if args.stop_lr is not None:
        lr["stop_lr"] = args.stop_lr


def _apply_loss(cfg: Dict[str, Any], args) -> None:
    loss = cfg["loss"]
    loss["type"] = args.loss_type
    loss["loss_func"] = args.loss_func
    loss["start_pref_e"] = args.start_pref_e
    loss["limit_pref_e"] = args.limit_pref_e
    loss["start_pref_f"] = args.start_pref_f
    loss["limit_pref_f"] = args.limit_pref_f
    loss["start_pref_v"] = args.start_pref_v
    loss["limit_pref_v"] = args.limit_pref_v


def _set_data(
    cfg: Dict[str, Any],
    train_paths: List[Path],
    valid_paths: Optional[List[Path]],
    workdir: Path,
) -> None:
    cfg["training"]["training_data"]["systems"] = [
        str(p.relative_to(workdir)) for p in train_paths
    ]
    if valid_paths:
        cfg["training"]["validation_data"] = {
            "systems": [str(p.relative_to(workdir)) for p in valid_paths],
            "batch_size": "auto:128",
        }
    else:
        cfg["training"].pop("validation_data", None)


# ---------------------------------------------------------------------------
# Sub-command implementations
# ---------------------------------------------------------------------------

def cmd_prepare_finetune(args) -> None:
    version = args.version
    if version not in _DPA4_TEMPLATES:
        logger.error(
            "Unknown DPA4 version '%s'. Available: %s",
            version, ", ".join(AVAILABLE_VERSIONS),
        )
        sys.exit(1)

    workdir = Path(args.workdir).resolve()
    workdir.mkdir(parents=True, exist_ok=True)

    atoms = _load_atoms([Path(p) for p in args.train_data])

    # Split into train / test based on max_train_frames
    max_train = args.max_train_frames
    if max_train > 0 and len(atoms) > max_train:
        indices = list(range(len(atoms)))
        if not args.no_shuffle:
            random.Random(args.seed).shuffle(indices)
        train_idx = sorted(indices[:max_train])
        test_idx = sorted(indices[max_train:])
        train_atoms = [atoms[i] for i in train_idx]
        test_atoms = [atoms[i] for i in test_idx]
        logger.info("Split → %d train / %d test (max_train_frames=%d)",
                     len(train_atoms), len(test_atoms), max_train)
    else:
        train_atoms = atoms
        test_atoms = None
        logger.info("No train/test split (all %d frames for training)", len(atoms))

    train_paths = _export(train_atoms, workdir / "train_data", args.mixed_type)
    test_paths = (
        _export(test_atoms, workdir / "test_data", args.mixed_type)
        if test_atoms else None
    )

    cfg = _randomise_seeds(_DPA4_TEMPLATES[version])

    # Compute numb_steps from epochs: numb_steps = epochs * n_train
    # (batch_size=1 in template, so each step = 1 frame)
    n_train = len(train_atoms)
    numb_steps = args.epochs * n_train
    cfg["training"]["numb_steps"] = numb_steps
    logger.info("Epochs=%d, n_train=%d → numb_steps=%d", args.epochs, n_train, numb_steps)

    _apply_lr(cfg, args)
    _apply_loss(cfg, args)
    cfg["model"]["type_map"] = ALL_TYPES
    # Use train for training, test for validation during training
    _set_data(cfg, train_paths, test_paths, workdir)

    model_dest = _place_model(Path(args.base_model), workdir, args.copy_model)

    _write_input(workdir, cfg)

    exec_cmd = (
        f"dp --pt train input.json "
        f"--finetune {model_dest.name} > train_log 2>&1"
    )
    exec_cmd += " && dp --pt freeze -c model.ckpt.pt -o frozen"
    has_test = test_paths is not None
    if has_test:
        exec_cmd += " && dp --pt test -m frozen.pt2 -s test_data -d result-test -l log-test"
    _print_result(
        workdir, "dpa4-finetune", exec_cmd, numb_steps,
        model_dest.name, version, has_test, args.epochs,
    )


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def _write_input(workdir: Path, cfg: Dict[str, Any]) -> None:
    path = workdir / "input.json"
    with open(path, "w") as f:
        json.dump(cfg, f, indent=4)
    logger.info("Wrote %s", path)


def _print_result(
    workdir: Path, mode: str, exec_cmd: str, numb_steps: int,
    model_dir_name: str = "", version: str = "", has_test: bool = False,
    num_epochs: int = 0,
) -> None:
    result = {
        "status": "prepared",
        "workdir": str(workdir),
        "mode": mode,
        "input_json": str(workdir / "input.json"),
        "numb_steps": numb_steps,
        "execution_command": exec_cmd,
        "has_test_data": has_test,
    }
    if num_epochs:
        result["num_epochs"] = num_epochs
    if version:
        result["version"] = version
    if model_dir_name:
        result["model_name"] = model_dir_name
    print(json.dumps(result, indent=2))


# ---------------------------------------------------------------------------
# convert-data sub-command
# ---------------------------------------------------------------------------

def cmd_convert_data(args) -> None:
    """Convert ASE-readable frames to deepmd/npy for use with ``dp test``."""
    outdir = Path(args.outdir).resolve()
    atoms = _load_atoms([Path(p) for p in args.data])
    if not atoms:
        logger.error("No frames loaded from provided files.")
        sys.exit(1)

    system_paths = _export(atoms, outdir, args.mixed_type)

    test_cmds = []
    for sp in system_paths:
        cmd = f"dp --pt test -m frozen.pt2 -s {sp}"
        if args.nframes:
            cmd += f" -n {args.nframes}"
        test_cmds.append(cmd)

    result = {
        "status": "converted",
        "outdir": str(outdir),
        "num_frames": len(atoms),
        "system_dirs": [str(p) for p in system_paths],
        "dp_test_commands": test_cmds,
    }
    print(json.dumps(result, indent=2))


# ---------------------------------------------------------------------------
# Argument parsers
# ---------------------------------------------------------------------------

def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--workdir", required=True, metavar="DIR",
        help="Output working directory (created if needed)",
    )
    p.add_argument(
        "--epochs", type=int, default=50, metavar="N",
        help="Target training epochs (default: 50). "
             "numb_steps = epochs * n_train_frames (batch_size=1).",
    )
    # Learning rate — cosine schedule (defaults from variant template)
    p.add_argument("--start_lr", type=float, default=None,
                   help="Starting learning rate (default: from variant template)")
    p.add_argument("--stop_lr", type=float, default=None,
                   help="Stopping learning rate (default: from variant template)")
    # Loss — MAE (shared across all variants)
    p.add_argument("--loss_type", default="ener",
                   help="Loss function type (default: ener)")
    p.add_argument("--loss_func", default="mae",
                   help="Loss function variant (default: mae)")
    p.add_argument("--start_pref_e", type=float, default=20.0)
    p.add_argument("--limit_pref_e", type=float, default=20.0)
    p.add_argument("--start_pref_f", type=float, default=20.0)
    p.add_argument("--limit_pref_f", type=float, default=20.0)
    p.add_argument("--start_pref_v", type=float, default=5.0)
    p.add_argument("--limit_pref_v", type=float, default=5.0)
    # Data split
    p.add_argument(
        "--max_train_frames", type=int, default=0, metavar="N",
        help="Max frames for training (default: 0 = use all). "
             "If set and data has more frames, excess goes to test_data/.",
    )
    p.add_argument(
        "--no_shuffle", action="store_true",
        help="Disable shuffling before train/test split",
    )
    p.add_argument("--seed", type=int, default=None, metavar="N",
                   help="Random seed for reproducible data splitting")
    p.add_argument("--mixed_type", action="store_true",
                   help="Export data in deepmd/npy/mixed format")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dpa4_prepare.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)

    # ── prepare-finetune ────────────────────────────────────────────
    pf = sub.add_parser(
        "prepare-finetune",
        help="Prepare workdir for DPA4 single-task finetuning",
    )
    pf.add_argument(
        "--train_data", nargs="+", required=True, metavar="FILE",
        help="Structure files readable by ASE (xyz, extxyz, POSCAR, …)",
    )
    pf.add_argument(
        "--base_model", required=True, metavar="PATH",
        help="Pretrained DPA4 model file",
    )
    pf.add_argument(
        "--version", default="neo", metavar="VER",
        choices=AVAILABLE_VERSIONS,
        help=(
            f"DPA4 model version — selects the matching input.json template "
            f"(available: {', '.join(AVAILABLE_VERSIONS)}; default: neo). "
            f"Model version and parameters must match exactly."
        ),
    )
    pf.add_argument(
        "--copy_model", action="store_true",
        help="Copy the base model checkpoint into workdir (always done for DPA4)",
    )
    _add_common(pf)
    pf.set_defaults(func=cmd_prepare_finetune)

    # ── convert-data ────────────────────────────────────────────────
    cd = sub.add_parser(
        "convert-data",
        help="Convert ASE-readable structure files to deepmd/npy for dp test",
    )
    cd.add_argument(
        "--data", nargs="+", required=True, metavar="FILE",
        help="Structure files readable by ASE (xyz, extxyz, POSCAR, …)",
    )
    cd.add_argument(
        "--outdir", required=True, metavar="DIR",
        help="Output directory for deepmd/npy system directories",
    )
    cd.add_argument(
        "--mixed_type", action="store_true",
        help="Export in deepmd/npy/mixed format (allows variable composition)",
    )
    cd.add_argument(
        "--nframes", type=int, default=None, metavar="N",
        help="Number of frames to test; embedded in the printed dp test command (optional)",
    )
    cd.set_defaults(func=cmd_convert_data)

    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
