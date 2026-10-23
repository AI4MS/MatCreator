# issue #2 开发期 A：实现与验证记录

日期：2026-10-07。主规格：[Ai-ready_Database #1](https://github.com/Zikkying/Ai-ready_Database/issues/1)；
任务：[T1 / #2](https://github.com/Zikkying/Ai-ready_Database/issues/2)。实现仓库为 MatCreator。

已读取两个仓库的 AGENTS、issue tracker / domain 指引、aidb 术语表、公开 adapter
契约和现有需求记录。沿用已有 hook 接线与公开 Skill adapter；相邻数据库仓库
没有业务代码改动。原工作区存在的其他改动保留。

## 已实现行为

- 当前自然请求、内部 action / prior_context / 父任务 goal、明确引用的现有结构
  提供任务信息；goal 未设置也会提取或澄清。已知、检索默认、未知分开反馈。
- 主 thinking agent 与 step executor 在受支持的新结构/输入准备及远程工具前
  实际预检。Skill 名规范化与两种加载器一致；完整报告、候选 ID、审计与诊断
  随 Skill 或直接准备工具的响应返回。
- 命中、成功空候选、失败明确区分；开发期 A 失败暂停新增准备/计算。
  同次调用重复与并发入口共用回执；新请求重新查库，当前材料/方法优先。
- 纯概念请求不查库；材料不明先澄清。“只准备/只生成”不授权提交；内部步骤
  不能扩大父任务范围；“继续/重算”保留原授权，明确新提交指令才改变范围。
- 本票仅做候选发现。候选尚未核验可复用；不归档未完成计算，不新增数据库连接。

使用说明见 [aidb_skill_hook.md](aidb_skill_hook.md)。这是首期硅任务的有限上下文
识别，不是通用自然语言解析或任意自由 shell/Python 入口的约束保证。

## 测试

| 检查 | 结果 |
| --- | --- |
| MatCreator hook + 真实 Skill 加载器针对性回归 | 40 passed |
| 相邻 aidb adapter/client/server 契约回归 | 14 passed |
| Python compileall 与本次 diff whitespace 检查 | 通过 |
| 完整 `pytest tests/` | 两个既有旧导入错误导致收集失败 |
| 排除两个无法收集文件后的完整运行 | 743 passed / 39 failed；失败集合与基线完全相同，见 [基线对比](aidb_preflight_test_results.json) |

完整测试的两个收集错误为 `test_execution_grouping.py` 和
`test_matcreator_phase_and_skills.py` 使用过时的 `agents.MatCreator` 导入。
已在起始提交 `d92c7a2973831d5da5214befbc888e8587387eb4` 的独立临时导出中
复现同样错误。对可收集的完整测试也运行了原始基线，对比失败用例集合。
原始基线为 710 passed / 39 failed；失败涵盖既有 benchmark、知识图谱、旧
retry_config 断言、结构建模和前端文本断言。未为本票修改这些无关行为。

未安装或配置 mypy/pyright，本次执行 Python 编译检查与公开流程测试，未宣称
静态类型检查已通过。没有前端业务改动，未运行前端构建。
测试的查询使用真实隔离本地库与公开 CLI/client/server；准备和远程提交工具
为受控测试边界，命中记录为隔离测试样本。没有提交真实 Bohrium 作业，没有
真实科学结果归档验收，也没有云端数据库上传。

## Standards

按 code-review 技能，由独立规范审查代理检查起始提交后的变更及后续修正。
最初发现材料澄清绕过、提取失败缺少持久日志、继续/重算识别与授权继承问题。
均增加公开工具边界测试并修正；统一继续识别规则以防词表漂移。
最终复审：无剩余可操作规范问题。

## Spec

由独立规格审查代理对照 issue #1 和 #2 检查。
最初发现只准备授权扩大、旧硅目标污染新材料、直接准备工具缺少查询反馈，
以及继续请求绕过。均已修正，相关公开流程回归通过。
最终复审：无剩余 T1 开发期 A 规格问题。

最终结果为 Standards 0 / Spec 0 个剩余可操作发现。
完整计算、自动归档/重查、独立会话复用与最终 B 切换仍属于后续 T2–T5。
