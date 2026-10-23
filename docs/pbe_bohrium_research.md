# 本机 PBE PAW 数据与 Bohrium VASP 联动

核验日期：2026-10-07。资料来自 VASP / pymatgen 官方文档、已安装的 bohr 命令帮助及 MatCreator 代码。本报告解释输入联动；云端 VASP 启动和真实计算是否成功，以会话工具轨迹和作业结果为准。

## 当前已确认的本机资源

本机检查报告 [check.json](../.workspace/silicon-t2/debug-pbe/check.json) 记录：`/home/shik-mechrevo-wsl/projects/9f9b5b-main/psudopotential/paw_pbe/Si/POTCAR.Z` 是 UNIX compress 数据，`gzip -cd` 可以解压。真实 `PotcarSingle` 解析结果为 Si / PBE，`TITEL = PAW_PBE Si 05Jan2001`，`ZVAL = 4`，`ENMAX = 245.345 eV`，具有完整的数据集结束标记，`is_valid = True`。解压字节的 SHA256 为 `52dbe99da884e0191b2d348dfcc281aaba36e2eacd3019a49d93cf07d84da86b`。这支持文件身份与解析完整性，不证明下载来源授权，也不把它称为最新推荐版本。[本机检查证据](../.workspace/silicon-t2/debug-pbe/check.json)

此前不是完全没有 Si 赝势：资源放在另一个项目，MatCreator 的 pymatgen 配置没有指向可读取的目录；原始 `paw_pbe/Si/POTCAR.Z` 与当前 pymatgen 所期待的目录、文件命名还不同。把原始目录路径直接赋给 `PMG_VASP_PSP_DIR` 不一定解决问题。官方方法先通过 `pmg config -p <原始库> <转换后的库>` 重排数据，再设置 `PMG_VASP_PSP_DIR` 为转换后根目录。本次可以只准备 Si，具体路径以 MatCreator 留存的准备记录为准。[pymatgen POTCAR setup](https://pymatgen.org/installation.html#potcar-setup)

## PBE 到底是什么

PBE（Perdew–Burke–Ernzerhof）是一种描述电子交换与关联能的近似方法，属于 GGA。VASP 本身实现这种计算方法；`INCAR` 中的 `GGA = PE` 选择 PBE。如果没有其它交换关联方法设置，VASP 会依据 POTCAR 的 `LEXCH` 选择默认方法。[VASP GGA](https://vasp.at/wiki/GGA)

你下载的“PBE 库”是用 PBE 构建的各元素 PAW 原子数据。它不是 PBE 算法插件，也不是硅晶体的计算结果。PAW 用平滑的辅助波函数减少平面波计算量，再在原子周围恢复所需的全电子信息；VASP 的实现采用冻结芯电子近似。这里的数据提供原子参考态、投影函数等信息，让 VASP 在求解晶体电子状态时正确处理核附近的贡献。[VASP PAW formalism](https://vasp.at/wiki/index.php/Projector-augmented-wave_formalism)

对于你这个 Si 数据，`TITEL` 识别数据类型、元素和生成日期；`ZVAL = 4` 表示每个 Si 原子显式处理四个价电子；`ENMAX` 是该数据推荐的平面波截断能，实际 `ENCUT` 可以更高，仍须依所需精度判断收敛。不要手改 POTCAR 内部数值或 `LEXCH` 来“切换方法”。[VASP POTCAR](https://vasp.at/wiki/POTCAR)

## 从本地库到云端计算

```text
WSL 中保存的 PBE PAW 库
       │ pymatgen 读取本次结构需要的元素数据
       ▼
本次作业输入目录：INCAR、POSCAR、POTCAR、KPOINTS
       │ MatCreator tracked submit → bohr --input 打包上传
       ▼
Bohrium 作业工作目录中的同名输入文件
       │ 容器镜像提供 VASP 程序和运行环境
       ▼
VASP 读取输入 → 计算能量/力 → 输出 OUTCAR、vasprun.xml 等
```

这是根据官方输入定义和本仓库提交实现得出的联动流程：镜像负责程序，作业目录负责本次数据。因此，使用输入上传方式时，无需把整套 PBE 库放入镜像；本次 Si 的 POTCAR 随输入送到作业工作目录即可。WSL 的库路径不会自动变成远端容器路径。[VASP input](https://vasp.at/wiki/index.php/Input)、[MatCreator 提交适配器](../src/matcreator/control_plane/providers/bohr_batchjob.py)、[本仓库 Bohrium 技能](../src/matcreator/skills/bohrium/SKILL.md)

已实际运行 `bohr batchjob submit --help`：安装版本的 `--input` 说明是本地文件或目录打包并上传；`--image` 指容器镜像，`--command` 指远端执行命令，`--dry-run` 只校验不执行。适配器把输入目录传给 `--input`，先做同参数 dry-run，再提交。库目录与 VASP 可执行文件路径互相独立；镜像内到底如何调用 VASP / MPI，应通过该镜像的启动信息核验，不能仅凭镜像名字推断。[MatCreator 提交适配器](../src/matcreator/control_plane/providers/bohr_batchjob.py)、[VASP Batch Job 参考](../src/matcreator/skills/vasp-pymatgen/references/bohr-batchjob.md)

## 四个输入如何协作

| 文件 | 告诉 VASP 什么 | 硅任务中的含义 |
|---|---|---|
| POSCAR | 晶胞、元素数量、原子位置 | 这次研究哪一个硅结构 |
| INCAR | 计算任务、电子求解与精度参数 | 结构弛豫还是静态能量、PBE、收敛标准等 |
| POTCAR | 每种元素所用 PAW 原子数据 | 本次只有 Si，所以只含一份 Si 数据集 |
| KPOINTS | 周期体系布里渊区的采样 | 电子状态在倒空间如何采样；也可以用 INCAR 的 KSPACING 自动生成 |

这些文件相互配合才构成完整的标准任务；KSPACING 的自动采样只在没有 KPOINTS 时使用。[VASP input](https://vasp.at/wiki/index.php/Input)、[VASP KSPACING](https://vasp.at/wiki/KSPACING)

POTCAR 按 POSCAR 中的元素类型顺序排列，按元素类型一次拼接，不按每个原子重复。比如纯 Si 有两个原子也只需一份 Si 数据；含 Si、O 的结构若 POSCAR 元素顺序是 Si O，则 POTCAR 是 Si 数据接 O 数据。选错顺序会使结构与原子数据对应错误。[VASP Preparing a POTCAR](https://vasp.at/wiki/Preparing_a_POTCAR)

可以把这四份文件理解为“地点与原子布局、计算要求、元素资料、采样方案”；VASP 是实际计算者，bohr 是把工作送到 Bohrium 并管理作业的入口，MatCreator 负责准备、提交、检查与留存。

## 复现与留存

结果应留存实际 POTCAR 的 TITEL 和字节哈希、INCAR、POSCAR、采样、镜像与启动命令，以及 MatCreator 的会话、工具轨迹和 durable job ID / provider batchjob ID。更换 POTCAR 版本可能改变结果，比较能量时要确认输入方法一致；当前输入校验工具会绑定提交时的哈希。[VASP POTCAR](https://vasp.at/wiki/POTCAR)、[本仓库结果验证工具](../src/matcreator/tools/silicon_vasp.py)

本报告与提交记录只记录身份和核验信息，不把整套库放入代码仓库。pymatgen 官方也明确不能随软件分发 VASP 赝势。文件可解析与用户的授权来源是两项不同事实。[pymatgen POTCAR setup](https://pymatgen.org/installation.html#potcar-setup)

后续仍由 MatCreator 在已有计算授权下执行硅工作流；输入准备成功、CLI 提交成功、科学计算收敛是三个不同的检查点。这里的库核验并不等于 issue #3 已通过真实计算验收。

## 实际输入准备结果

MatCreator 已在原会话 `silicon-t2-20261007-env2` 解压并核验所需 Si 文件，暂存于
`.workspace/silicon-t2/pseudopotentials/POT_GGA_PAW_PBE/Si/POTCAR`，随后用
`MPRelaxSet` 生成 `vasp-relax-Si/` 的 INCAR、POSCAR、POTCAR、KPOINTS 和 run.sh。
作业 POTCAR 与原文件解压结果的 SHA256 相同。实际 INCAR 的 ENCUT 为 520 eV，
与 POTCAR 的 ENMAX 245.345 eV 是两个不同数值：前者是本次输入选择，后者是势的建议值。
这项输入准备已解决此前 `PmgVaspPspDirError`，但不能据此宣称云端启动或收敛成功。
证据：[MatCreator 输入准备报告](../.workspace/silicon-t2/pbe-input-preparation-report.json)、
[势准备证据](../.workspace/silicon-t2/pseudopotential-evidence.json)。

配套教学：[互动首课](learning/vasp/lessons/0001-pbe-potcar-bohrium.html)与
[输入速查卡片](learning/vasp/reference/vasp-inputs.html)。

## 真实运行核验

原会话中的 Si 松弛与静态总能均已完成科学验证：松弛 durable job
`56072cf15fe94e639d7b71f563ed320a` / batchjob
`5ed5945230884cf88f30475c1afed598`，能量 -10.84124445 eV；静态 durable job
`c266a691a9874ab9ad097bfd09dca9b6` / batchjob
`6ce96bb830764a01976fec7ef9d16a99`，能量 -10.84601452 eV（均为 Si2 晶胞总能）。
实际输出证明该私有镜像可以运行 VASP 6.3.0，所暂存的 Si POTCAR 确实参与两步计算。
静态结构来自成功松弛结构，势哈希相同。完整证据与测试见
[T2 验收记录](silicon_vasp_t2.md)。本研究不宣称数据库归档或完整本地闭环已完成。
