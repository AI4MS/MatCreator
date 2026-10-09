# Mission: 理解 PBE 赝势如何进入 Bohrium VASP 作业

## Why
用户正在用 MatCreator 完成真实硅计算，已下载 PBE 文件，但尚不清楚它们与 VASP 的联动。学习目标是能判断输入是否齐全、文件应该放在哪里，以及计算实际使用了哪套势。

## Success looks like
- 区分 PBE 泛函、PAW 数据集、VASP 程序和 Bohrium 提交工具。
- 指出本机赝势库、单次作业输入目录、远端运行目录的区别。
- 能从 Si POTCAR 的 TITEL、ZVAL、ENMAX 读出基本含义。

## Constraints
- 从本次 Si 文件入手，用短课和即时反馈练习解释。
- 实际计算继续由 MatCreator 主导，保留轨迹和作业身份。
- 本目录是独立教学工作区，避免占用软件仓库根目录的教学状态文件。

## Out of scope
- 首课不推导 PAW 方程，不设计新的收敛研究。
