# aidb 计算前预检（issue #2，开发期 A）

主规格为 [Ai-ready_Database #1](https://github.com/Zikkying/Ai-ready_Database/issues/1)，
本次只实现 [T1 / #2](https://github.com/Zikkying/Ai-ready_Database/issues/2)。

主 thinking agent 和内部 step executor 共用工具回调。加载 `vasp-pymatgen`、
硅任务的 `atomic-structure`，以及受支持任务直接调用准备或远程工具时，
先建立当前任务上下文，再通过 ai-ready-db Skill 的公开 adapter 执行本地预检。
技能名称去除首尾空白并忽略大小写。纯概念请求与训练技能不触发。

信息来源为当前自然请求、内部步骤的 action / prior_context / 父任务 goal，
以及请求中明确引用的工作区结构文件。后续“继续 / 改用 / 重算”请求可以补充
前一用户请求，但当前方法优先。未设置 goal 不会跳过提取。
材料身份无法确定时先澄清；已确定 Si 时可先发现候选。PBE、bulk、diamond
缺失时只是检索默认值，反馈中的 known / retrieval_defaults / unknown 分别保存。
准备计算前仍需明确结构模型。非首期晶相、体系或方法会暂停并说明支持边界。
这是固定硅样例的有限识别，不是通用自然语言或结构等价判定器。

## 配置与公开接口

优先使用已安装 `ai-ready-db` Skill 的 `scripts/aidb_loop_adapter.py`。
也可使用 `AI_READY_DB_ROOT` 指定仓库的标准 Skill 包；禁用 Skill 时不绕过。
client/server 的根目录、Python、配置、socket 等配置沿用 aidb 自身契约。
例如本地开发设置 `AI_READY_DB_ROOT` 与 `AI_READY_DB_PYTHON`，隔离测试额外
设置 `AI_READY_DB_CONFIG` 和 `AI_READY_DB_BRIDGE_SOCK`。

不导入 aidb 内部模块、不连接数据库表。adapter 调用公开 preflight，单材料
min_records=1，无相关材料扩展。每个 CLI 调用等待最多 20 秒，整个 adapter
最多 50 秒；取消或超时终止本次 adapter 客户端，数据库桥接服务仍按自身生命周期运行。

## 反馈与阻断

Skill 正文正常加载；after_tool_callback 将 `aidb_preflight` 加入实际工具响应，
模型能够读取并向用户反馈。反馈同时写入 session_log 和运行日志。
成功响应读取完整 `preflight-report.json`，保留完整候选 ID、报告、审计位置。
`found` 是候选命中，不能当成已经核验可复用；本票不实现候选复用/归档。
`not_found` 是成功查询空候选；`failed` 是真实配置、连接、超时或协议失败。
任务上下文与原始 adapter 交换记录保存在工作区 `.aidb/requests/`，完整查询
报告和 audit 保存在 adapter 的 `.aidb/runs/` 下。失败诊断也保留位置。

开发期 A 中，失败或材料澄清状态阻止新结构/输入准备及 tracked remote-job
提交/上传/执行。只准备的请求即使查询成功也不授权提交。状态/结果读取工具
仍可使用。同任务同次 invocation 的重复入口共用回执；新轮次重新查库，
条件变化不会复用旧回执。此约束覆盖登记的工具入口，不承诺约束任意自由
shell/Python 入口。后续 T5 才切换最终 B，不能把本票称为完整闭环验收。

## 验证

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_aidb_skill_hook.py -q
```

测试使用确定性 ADK runner、实际两种 agent 接线、受控准备/提交工具边界，
和相邻 Ai-ready_Database 的真实公开桥接及隔离本地库。命中样本仅用于测试，
不是本任务提交的真实计算。没有相邻 aidb checkout/runtime 时集成用例跳过。
不提交 Bohrium 作业、不归档未完成计算、不自动上传云端数据库。
