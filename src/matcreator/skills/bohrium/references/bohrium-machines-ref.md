# Bohrium Machine Types Reference

Bohrium offers CPU and GPU machines. 

> Note: **Always check current availability with `bohr node list`** — pricing and inventory may change!

### CPU Machines

Format: `c{cores}_m{memory_size_in_GB}_cpu`
(some have `_H` suffix for high-performance, but `_H` machines are typically not recommended.)

| Range  | Memory_GB / Cores | Examples      | Price (CNY/h) |
|--------|-------------------|---------------|---------------|
| 2C    | 1, 2, 4, 8        | c2_m2_cpu     | 0.16-0.20 |
| 4C    | 1, 2, 8           | c4_m4_cpu         | 0.32-0.40 |
| 8C    | 1, 2, 4, 8        | c8_m8_cpu         | 0.64-0.80 |
| 12C   | 1, 2, 4, 8        | c12_m12_cpu       | 0.96-1.20 |
| 16C   | 1, 2, 4, 8        | c16_m16_cpu       | 1.28-1.60 |
| 24C   | 1, 2, 4, 8        | c24_m24_cpu       | 1.92-2.40 |
| 32C   | 1, 2, 4, 8        | c32_m32_cpu       | 2.56-3.20 |
| 48C   | 4                 | c48_m176_cpu      | 3.84-4.80 |
| 56C   | 4                 | c56_m160_cpu      | 4.48 |
| 64C   | 1, 2, 4, 8        | c64_m64_cpu       | 5.12-7.68 |
| 96C   | 2, 4              | c96_m192_cpu      | 9.60-11.52 |
| 128C  | 2, 4              | c128_m512_cpu     | 12.80 |

Check current machines:
```bash
bohr node list
```

- For most first-principle jobs under 500 atoms * K-points,
  32C machines are recommended. Scale core count with Number of atoms * K-points.
- Memory to core ratio = 4 GB/core recommended.
- For lighter jobs involves pre-processing or post-processing, 4C or 8C machines are sufficient.
- Increase memory size when encountering memory overflow.

### GPU Machines

Format: `c{cores}_m{memory_size_in_GB}_{gpu_count} * {GPU_MODEL}` or `{gpu_count} * {GPU_MODEL}_{vram}g` (GPU-only)

Available GPU types:

| GPU | VRAM | Price Range (CNY/h) | Configs |
|-----|------|---------------------|---------|
| NVIDIA T4 | 16GB | 2.5-12.0 | 10 |
| NVIDIA V100 | 16/32GB | 4.5-36.0 | 18 |
| NVIDIA A100 | 40/80GB | 10.0-80.0 | 4 |
| NVIDIA 3090 | 24GB | 4.5-36.0 | 4 |
| NVIDIA 4090 | 24GB | 5.5-44.0 | 5 |
| NVIDIA 5090 | 32GB | 1.9 | 1 |
| NVIDIA L4 | 24GB | 5.0-20.0 | 3 |
| NVIDIA L20 | 48GB | 8.0-64.0 | 4 |
| NVIDIA P100 | 16GB | 4.0-32.0 | 4 |
| DCU | 16GB | 1.2-6.0 | 8 |
| FPGA | - | 8.0 | 2 |

> Note: **GPU-only vs CPU+GPU**: Entries like `1 * NVIDIA V100_32g` are GPU-only (no CPU/RAM).
> Entries like `c12_m64_1 * NVIDIA L4` bundle CPU+RAM+GPU. Choose based on your workload's CPU needs.

- For light-weight jobs such as structure-frames selection and very small-scaled MD, `1 * NVIDIA V100_32g` is usually sufficient.
- For most machine-learned force field (MLFF) training and inference-related jobs,
   preference order is: `1 * NVIDIA L20_48g` > `1 * NVIDIA V100_32g` > `1 * NVIDIA 4090_24g` > `c6_m64_1 * NVIDIA 3090`.
- For first-principle jobs, CPU machines should be default. However, to use GPU-acceleration for first-principle jobs,
   `1 * NVIDIA V100_32g` as it offers the best price-performance ratio at FP64 compute, do not use A100 unless you are
   sure about your job's scale.
- [2026/07/15] 4090 and 3090 resources, though cost-effective, are often limited and may require extremely long waiting for now,
   not recommended unless you are sure about your job's scale and can tolerate long waiting time.