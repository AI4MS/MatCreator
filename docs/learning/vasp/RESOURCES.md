# VASP / PBE 资料

## Knowledge

- [VASP Wiki：POTCAR](https://vasp.at/wiki/index.php/POTCAR)
  文件内容、元素顺序、TITEL/ZVAL/ENMAX；解释实际 Si 文件的首选。
- [VASP Wiki：GGA](https://vasp.at/wiki/index.php/GGA)
  计算泛函的选择及 POTCAR 默认值；用于区分 PBE 方法和 PAW 数据。
- [VASP Wiki：PAW](https://vasp.at/wiki/index.php/Projector-augmented-wave_formalism)
  平滑波函数与原子附近的重构；进阶解释赝势为何提高效率。
- [VASP Wiki：Preparing a POTCAR](https://vasp.at/wiki/index.php/Preparing_a_POTCAR)
  从库挑选并组合 POTCAR 的官方操作说明。
- [VASP Wiki：ENCUT](https://vasp.at/wiki/index.php/ENCUT)
  截断能与收敛；ENMAX 不等于所有体系都已验证的精度。
- [pymatgen：安装与 POTCAR 配置](https://pymatgen.org/installation.html)
  本机库供输入生成器查找；实际路径仍以当前安装版本源码为准。
- [本仓库的 Bohrium adapter](../../../src/matcreator/control_plane/providers/bohr_batchjob.py)
  `input_path` 到 CLI `--input` 的真实调用边界。此相对链接从本目录指向项目源代码。
- [本机文件实测](../../../.workspace/silicon-t2/debug-pbe/check.json)
  用户下载的压缩 Si 文件及 pymatgen 解析结果；不包含 POTCAR 正文。

## Wisdom (Communities)

- [VASP 官方论坛](https://www.vasp.at/forum/)
  用于向维护团队确认特定势版本和程序报错；没有代用户发帖。

## Gaps

- 官方知识和文件校验不替代本次真实 VASP 成功及收敛验证。
- 镜像目录名不能证明其内部程序路径；以实际作业启动记录为准。
