# Supported deepmd models reference

This reference file discusses currently supported DP model types and their usage guidelines.

## Supported DP model types

| Model type | Descriptor type  | Model version     | Model variant | Pretrained model file  |
|------------|------------------|-------------------|---------------|------------------------|
| DPA-1      | se_atten_v2      | MatPES            | small         | DPA1-MatPES-s.pt       |
| DPA-1      | se_atten_v2      | MatPES            | medium        | DPA1-MatPES-m.pt       |
| DPA-1      | se_atten_v2      | MatPES            | large         | DPA1-MatPES-l.pt       |
| DPA-2      | dpa2             | 2.3.1-v3.0.0rc0   | None          | DPA-2.3.1-v3.0.0rc0.pt |
| DPA-3      | dpa3             | 3.1-3M            | None          | DPA-3.1-3M.pt          |
| DPA-4      | SeZM (or "dpa4") | Omat24, v20260704 | Air           | DPA4-omat24-Air.pt     |
| DPA-4      | SeZM (or "dpa4") | Omat24, v20260704 | Neo           | DPA4-omat24-Neo.pt     |
| DPA-4      | SeZM (or "dpa4") | Omat24, v20260704 | Mini          | DPA4-omat24-Mini.pt    |
| DPA-4      | SeZM (or "dpa4") | Omat24, v20260704 | Nano          | DPA4-omat24-Nano.pt    |
| DPA-4c     | dpa4c            | OMat24, v20260819 | Air           | DPA4C-Air-OMat24-v20260819.pt  |
| DPA-4c     | dpa4c            | OMat24, v20260819 | Neo           | DPA4C-Neo-OMat24-v20260819.pt  |
| DPA-4c     | dpa4c            | OMat24, v20260819 | Mini          | DPA4C-Mini-OMat24-v20260819.pt |
| DPA-4c     | dpa4c            | OMat24, v20260819 | Nano          | DPA4C-Nano-OMat24-v20260819.pt |
| DPA-4c     | dpa4c            | OMat24, v20260819 | Plus          | DPA4C-Plus-OMat24-v20260819.pt |

> DPA-4c is a **fine-tuning-only** architecture: it is always trained (distilled) from a
> pretrained DPA-4c checkpoint and its CLI usage differs from all other models — see the
> "DPA-4c" section at the end of this file.

> The oldest DP descriptors such as se_e2_a, se_e2_r, and se_e3 are no longer supported due
> to lack of efficiency. Here, we actually use se_atten_v2 with attn_layer=0, yielding virtually 
> the same architecture as conventional DP descriptors.

## Model heads (also called model branches)

The DPA-2 and DPA-3 model are trained with multiple heads, corresponding to multiple domains of research.
The heads can be chosen with their specific names in `dp` CLI.

> DPA-1 and DPA-4 models are trained with no head, so do not add any head name in `dp` CLI.

> When unsure about which model head to use, you can always use the `MP_traj_v024_alldata_mixu` head for DPA-2 and
> the `Omat24` head for DPA-3 as the cover a wide range of materials.

### DPA-2 heads
| Head name                 | Domain of research                                                                      | First-principles software |
|---------------------------|-----------------------------------------------------------------------------------------|---------------------------|
| MP_traj_v024_alldata_mixu | Materials Project trajectories                                                          | VASP                      |
| Domains_Alloy             | Alloys formed by 53 typical metallic elements                                           | ABACUS                    |                         
| Domains_SemiCond          | 20 semiconductors spanning from group IIB to VIA                                        | ABACUS                    |                         
| Domains_Anode             | O3-type layered oxide cathodes (NOT anodes) employed in Li and Na-ion batteries         | VASP                      |                         
| Domains_Cluster           | Metal nano-clusters                                                                     | CP2K                      |
| Domains_Drug              | Small drug molecules procured from the ChEMBL database                                  | Gaussian                  |  
| Domains_FerroEle          | 26 ABO3-type perovskite oxides                                                          | Abacus                    |                      
| Domains_OC2M              | Open Catalyst Project’s OC20 dataset, with various adsorptions on surfaces              | Unknown                   |
| Domains_SSE-PBE           | Solid-state electrolyte                                                                 | VASP                      |
| H2O_H2O-PD                | Water/ice, 0 to 2400 K and 0 to 50 GPa, SCAN functional                                 | VASP                      |
| Metals_AgAu-PBE           | Ag, Au and AgAu configurations                                                          | VASP                      |
| Metals_AlMgCu             | Unitary, binary, and ternary alloys of Al, Cu, and Mg                                   | VASP                      |
| Domains_ANI               | Conformations of organic molecules with up to 13 heavy atoms from GDB-11 molecules      | Unknown                   |
| Domains_Transition1x      | Organic small molecules reactant-product pairs, configurations on reaction trajectories | Unknown                   |


### DPA-3 heads
The DPA-3 model has all heads in DPA-2 except `Domains_OC2M` and `Domains_Ani`. Besides, it has the following additional heads:

| Head name                  | Domain of research                                                                  | First-principles software |
|----------------------------|-------------------------------------------------------------------------------------|---------------------------|
| Omat24                     | Meta's gigantic materials database covering nearly all types of materials           | VASP                      |
| Alloy_tongqi               | Upgraded version of Domains_Alloy                                                   | VASP                      |
| SPICE2                     | SPICE v2 dataset of small molecules                                                 | Unknown                   |
| Alex2D                     | Novel two-dimensional materials                                                     | VASP                      |
| OC20M                      | A subset of OC20 dataset                                                            | Unknown                   |
| ODAC23                     | Metal-organic frameworks interacting with CO2 and H2O                               | VASP                      |
| OC22                       | OC22 dataset of catalysis, significant upgrade from OC20                            | VASP                      |
| solvated_protein_fragments | Protein fragment “amons” (hydrogen-saturated covalently bonded fragments)           | Unknown                   |
| Organic_Reactions          | Organic reaction paths involving C, H, O, and N, computed with GFN2-xTB             | Unknown                   |
| SSE_ABACUS                 | Solid-state electrolyte, at PBE-sol level, wider element coverage than `Domain_SSE` | ABACUS                    |
| Domains_SSE_PBESol         | Same domain as `Domains_SSE`, but with PBESol functional                            | VASP                      |
| Electrolyte                | Liquid electrolyte in Li-ion batteries, PBE-D3                                      | CP2K                      |
| Hybrid_Perovskite          | Organic-inorganic hybrid perovskites, PBE-D3                                        | Unknown                   |

## Model acquisition

When using pretrained models, you should acquire the corresponding model file using the following order:
1. Try searching under user specified directory as specified in the environment variable `MODELS_PATH`;
2. If not found, try searching under the following default directories `/opt/models`, `~/.matcreator/models` and `~/.models`;
3. Report a warning to the users, notify them to set the environment variable `MODELS_PATH` to the directory
   where the models are stored; Then download the model file using the direct download links below.

    Models are available on AIS Square (direct download links, no authentication required):

    | Model file                    | Download URL                                                                                                  |
    |-------------------------------|---------------------------------------------------------------------------------------------------------------|
    | DPA1-L0-S-MatPES-v20260714.pt | https://store.aissquare.com/models/859248d2-156d-46ee-8161-fee1d7c160b3/DPA1-L0-S-MatPES-v20260714.pt         |
    | DPA1-L0-M-MatPES-v20260714.pt | https://store.aissquare.com/models/859248d2-156d-46ee-8161-fee1d7c160b3/DPA1-L0-M-MatPES-v20260714.pt         |
    | DPA1-L0-L-MatPES-v20260714.pt | https://store.aissquare.com/models/859248d2-156d-46ee-8161-fee1d7c160b3/DPA1-L0-L-MatPES-v20260714.pt         |
    | DPA2_medium_28_10M_rc0.pt     | https://store.aissquare.com/models/41d1cfb7-1a98-42a2-90a8-e6257db431ea/DPA2_medium_28_10M_rc0.pt             |
    | DPA-3.1-3M.pt                 | https://store.aissquare.com/models/35b4ce45-4f59-4868-9fd7-a0c0f5ad9464/DPA-3.1-3M.pt                         |
    | DPA4-Air-OMat24-v20260704.pt  | https://store.aissquare.com/models/9293690b-6758-425b-ac8c-74a6cb53235a/DPA4-Air-OMat24-v20260704.pt          |
    | DPA4-Neo-OMat24-v20260704.pt  | https://store.aissquare.com/models/9293690b-6758-425b-ac8c-74a6cb53235a/DPA4-Neo-OMat24-v20260704.pt          |
    | DPA4-Mini-OMat24-v20260704.pt | https://store.aissquare.com/models/9293690b-6758-425b-ac8c-74a6cb53235a/DPA4-Mini-OMat24-v20260704.pt         |
    | DPA4-Nano-OMat24-v20260704.pt | https://store.aissquare.com/models/9293690b-6758-425b-ac8c-74a6cb53235a/DPA4-Nano-OMat24-v20260704.pt         |
    | DPA4C-Air-OMat24-v20260819.pt  | https://store.aissquare.com/models/220858e4-a519-47f5-b167-756b0f4d91f2/DPA4C-Air-OMat24-v20260819.pt   |
    | DPA4C-Neo-OMat24-v20260819.pt  | https://store.aissquare.com/models/220858e4-a519-47f5-b167-756b0f4d91f2/DPA4C-Neo-OMat24-v20260819.pt   |
    | DPA4C-Mini-OMat24-v20260819.pt | https://store.aissquare.com/models/220858e4-a519-47f5-b167-756b0f4d91f2/DPA4C-Mini-OMat24-v20260819.pt  |
    | DPA4C-Nano-OMat24-v20260819.pt | https://store.aissquare.com/models/220858e4-a519-47f5-b167-756b0f4d91f2/DPA4C-Nano-OMat24-v20260819.pt  |
    | DPA4C-Plus-OMat24-v20260819.pt | https://store.aissquare.com/models/220858e4-a519-47f5-b167-756b0f4d91f2/DPA4C-Plus-OMat24-v20260819.pt  |

    > Each DPA-4c model also ships an official training input `DPA4C-<Variant>-OMat24-v20260819.json`
    > at the same URL path (replace the `.pt` suffix with `.json`).

   > When downloading is required, try downloading to `/opt/models` then `~/.matcreator/models`,
   > and rename the model file as specified in the table above, for quick future reference.


## Model choice guidelines

| Task type                                                                   | Most recommended model               | Other recommended models                                                                                          | Prohibited models                                         |
|-----------------------------------------------------------------------------|--------------------------------------|-------------------------------------------------------------------------------------------------------------------|-----------------------------------------------------------|
| Zero-shot inference with the pretrained model (lammps, ase, dp test, etc, < 50k atoms; no dataset needed, ready to use) | DPA-4, Neo | DPA-4 Air (higher accuracy); DPA-4, Mini (higher efficiency) | DPA-1 (low accuracy) |
| Fine-tuning for a specific chemical system (higher accuracy on the target system, requires labeled data) | DPA-4, Neo | DPA-4 Air (higher accuracy); DPA-4, Mini (higher efficiency) | DPA-1 (low accuracy) |
| Large-scale simulation (≳ 50k atoms)                                        | DPA-4c, Neo (fine-tuned)             | DPA-4c Plus / Air (higher accuracy); DPA-4c Mini / Nano (higher efficiency) — switch only on user request         | DPA-1 (low accuracy), DPA-2, DPA-3, DPA-4 (low efficiency) |

DPA-2 and DPA-3 models are supported but not strongly recommended in any scenario, as DPA-4 models are both more efficient and accurate.

> **DPA-4c variant policy:** **Neo is the default variant** whenever a DPA-4c model is
> fine-tuned for large-scale simulation. Switch to another variant (Plus / Air for higher
> accuracy, Mini / Nano for higher efficiency) only when the user explicitly requests it.


## [For bohrium submission] Bohrium image and machine choice guidelines

1. Always prefer the bohrium image and machine as specified by the user in environment variables `BOHRIUM_DPA_IMAGE` and
   `BOHRIUM_DPA_MACHINE`.
2. If not specified, a GPU machine must be used for running any DPA model-related jobs. You can check available GPU machines
   and details by referring to the `bohrium` skill's documents, 
   at [../bohrium/references/bohrium-machines-ref.md](../bohrium/references/bohrium-machines-ref.md).
3. GPUs and their supported images:

    cu126 image: `registry.dp.tech/dptech/dp/native/hub/custom_images/dpa4:20260712cu126-1783827000`
    cu131 image: `registry.dp.tech/dptech/dp/native/hub/custom_images/dpa4:20260704cu131-1783152120`
    
    | GPU  | Architecture | Compute capability | Image                      |
    |------|--------------|--------------------|----------------------------|
    | V100 | Volta        | sm_70              | cu126 image only           |
    | T4   | Tesla        | sm_75              | cu126 image or cu131 image |
    | A100 | Ampere       | sm_89              | cu126 image or cu131 image |
    | L20  | Ampere       | sm_89              | cu126 image or cu131 image |
    | 3090 | Ampere       | sm_89              | cu126 image or cu131 image |
    | 4090 | Ada          | sm_89              | cu126 image or cu131 image |
    | 5090 | Blackwell    | sm_120             | cu131 image only           |
    
    > Image with newer cuda does not always mean better performance and stability. When multiple images are supported, the
    > one with older cuda is recommended.

4.  Never use any non-nvidia GPUs for now as they are poorly supported by deepmd-kit.
5.  Also, do not use nvidia GPUs older than V100 as they no longer support the triton AOT induction route of
    modern pytorch, which is compulsory for deepmd-kit>=3.2.0.

## DPA-4c ("dpa4c" descriptor, only used during fine-tuning)

DPA-4c is the architecture for fine-tuning (distilling) from a fine-tuned model.
Its CLI usage differs from the fine-tuning flow documented elsewhere in this skill.
**The input.json preparation is integrated into `deepmd_prepare.py`**: run `prepare-finetune`
with `--model_name dpa4c` and `--model_variant` one of `air` / `neo` / `mini` / `nano` / `plus`,
pointing `--input_model_path` to the matching official checkpoint
(`DPA4C-<Variant>-OMat24-v20260819.pt`, downloadable from AIS Square — see Model acquisition).
The script embeds the official per-variant parameters and prints the exact execution
command — do NOT write the DPA-4c input.json by hand.

**`--finetune` is STRICTLY PROHIBITED for DPA-4c; always train from scratch with `--pt-expt`:**

```bash
dp --pt-expt train input.json --init-model <pretrain.pt> --skip-neighbor-stat \
   --use-pretrain-script --output output.json
```

- **NEVER use `--finetune` with DPA-4c.** Its bias-adjustment dense forward pass runs out
  of memory (OOM) for the DPA-4c selection `sel=[999999]`; the job will crash. Always use
  `--pt-expt ... --init-model ... --skip-neighbor-stat` to train from scratch instead.
- **Every DPA-4c CLI backend must use `--pt-expt`, never `--pt`.** The standard `--pt`
  (PyTorch) backend produces wrong forces + virials/pressure and crashes on the DPA-4c
  `sel=[999999]` selection. This applies to `train`, `freeze`, `compress`, and `test`
  alike. Only regular DPA-4 (and other DPA models) keep `--pt`.
- `<pretrain.pt>` is the DPA-4c pretrained checkpoint used to initialize the DPA-4c:
  the official `DPA4C-<Variant>-OMat24-v20260819.pt` from AIS Square (historically
  `dpa4c_pretrain_rmse_epoch.pt`); `--skip-neighbor-stat` must be appended.

**Official per-variant inputs:** each DPA-4c checkpoint is published together with its
training input `DPA4C-<Variant>-OMat24-v20260819.json` (see Model acquisition).
Per the official usage note, the released checkpoints serve as **pretrained initializations**:
start from the corresponding released input file, keep the entire **model section unchanged**
(descriptor, fitting net, and the full-periodic-table type_map the type embeddings are
indexed by), replace only the training/validation data, and use a **small learning rate**
for fine-tuning (e.g. `start_lr = 1e-4`). `deepmd_prepare.py` follows this guidance — the
embedded per-variant templates keep the official model section and differ from the released
inputs only in:
- `start_lr` is lowered to **1e-4** for downstream fine-tuning (the released inputs' larger
  lr values were for the original training runs);
- `batch_size`: the official `mix:N` rule is not supported by the deepmd build used here,
  so the closest supported rule `max:N` (batch_size × natoms ≤ N) is used instead;
- default `num_epochs` is **50** (the released inputs' 12 targets the original training
  runs; the MLFF workflow keeps 50 for fine-tuning, overridable via `--epochs`).

Two extra safeguards are added to the training command:
- `--use-pretrain-script`: the model section is taken from the checkpoint's stored script
  instead of the embedded template, preventing mismatches with future DPA-4c releases;
- `--output output.json`: saves the normalized training parameters actually used after
  automatic parameter filling — **keep this file as a record of the run**.

DPA-4c input parameters are **exclusive to DPA-4c — do NOT use them for any other
architecture** (dpa2, dpa3, dpa4/SeZM, se_atten_v2, ...).

**[For bohrium submission] Image and machine:** recommended image
`registry.dp.tech/dptech/dpa-calculator:dpa4-mlip-340e01f9` on a **5090** GPU machine.
This image is specific to DPA-4c fine-tuning; all other DPA models keep the images
given in the previous section.