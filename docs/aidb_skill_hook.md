# aidb 计算前预检（最终 B）

主规格为 [Ai-ready_Database #1](https://github.com/Zikkying/Ai-ready_Database/issues/1)，
初始实现为 [T1 / #2](https://github.com/Zikkying/Ai-ready_Database/issues/2)，
当前默认行为已按 [T5 / #6](https://github.com/Zikkying/Ai-ready_Database/issues/6)
切换为最终 B；[切换验收记录](silicon_vasp_t5.md)。

主 thinking agent 和内部 step executor 共用工具回调。加载 `vasp-pymatgen`、
硅任务的 `atomic-structure`，以及受支持任务直接调用准备或远程工具时，
先建立当前任务上下文，再通过 ai-ready-db Skill 的公开 adapter 执行本地预检。
技能名称去除首尾空白并忽略大小写。纯概念请求与训练技能不触发。

信息来源为当前自然请求、内部步骤的 action / prior_context / 父任务 goal，
以及请求中明确引用的工作区结构文件。后续“继续 / 改用 / 重算”请求可以补充
前一用户请求，但当前方法优先。未设置 goal 不会跳过提取。
材料身份无法确定时先澄清；已确定 Si 时可先发现候选。PBE、bulk、diamond
缺失时只是检索默认值，反馈中的 known / retrieval_defaults / unknown 分别保存。
准备计算前仍需明确结构模型或提供已有结构。非首期晶相、体系或方法会暂停并说明支持边界。
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
模型能够读取并向用户反馈；直接调用准备工具时其响应也携带同样证据。反馈同时写入 session_log 和运行日志。
成功响应读取完整 `preflight-report.json`，保留完整候选 ID、报告、审计位置。
`found` 是候选命中，适用性与复用由独立的 `reuse` 回执给出。
`not_found` 是成功查询空候选；`failed` 是真实配置、连接、超时或协议失败。
任务上下文与原始 adapter 交换记录保存在工作区 `.aidb/requests/`，完整查询
报告和 audit 保存在 adapter 的 `.aidb/runs/` 下。失败诊断也保留位置。

最终 B 中，查询失败先说明数据存在性未知与重复计算风险，再保持准备/提交
暂停。用户可回复回执给出的 `确认继续 <failure_id>`，明确接受此次失败风险，
仅继续原授权范围。仅准备授权仍不允许提交。拒绝、普通“继续/yes”、未确认、
旧会话或旧失败事件的确认保持暂停；提取错误与材料澄清不能绕过。
确认绑定原任务、条件及此次失败，已引用结构文件变化也会使其失效。
受支持 executor 只能由 runner 继承本轮已确认范围，不能用内部 action 伪造同意。
查询回执始终为 `failed`，`loop_complete=false`；计算完成仍自动尝试本地归档，
分别报告科学结果和实际保存结果。状态/结果读取工具仍可使用。
同任务同次 invocation 的重复入口共用回执；独立新请求与条件变化重新查库。
该确认针对已有失败事件，所以确认轮次沿用该事件而不制造新的查询失败。
此约束覆盖登记的工具入口，不承诺约束任意自由 shell/Python 入口。

## 验证

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_aidb_skill_hook.py -q
```

测试使用确定性 ADK runner、实际两种 agent 接线、受控准备/提交工具边界，
和相邻 Ai-ready_Database 的真实公开桥接及隔离本地库。命中样本仅用于测试，
不是本任务提交的真实计算。没有相邻 aidb checkout/runtime 时集成用例跳过。
不提交 Bohrium 作业、不归档未完成计算、不自动上传云端数据库。

## T4 verified candidate reuse

[T4 evidence and rules](silicon_vasp_t4.md) extend the original T1 discovery
behavior. Public preflight now exports the exact candidate IDs and verifies
scientific content before selecting. `found` remains lookup status;
`reuse.status` separately reports `reused`, `recalculate`, `unavailable`, or
`needs_clarification`. The response includes candidate diagnostics, selected
structures and separate eV/cell energies, source metadata and choice reason.

A compatible selection prevents preparation and new submission, including the
Flash executor entry. Explicit recalculation still queries first and respects
preparation-only authorization. Same-condition duplicates use persistent
archive times; ambiguity or meaningful condition differences require
clarification. Task changes trigger a new lookup. T4's historical A evidence is
retained; the current default is [T5 final B](silicon_vasp_t5.md). No cloud
database upload is performed.
