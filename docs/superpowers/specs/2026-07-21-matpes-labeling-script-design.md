# MatPES MLFF 标注输入生成脚本 — 设计

日期：2026-07-21
位置：`src/matcreator/skills/vasp-pymatgen/scripts/prepare_matpes.py`

## 目的

为 MLFF 训练集标注生成 VASP 静态计算输入。以 pymatgen `MatPESStaticSet`（PBE）为基底，通过 `user_incar_settings` 覆盖不必要的默认参数：默认关闭磁性、关闭电荷密度输出、ENCUT 降到 600。脚本负责结构文件 → POSCAR 转换、输入生成和三重校验。

## CLI

```
python prepare_matpes.py STRUCTURE_FILE [-o OUTDIR] [--spin] [--frames START:STOP:STEP]
python prepare_matpes.py --validate-only DIR [DIR ...]
```

- `STRUCTURE_FILE`：ASE 可读的任意格式（cif、POSCAR、extxyz、…）
- 单帧文件 → `OUTDIR/` 单目录；多帧轨迹 → `OUTDIR/frame_0000/`、`frame_0001/`…
- `-o/--output-dir`：默认 `matpes_job`
- `--frames`：Python 切片语法子采样轨迹帧
- `--spin`：ISPIN=2，保留 MatPES 默认 MAGMOM 猜测值
- `--validate-only`:对已有目录只跑校验，不生成

## INCAR 覆盖（user_incar_settings）

| Tag | 值 | 理由 |
|---|---|---|
| ISPIN | 1 | 磁性 opt-in；`--spin` 时为 2 |
| ENCUT | 600 | 精度够用，比默认 680 省时 |
| LCHARG | False | MLFF 只需能量/力/应力 |
| LAECHG | False | 同上，不写 AECCAR |
| LMIXTAU | False | 不用 r2SCAN，无需混合动能密度 |
| LORBIT | None（移除） | 不需要轨道投影，减小输出 |
| MAGMOM | None（移除） | ISPIN=1 下无意义；`--spin` 时保留 |

- ENAUG=1360 不动：PREC=Accurate 下被 VASP 忽略（死参数），保留以与上游 MatPES YAML 一致。
- KSPACING=0.22 沿用 MatPES 默认，不写 KPOINTS 文件。
- xc_functional 固定 PBE。

## 校验（每个生成目录）

1. **文件完整性**：INCAR/POSCAR/POTCAR 存在；POTCAR 元素顺序与 POSCAR 一致；INCAR 有 KSPACING 时不应存在 KPOINTS 文件。
2. **INCAR 覆盖生效**：回读 INCAR，确认上表所有覆盖值；LORBIT、MAGMOM 不存在（`--spin` 时 MAGMOM 必须存在且 ISPIN=2）。
3. **结构合理性**（生成前检查）：最近原子间距 < 0.5 Å 或体积/原子异常（< 1 Å³ 或 > 1000 Å³）→ 该帧警告并跳过，不中断整批。

## 错误处理与输出

- 坏帧：打印警告、跳过、继续。
- 结束时汇总：成功 N、跳过 M、校验失败目录列表。
- 存在校验失败或全部帧被跳过 → exit 1；否则 exit 0。
- 前置条件：`PMG_VASP_PSP_DIR` 未设置时立即报错退出。

## 测试

- pytest 单测放 `tests/`（若 skill 无 tests 目录则新建脚本旁 `tests/`）：
  - 覆盖字典正确性（--spin 开/关两种）
  - 单帧/多帧目录布局
  - 结构合理性检查（构造重叠原子帧）
  - 校验器对故意篡改的 INCAR 报错
- POTCAR 依赖 `PMG_VASP_PSP_DIR`：无 POTCAR 库的环境下相关用例 skip。
