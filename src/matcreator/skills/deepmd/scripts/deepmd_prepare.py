#!/usr/bin/env python3
"""deepmd_prepare.py — Preparation-stage script for DeePMD-kit training jobs.

Converts raw structure data (xyz / extxyz / POSCAR / …) into deepmd/npy format
and writes an input.json script for ``dp train``.

Sub-commands
------------
  prepare-finetune            Single-task finetune of a pretrained DPA model
  prepare-test                Prepare data for dp test command.

After running any sub-command the ``workdir`` directory should contain:

[common (for all subcommands)]
    <model_name>.pt             Symlink (or copy) to the base model (finetune only)

[prepare-finetune]
    input.json                  Training configuration consumed by ``dp [--pt] train``
    train_data/                 Training data split in deepmd formatted folders.
    test_data/                  Testing data split in deepmd formatted folders.
                                (optional, only when number of available frames is more than max_train_frames)

[prepare-test]
    dpdata/                     Testing data in deepmd formated folders.
"""
import ast
import argparse
from copy import deepcopy
import json
import logging
import random
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from ase.atoms import Atoms
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
# Configuration templates (DPA-1/DPA-2/DPA-3/DPA-4)
# ---------------------------------------------------------------------------

## DPA-1 template and variants.
_DPA1_VARIANTS: Dict[str, Any] = {
    # Small model specs.
    "small": {
        "model": {
            "descriptor": {
                "neuron": [16, 16, 16],
            },
            "fitting_net": {
                "neuron": [64, 64, 64],
            },
        },
        "learning_rate": {
            "start_lr": 0.007,
        },
        "training": {
            "num_epoch": 500,
        },
    },
    # Medium model specs.
    "medium": {
        "model": {
            "descriptor": {
                "neuron": [32, 32, 32],
            },
            "fitting_net": {
                "neuron": [128, 128, 128],
            },
        },
        "learning_rate": {
            "start_lr": 0.005,
        },
        "training": {
            "num_epoch": 500,
        },
    },
    # Medium model specs.
    "large": {
        "model": {
            "descriptor": {
                "neuron": [64, 64, 64],
            },
            "fitting_net": {
                "neuron": [256, 256, 256],
            },
        },
        "learning_rate": {
            "start_lr": 0.003,
        },
        "training": {
            "num_epoch": 300,
        },
    },
}

_DPA1_TEMPLATE: Dict[str, Any] = {
    "model": {
        "descriptor": {
            "type": "se_atten_v2",
            "sel": 181,
            "rcut_smth": 0.5,
            "rcut": 6.0,
            "type_one_side": False,
            "resnet_dt": False,
            "axis_neuron": 16,
            "attn_layer": 0,
            "activation_function": "silu",
            "precision": "float32",
            "seed": 42,  # Fixed seed for reproducibility.
        },
        "fitting_net": {
            "seed": 42,
            "resnet_dt": True,
            "activation_function": "silu",
            "precision": "float32",
        },
    },
    "learning_rate": {
        "type": "wsd",
        "stop_lr": 1e-06,
        "warmup_ratio": 0.003,
        "warmup_start_factor": 0.2,
        "decay_phase_ratio": 0.65,
        "decay_type": "cosine"
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
        "limit_pref_v": 5
      },
    "optimizer": {
        "type": "HybridMuon",
        "weight_decay": 0.001
    },
    "training": {
        "training_data": {
            "systems": [],
            "batch_size": "filter:3000",
        },
        "num_epoch": 500,  # Starting from dp-kit 3.2.0, num_epoch now supported.
        "gradient_max_norm": 5,
        "save_freq": 1000,
        "save_dir": "models",
        "max_ckpt_keep": 3,
        "enable_ema": True,
        "ema_decay": 0.999,
        "ema_ckpt_keep": 3,
        "disp_file": "lcurve.out",
        "disp_freq": 500,
        "disp_avg": True,
        "disp_training": True,
        "time_training": True,
        "tensorboard": False,
        "enable_profiler": False,
        "tensorboard_freq": 1000,
        "profiling": False,
        "zero_stage": 1,
        "seed": 42
    },
}

# DPA2 template (no variant).
_DPA2_TEMPLATE: Dict[str, Any] = {
    "model": {
        "descriptor": {
            "type": "dpa2",
            "repinit": {
                "tebd_dim": 8,
                "rcut": 6.0,
                "rcut_smth": 0.5,
                "nsel": 120,
                "neuron": [
                    25,
                    50,
                    100
                ],
                "axis_neuron": 12,
                "activation_function": "tanh",
                "three_body_sel": 40,
                "three_body_rcut": 4.0,
                "three_body_rcut_smth": 3.5,
                "use_three_body": True,
            },
            "repformer": {
                "rcut": 4.0,
                "rcut_smth": 3.5,
                "nsel": 40,
                "nlayers": 6,
                "g1_dim": 128,
                "g2_dim": 32,
                "attn2_hidden": 32,
                "attn2_nhead": 4,
                "attn1_hidden": 128,
                "attn1_nhead": 4,
                "axis_neuron": 4,
                "update_h2": False,
                "update_g1_has_conv": True,
                "update_g1_has_grrg": True,
                "update_g1_has_drrd": True,
                "update_g1_has_attn": False,
                "update_g2_has_g1g1": False,
                "update_g2_has_attn": True,
                "update_style": "res_residual",
                "update_residual": 0.01,
                "update_residual_init": "norm",
                "attn2_has_gate": True,
                "use_sqrt_nnei": True,
                "g1_out_conv": True,
                "g1_out_mlp": True
            },
            "add_tebd_to_repinit_out": False
        },
        "fitting_net": {
            "neuron": [
                240,
                240,
                240
            ],
            "resnet_dt": True,
            "seed": 1,

        },
    },
    "learning_rate": {
        "type": "exp",
        "decay_steps": 1000,
        "start_lr": 0.0002,
        "stop_lr": 3.51e-08,
    },
    "loss": {
        "type": "ener",
        "start_pref_e": 0.02,
        "limit_pref_e": 1,
        "start_pref_f": 1000,
        "limit_pref_f": 1,
        "start_pref_v": 0.001,
        "limit_pref_v": 0.1,
    },
    "training": {
        "training_data": {
            "systems": [],
            "batch_size": "auto",
        },
        "num_epoch": 120,   # DPA-2 typically requires this much at fine-tuning.
        "warmup_steps": 0,
        "gradient_max_norm": 1.0,  # Suggest strict gradient-clipping for stability.
        "seed": 42,
        "disp_file": "lcurve.out",
        "disp_freq": 200,
        "save_freq": 1000,
    },
}


# DPA-3 template (no variant).
_DPA3_TEMPLATE: Dict[str, Any] = {
    "model": {
        "descriptor": {
            "type": "dpa3",
            "repflow": {
                "n_dim": 128,
                "e_dim": 64,
                "a_dim": 32,
                "nlayers": 16,
                "e_rcut": 6.0,
                "e_rcut_smth": 5.3,
                "e_sel": 1200,
                "a_rcut": 4.0,
                "a_rcut_smth": 3.5,
                "a_sel": 300,
                "axis_neuron": 4,
                "fix_stat_std": 0.3,
                "a_compress_rate": 1,
                "a_compress_e_rate": 2,
                "a_compress_use_split": True,
                "update_angle": True,
                "smooth_edge_update": True,
                "use_dynamic_sel": True,
                "sel_reduce_factor": 10.0,
                "use_exp_switch": True,
                "update_style": "res_residual",
                "update_residual": 0.1,
                "update_residual_init": "const"
            },
            "activation_function": "silut:3.0",
            "use_tebd_bias": False,
            "precision": "float32",
            "concat_output_tebd": False
        },
        "fitting_net": {
            "neuron": [
                240,
                240,
                240
            ],
        "dim_case_embd": 31,
        "resnet_dt": True,
        "precision": "float32",
        "activation_function": "silut:3.0",
        "seed": 42,
        },

    },
    "learning_rate": {
        "type": "exp",
        "decay_steps": 1000,
        "start_lr": 0.001,
        "stop_lr": 3e-05,
    },
    "loss": {
        "type": "ener",
        "start_pref_e": 0.2,
        "limit_pref_e": 20,
        "start_pref_f": 100,
        "limit_pref_f": 60,
        "start_pref_v": 0.02,
        "limit_pref_v": 1,
        "_comment": " that's all"
    },
    "training": {
    "stat_file": "./train.hdf5",
    "training_data": {
        "systems": [],
        "batch_size": "auto:128",
        "_comment": "that's all"
    },
    "num_epoch": 50,
    "warmup_steps": 0,
    "gradient_max_norm": 1.0,  # Lower gradient clip.
    "seed": 10,
    "disp_file": "lcurve.out",
    "disp_freq": 100,
    "save_freq": 2000,
    "_comment": "that's all"
    }
}

# DPA-4 template and variants.
_DPA4_VARIANTS: Dict[str, Any] = {
    # Air model specs.
    "air": {
        "model": {
            "descriptor": {
                "channels": 64,
                "lmax": 3,
                "n_blocks": 3,
                "so2_layers": 4,
                "radial_so2_mode": "degree_channel",
                "radial_so2_rank": 1,
                "n_focus": 1,
            },
        },
    },
    # Neo model specs.
    "neo": {
        "model": {
            "descriptor": {
                "channels": 32,
                "lmax": 3,
                "n_blocks": 2,
                "so2_layers": 3,
                "radial_so2_mode": "degree_channel",
                "radial_so2_rank": 1,
                "n_focus": 2,
            },
        },
    },
    # Mini model specs.
    "mini": {
        "model": {
            "descriptor": {
                "channels": 32,
                "lmax": 2,
                "n_blocks": 2,
                "so2_layers": 3,
                "radial_so2_mode": "degree_channel",
                "radial_so2_rank": 1,
                "n_focus": 1,
            },
        },
    },
    # Nano model specs.
    "nano": {
        "model": {
            "descriptor": {
                "channels": 32,
                "lmax": 1,
                "n_blocks": 1,
                "so2_layers": 3,
                "radial_so2_mode": "none",
                "n_focus": 1,
            },
        },
    },
}

_DPA4_TEMPLATE: Dict[str, Any] = {
    "model": {
        "type": "SeZM",
        "descriptor": {
            "sel": 416,
            "rcut": 6.0,
            "n_radial": 16,
            "use_env_seed": True,
            "mmax": 1,
            "focus_dim": 0,
            "n_atten_head": 1,
            "message_node_so3": True,
            "ffn_neurons": 0,
            "ffn_so3_grid": True,
            "grid_mlp": False,
            "grid_branch": [0, 0, 1],
            "ffn_blocks": 1,
            "so3_readout": "mlp",
            "use_amp": True,  # Should be False on V100.
            "precision": "float32",
            "seed": 42
        },
        "fitting_net": {
            "neuron": [0],
            "precision": "float32",
            "seed": 42
        },
        "use_compile": True,  # Should be False on V100.
        "enable_tf32": True,  # Should be False on V100.
    },
    "learning_rate": {
        "type": "cosine",
        "start_lr": 1e-4,
        "stop_lr": 1e-6,
        "warmup_ratio": 0.003,
        "warmup_start_factor": 0.2,
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
        "limit_pref_v": 5
    },
    "optimizer": {
        "type": "HybridMuon",
        "muon_mode": "slice",
        "magma_muon": True,
        "lr_adjust": 0.0,
        "weight_decay": 0.001
    },
    "training": {
        "stat_file": "./train.hdf5",
        "training_data": {
            "systems": [],
            "batch_size": "auto:128",
            "_comment": "that's all"
        },
        "num_epochs": 30,  # DPA-4 learns very quickly, only very few epochs are needed.
        "gradient_max_norm": 1.0,
        "save_freq": 1000,
        "max_ckpt_keep": 1,
        "enable_ema": False,
        "disp_file": "lcurve.out",
        "disp_freq": 100,
        "disp_avg": True,
        "disp_training": True,
        "time_training": True,
        "seed": 42
    },
}

# Summing up.
_AVAILABLE_MODEL_CFGS = {
    "dpa1": {
        "variants": _DPA1_VARIANTS,
        "template": _DPA1_TEMPLATE,
    },
    "dpa2": {
        "variants": None,
        "template": _DPA2_TEMPLATE,
    },
    "dpa3": {
        "variants": None,
        "template": _DPA3_TEMPLATE,
    },
    "dpa4": {
        "variants": _DPA4_VARIANTS,
        "template": _DPA4_TEMPLATE,
    }
}

_DEFAULT_HEADS = {
    "dpa1": None,
    "dpa2": "MP_traj_v024_alldata_mixu",
    "dpa3": "Omat24",
    "dpa4": None,
}

# ---------------------------------------------------------------------------
# Input files management
# ---------------------------------------------------------------------------

def _load_atoms(paths: List[Path]) -> List[Atoms]:
    """Load all atoms from a given input file."""
    frames: List[Atoms] = []
    for p in paths:
        frames.extend(read(str(p), index=":"))
    logger.info("Loaded %d frames from %d file(s)", len(frames), len(paths))
    return frames


def _export_atoms_to_deepmd_paths(atoms: List[Atoms], out_dir: Path | str, mixed_type: bool=False) -> List[Path]:
    """Export atoms to deepmd/npy format.

    Args:
        atoms: List of ASE atoms.
        out_dir: Output directory of all dpdata systems, each system in a subfolder.
        mixed_type (optional): Whether to use deepmd mixed type format.
            Helps when handling systems with the same number of atoms but different compositions.
            Typically, should not use. Default: False.
    Returns:
        List of paths to the exported dpdata systems.
    """
    if not atoms:
        raise ValueError("No structures to export to deepmd/npy.")

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    ms = dpdata.MultiSystems()
    for a in atoms:
        ms.append(dpdata.LabeledSystem().from_ase_structure(a))
    fmt = "deepmd/npy/mixed" if mixed_type else "deepmd/npy"
    ms.to(fmt, str(out_dir))
    paths = [Path(p).parent for p in out_dir.rglob("type.raw")]
    logger.info(
        "Exported %d system(s) with %d frames to: %s",
        len(paths),
        len(atoms),
        out_dir
    )
    return paths


def _place_model(base_model: Path | str, workdir: Path | str, copy: bool = True) -> Path:
    """Place the base model in the workdir.

    Args:
        base_model: Path to the base model.
        workdir: Path to the workdir.
        copy: Whether to copy the base model or symlink it. Default: True, for stability.
    Returns:
        Path to the base model in the workdir.
    """
    base_model = Path(base_model)
    workdir = Path(workdir)
    src = base_model.expanduser().resolve()
    dest = workdir / src.name
    if dest.exists() or dest.is_symlink():
        dest.unlink()
    if copy:
        shutil.copy2(src, dest)
        logger.info("Copied base model → %s", dest)
    else:
        dest.symlink_to(src)
        logger.info("Symlinked base model → %s", dest)
    return dest

# ---------------------------------------------------------------------------
# Configuration handling
# ---------------------------------------------------------------------------
def _merge_variant_into_template(
        template: Dict[str, Any], variant: Dict[str, Any]
) -> Dict[str, Any]:
    """Recursively merge variant into template."""
    for k, v in variant.items():
        if isinstance(v, dict):
            template[k] = _merge_variant_into_template(template.get(k, {}), v)
        else:
            template[k] = v
    return template

def normalize_name(name: str) -> str:
    """Normalize the name of the model or variant.

    Remove all non-alphanumeric characters and convert to lowercase.
    Args:
        name: Name to normalize.
    Returns:
        Normalized name.
    """
    return re.sub(r"[^a-zA-Z0-9]", "", name).lower()

def _prepare_cfg(model_name: str, variant_name: str | None = None) -> dict[str, Any]:
    """Prepare the configuration for the model."""
    model_name = normalize_name(model_name)
    if variant_name is not None:
        variant_name = normalize_name(variant_name)
    template = _AVAILABLE_MODEL_CFGS[model_name]["template"]
    variants = _AVAILABLE_MODEL_CFGS[model_name]["variants"]
    if variant_name is not None and variant_name != "none" and variants is not None:
        variant = variants[variant_name]
    else:
        variant = {}
    return _merge_variant_into_template(template, variant)



def _randomize_seeds_in_cfg(
    cfg: dict[str, Any],
    rng: np.random.Generator,
) -> tuple[dict[str, Any], dict[str, int]]:
    """
    Recursively randomize all keys named 'seed'.

    Mapping of seeds will be returned as a dictionary with the path to the seed as key,
    then saved for reproducibility.
    Returns:
        randomized config, and mapping from seed path to generated value.
    """
    cfg = deepcopy(cfg)
    seed_map = {}

    def visit(obj, path=""):
        if isinstance(obj, dict):
            for key, value in obj.items():
                current = f"{path}/{key}" if path else key

                if key == "seed":
                    new_seed = int(rng.integers(0, 2**16 - 1))
                    obj[key] = new_seed
                    seed_map[current] = new_seed

                else:
                    visit(value, current)

        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                visit(item, f"{path}/{i}")

    visit(cfg)

    return cfg, seed_map


def _apply_lr_to_cfg(cfg: Dict[str, Any], args) -> None:
    """Interface for modifying learning rates. (optional)

    Command-line options always overwrite the config defaults.
    """
    update_dict = {}
    if args.lr_type is not None:
        update_dict["type"] = args.lr_type
    if args.start_lr is not None:
        update_dict["start_lr"] = args.start_lr
    if args.stop_lr is not None:
        update_dict["stop_lr"] = args.stop_lr
    if args.other_lr_kwargs is not None:
        update_dict.update(args.other_lr_kwargs)
    if update_dict:
        cfg["learning_rate"].update(update_dict)


def _apply_loss_to_cfg(cfg: Dict[str, Any], args) -> None:
    """Interface for modifying loss function. (optional)"""
    update_dict = {}
    if args.loss_type is not None:
        update_dict["type"] = args.loss_type
    if args.loss_func is not None:
        update_dict["loss_func"] = args.loss_func
    if args.start_pref_e is not None:
        update_dict["start_pref_e"] = args.start_pref_e
    if args.limit_pref_e is not None:
        update_dict["limit_pref_e"] = args.limit_pref_e
    if args.start_pref_f is not None:
        update_dict["start_pref_f"] = args.start_pref_f
    if args.limit_pref_f is not None:
        update_dict["limit_pref_f"] = args.limit_pref_f
    if args.start_pref_v is not None:
        update_dict["start_pref_v"] = args.start_pref_v
    if args.limit_pref_v is not None:
        update_dict["limit_pref_v"] = args.limit_pref_v
    if update_dict:
        cfg["loss"].update(update_dict)


def _apply_opt_to_cfg(cfg: Dict[str, Any], args) -> None:
    """Interface for modifying optimizer. (optional)"""
    update_dict = {}
    if args.opt_type is not None:
        update_dict["type"] = args.opt_type
    if args.opt_kwargs is not None:
        update_dict.update(args.opt_kwargs)
    if update_dict:
        cfg["optimizer"].update(update_dict)


def _apply_data_path_to_cfg(
    cfg: Dict[str, Any],
    workdir: Path,
    train_paths: List[Path],
    valid_paths: Optional[List[Path]] = None,
) -> None:
    """Interface for modifying data paths. (must call)"""
    cfg["training"]["training_data"]["systems"] = [
        str(p.relative_to(workdir)) for p in train_paths
    ]
    if valid_paths:
        cfg["training"]["validation_data"] = {
            "systems": [str(p.relative_to(workdir)) for p in valid_paths],
            "batch_size": 1,
        }
    else:
        cfg["training"].pop("validation_data", None)


# ---------------------------------------------------------------------------
# Sub-command implementations
# ---------------------------------------------------------------------------

def cmd_prepare_finetune(args) -> None:
    """Subcommand to prepare a fine-tuning run."""
    if args.seed is None:
        logger.warning("No seed provided. Using random seed.")
        main_seed = int(np.random.randint(0, 2**16))
    else:
        main_seed = args.seed
    rng = np.random.default_rng(main_seed)

    cfg = _prepare_cfg(args.model_name, args.model_variant)

    workdir = Path(args.workdir).resolve()
    workdir.mkdir(parents=True, exist_ok=True)

    atoms = _load_atoms([Path(p) for p in args.data])

    # Split into train / test based on max_train_frames
    max_train = args.max_train_frames
    if 0 < max_train < len(atoms):
        indices = list(range(len(atoms)))
        # These atoms pre-selected by entropy. Therefore, train-test can be splited at random.
        # Should always shuffle.
        random.Random(main_seed).shuffle(indices)
        train_idx = sorted(indices[:max_train])
        test_idx = sorted(indices[max_train:])
        train_atoms = [atoms[i] for i in train_idx]
        test_atoms = [atoms[i] for i in test_idx]
        logger.info("Split → %d train / %d test (max_train_frames=%d)",
                     len(atoms), len(test_atoms), max_train)
    else:
        train_atoms = atoms
        test_atoms = None
        logger.info("No train/test split (all %d frames for training)", len(atoms))

    train_paths = _export_atoms_to_deepmd_paths(train_atoms, workdir / "train_data", args.mixed_type)
    test_paths = (
        _export_atoms_to_deepmd_paths(test_atoms, workdir / "test_data", args.mixed_type)
        if test_atoms else None
    )

    cfg, seed_map = _randomize_seeds_in_cfg(cfg, rng)

    logger.info("Main seed=%d", main_seed)
    for seed_path, seed_value in seed_map.items():
        logger.info("Sub-seed %s=%d", seed_path, seed_value)

    # Set number of epochs. Supported since deepmd 3.2.0. Estimating number of steps no longer meaningful.
    cfg["training"]["num_epoch"] = args.epochs
    logger.info(
        "Epochs=%d, n_train=%d",
        args.epochs,
        len(train_atoms),
    )

    _apply_lr_to_cfg(cfg, args)
    _apply_loss_to_cfg(cfg, args)
    _apply_opt_to_cfg(cfg, args)
    cfg["model"]["type_map"] = ALL_TYPES
    # Use train for training, test for validation during training
    _apply_data_path_to_cfg(cfg, workdir, train_paths, test_paths)

    model_dest = _place_model(args.input_model_path, workdir, not args.no_copy_model)

    # Write input.json.
    workdir = Path(workdir)
    path = workdir / "input.json"
    with open(path, "w") as f:
        json.dump(cfg, f, indent=4)
    logger.info("Wrote input.json to %s", path)

    selected_head = args.head or _DEFAULT_HEADS[args.model_name]
    if selected_head is not None and selected_head.lower() != "none":
        head_section = f"--head {selected_head}"
    else:
        head_section = ""

    exec_cmd = (
        f"dp --pt train input.json "
        f"--finetune {model_dest.name} {head_section} > train_log 2>&1"
    )

    # Freeze, if requested.
    if not args.no_freeze:
        exec_cmd += " && dp --pt freeze -c model.ckpt.pt -o frozen" + " " + head_section

    # Infer with pretrained model for comparison.
    pretrained_model = Path(args.input_model_path).name
    exec_cmd += (
        f" && dp --pt test -m {pretrained_model} -s train_data -d result-train -l log-train"
        + " " + head_section
    )
    # Infer on test data if available.
    if test_paths is not None:
        exec_cmd += (
            f" && dp --pt test -m {pretrained_model} -s test_data -d result-test -l log-test"
            + " " + head_section
        )

    # Infer on training data.
    if not args.no_freeze:
        # Safe to both pth and pt2 suffixes.
        eval_model = "$(find . -maxdepth 1 -name 'frozen.*' -type f | head -n 1)"
    else:
        eval_model = "model.ckpt.pt"

    # Infer with the fine-tuned result.
    exec_cmd += f" && dp --pt test -m {eval_model} -s train_data -d result-train -l log-train"
    # Infer on test data if available.
    if test_paths is not None:
        exec_cmd += f" && dp --pt test -m {eval_model} -s test_data -d result-test -l log-test"

    seeds = {"cli_main_seed": main_seed,}
    seeds.update(seed_map)
    result = {
        "status": "prepared",
        "workdir": str(workdir),
        "input_json": str(workdir / "input.json"),
        "num_epochs": args.epochs,
        "execution_command": exec_cmd,
        "seeds": seeds,
    }
    logger.info("CLI execution summary:\n%s", json.dumps(result, indent=4))


def cmd_prepare_dp_test(args) -> None:
    """Convert ASE-readable frames to deepmd/npy for use with ``dp test``."""
    workdir = Path(args.workdir).resolve()
    atoms = _load_atoms([Path(p) for p in args.data])
    if not atoms:
        logger.error("No frames loaded from provided files.")
        sys.exit(1)

    system_paths = _export_atoms_to_deepmd_paths(atoms, workdir / "dpdata", args.mixed_type)
    model_dest = _place_model(args.input_model_path, workdir, not args.no_copy_model)

    # DP test can be executed in a single command.
    cmd = f"dp --pt test -m {model_dest.name} -s {str(workdir / "dpdata")} -d result-infer -l log-infer"

    normalized_file_name = normalize_name(Path(args.input_model_path).stem)
    inferred_model_name = None
    for model_name in _DEFAULT_HEADS.keys():
        if model_name in normalized_file_name:
            inferred_model_name = model_name
            break
    selected_head = args.head or (_DEFAULT_HEADS[inferred_model_name] if inferred_model_name else None)
    if selected_head is not None and selected_head.lower() != "none":
        head_section = f"--head {selected_head}"
    else:
        head_section = ""
    cmd += head_section

    if args.nframes:
        cmd += f" -n {args.nframes}"

    result = {
        "status": "prepared",
        "workdir": str(workdir),
        "num_frames": len(atoms),
        "system_dirs": [str(p) for p in system_paths],
        "dp_test_command": cmd,
    }
    logger.info("CLI execution summary:\n%s", json.dumps(result, indent=4))


# ---------------------------------------------------------------------------
# Argument parsers
# ---------------------------------------------------------------------------

def _parse_dict_kwargs(kv_pairs):
    if kv_pairs is None:
        return None
    kv_pairs = kv_pairs.strip().split(",")
    result = {}
    for item in kv_pairs:
        key, value = item.split("=", 1)
        result[key.strip()] = ast.literal_eval(value.strip())
    return result

def _add_common_argparse(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--workdir", required=True, metavar="DIR",
        help="Output working directory (required, created if non-existent)",
    )
    p.add_argument(
        "--data", required=True, nargs="+", metavar="FILE",
        help="Input data file(s) (required, must be readable file formats by ASE)",
    )
    p.add_argument(
        "--input_model_path",
        help="The path to the input model file for finetuning or testing (required, must match the model name)",
        type=str,
        required=True
    )
    p.add_argument(
        "--no_copy_model",
        help="Whether not to copy the input model file to the working directory."
             " If not copy, will just create a symbolic link"
             " (default: False, will copy)",
        action="store_true",
    )
    p.add_argument(
        "--mixed_type",
        action="store_true",
        help="Whether to export data in deepmd/npy/mixed format. Default is false."
    )
    p.add_argument(
        "--head",
        help="The head of the model to use. Default is None, will use the default head of the model."
             "DPA-1: no head, DPA-2: MP_traj_v024_alldata_mixu, DPA-3: Omat24, DPA-4: no head.",
        type=str,
        default=None
    )

def _add_finetune_argparse(p: argparse.ArgumentParser) -> None:
    # All defaults should be controlled by cfg templates rather than set here.
    p.add_argument(
        "--model_name",
        help="Model name (required. valid options: dpa1, dpa2, dpa3, dpa4)",
        type=str,
        required=True,
    )
    p.add_argument(
        "--model_variant",
        help="Model variant (required. For models with no variants, use 'none')",
        type=str,
    )
    p.add_argument(
        "--max_train_frames",
        help="Number of training frames. If data less than this, use all to train with no test",
        type=int,
        required=True,
        metavar="NUMTRAIN",
    )
    p.add_argument(
        "--seed",
        help="Random seed (default: None)",
        type=int,
        default=None
    )
    p.add_argument(
        "--model_type",
        help="Model type (default: None)",
        type=str,
        default=None
    )

    p.add_argument(
        "--epochs",
        help="Number of epochs to train (default: None)",
        type=int,
        default=None
    )
    p.add_argument(
        "--seed",
        type=int,
        default=None,
        metavar="SEED",
        help="Random seed for reproducible data splitting. Also generates random seed for training from this seed."
    )
    p.add_argument(
        "--no_freeze",
        action="store_true",
        help="Whether not to freeze the model parameters after fine-tuning."
             " Default is false, will freeze, as frozen models often infer faster."
    )

    # Learning rate.
    p.add_argument(
        "--lr_type",
        help="Learning rate scheduler type (default: None)",
        type=str,
        default=None
    )
    p.add_argument(
        "--start_lr",
        type=float,
        default=None,
        help="Starting learning rate (default: None)"
    )
    p.add_argument(
        "--stop_lr",
        type=float,
        default=None,
        help="Stopping learning rate (default: None)"
    )
    p.add_argument(
        "--other_lr_kwargs",
        type=_parse_dict_kwargs,
        default=None,
        help="Other learning rate kwargs. Use `key=value` format, separated by comma. Values must be readable by ast"
             " (default: None). You may refer to deepmd-kit documentation online for more details."
    )
    # Loss function.
    p.add_argument(
        "--loss_type",
        type=str,
        default=None,
        help="Loss function type (default: None)"
    )
    p.add_argument(
        "--loss_func",
        type=str,
        default=None,
        help="Loss function, for example, 'mae' or 'rmse' (default: None)"
    )
    p.add_argument(
        "--start_pref_e",
        type=float,
        default=None,
        help="Starting energy loss prefactor (default: None)"
    )
    p.add_argument(
        "--limit_pref_e",
        type=float,
        default=None,
        help="Stopping energy loss prefactor (default: None)"
    )
    p.add_argument(
        "--start_pref_f",
        type=float,
        default=None,
        help="Starting force loss prefactor (default: None)"
    )
    p.add_argument(
        "--limit_pref_f",
        type=float,
        default=None,
        help="Stopping force loss prefactor (default: None)"
    )
    p.add_argument(
        "--start_pref_v",
        type=float,
        default=None,
        help="Starting virial loss prefactor (default: None)"
    )
    p.add_argument(
        "--limit_pref_v",
        type=float,
        default=None,
        help="Stopping virial loss prefactor (default: None)"
    )
    # Optimizer.
    p.add_argument(
        "--opt_type",
        type=str,
        default=None,
        help="Optimizer type (default: None)"
    )
    p.add_argument(
        "--opt_kwargs",
        type=_parse_dict_kwargs,
        default=None,
        help="Optimizer kwargs. Use `key=value` format, separated by comma. Values must be readable by ast"
             " (default: None). You may refer to deepmd-kit documentation online for more details."
    )


def _add_test_argparse(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--nframes",
        type=int,
        default=None,
        help="Number of frames to test (default: None)"
)

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="deepmd_prepare.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)

    # ── prepare-finetune ────────────────────────────────────────────
    pf = sub.add_parser(
        "prepare-finetune",
        help="Prepare workdir for single-task DPA finetuning",
    )
    _add_common_argparse(pf)
    _add_finetune_argparse(pf)
    pf.set_defaults(func=cmd_prepare_finetune)

    # ── prepare-test ────────────────────────────────────────────────
    cd = sub.add_parser(
        "prepare-test",
        help="Convert ASE-readable structure files to deepmd/npy for dp test",
    )
    _add_common_argparse(cd)
    _add_test_argparse(cd)
    cd.set_defaults(func=cmd_prepare_dp_test)

    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
