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
| DPA-4      | SeZM (or "dpa4") | Omat24, v20240704 | Air           | DPA4-omat24-Air.pt     |
| DPA-4      | SeZM (or "dpa4") | Omat24, v20240704 | Neo           | DPA4-omat24-Neo.pt     |
| DPA-4      | SeZM (or "dpa4") | Omat24, v20240704 | Mini          | DPA4-omat24-Mini.pt    |
| DPA-4      | SeZM (or "dpa4") | Omat24, v20240704 | Nano          | DPA4-omat24-Nano.pt    |

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
   where the models are stored; Then try visiting the provided webpage, extract the corresponding model download url,
   and download the model file.

    Models are available on the AIS Square website:
    DPA-1 models webpage (S/M/L): https://www.aissquare.com/models/detail?pageType=models&name=DPA1-MatPES&id=429
    DPA-2 model webpage (2.3.1-v3.0.0rc0): https://www.aissquare.com/models/detail?pageType=models&name=DPA-2.3.1-v3.0.0rc0&id=287
    DPA-3 model webpage (3.1-3M): https://www.aissquare.com/models/detail?pageType=models&name=DPA-3.1-3M&id=343
    DPA-4 models webpage (Omat24, v20240704, Air/Neo/Mini/Nano): https://www.aissquare.com/models/detail?pageType=models&name=DPA4-OMat24&id=423

   > When downloading is required, try downloading to `/opt/models` then `~/.matcreator/models`,
   > and rename the model file as specified in the table above, for quick future reference.


## Model choice guidelines

| Task type                                                                   | Most recommended model                          | Other recommended models                                                                                                 | Prohibited models                                        |
|-----------------------------------------------------------------------------|-------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------|----------------------------------------------------------|
| Common materials inference (lammps, ase, dp test, etc, < 50k atoms)         | DPA-4, Neo                                      | DPA-4 Air (higher accuracy); DPA-4, Mini (higher efficiency)                                                             | DPA-1 (low accuracy)                                     |
| Fine-tuning                                                                 | DPA-4, Neo                                      | DPA-4 Air (higher accuracy); DPA-4, Mini (higher efficiency)                                                             | DPA-1 (low accuracy)                                     |
| Distillation teacher model                                                  | DPA-4, Neo                                      | DPA-4 Air (higher accuracy); DPA-4, Mini (higher efficiency)                                                             | DPA-1 (low accuracy)                                     |
| Large-scale materials inference after distillation (~ 50k ~ 200k atoms)     | DPA-1, Medium (distilled from fine-tuned DPA-4) | DPA-1, Large (distilled from fine-tuned DPA-4); DPA-1, Small (distilled from fine-tuned DPA-4); DPA-4, Nano (fine-tuned) | DPA-2, DPA-3, DPA-4 Air/Neo/Mini (low efficiency)        | 
| Extremely large-scale materials inference after distillation (> 200k atoms) | DPA-1, Small (distilled from fine-tuned DPA-4)  | None                                                                                                                     | Other DPA-1 models, DPA-2, DPA-3, DPA-4 (low efficiency) | 

DPA-2 and DPA-3 models are supported but not strongly recommended in any scenario, as DPA-4 models are both more efficient and accurate.


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
