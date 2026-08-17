# Qwen Skill Catalog 确定性路由设计

## 目标

将当前仅含九条固定映射的 `SkillRegistry` 升级为完整的、命名驱动的 Qwen Skill Catalog。调度层在每个工作流步骤中从可用 Qwen Skills 里确定性选择必选与补充 Skills；选择不依赖额外模型调用，且可由 trace 完整复现。

## 可用 Skill 范围

Catalog 只扫描仓库根目录 `skills/` 的一级子目录中的 `SKILL.md`：`skills/<skill-id>/SKILL.md`。

必须排除以下目录及其全部后代：

- `skills/skills-codex/`
- `skills/skills-codex-claude-review/`
- `skills/skills-codex-gemini-review/`
- `skills/shared-references/`

目录名是 Skill 的稳定 ID 和第一功能标签；frontmatter 的 `name`、`description` 仅用于补充关键词。不会把跨模型审阅版本、工具权限或任意嵌套文件加入 Catalog。

## 架构与数据流

`SkillCatalog` 在创建时枚举允许的一级目录，使用已有安全 `SkillLoader` 读取每个 `SKILL.md` 的 frontmatter，并生成不可变的 `CatalogSkill`：`id`、`name`、`description`、`tokens`。`tokens` 由 kebab-case 目录名分词并补充名称与描述中的 ASCII 词；不调用 Qwen。

`SkillRegistry` 保留每一步的必选 Skill 和允许补充标签：

| 步骤 | 必选 Skill | 允许补充标签 |
| --- | --- | --- |
| 问题理解 | `idea-discovery` | `idea`, `research`, `literature`, `novelty` |
| 知识整合 | `research-lit` | `arxiv`, `semantic`, `literature`, `review`, `wiki` |
| 假设生成 | `idea-discovery` | `idea`, `novelty`, `claim`, `research` |
| 证据推理 | `research-review` | `review`, `claim`, `novelty`, `proof` |
| 实验设计 | `experiment-plan` | `ablation`, `training`, `experiment`, `optimize`, `formula` |
| 实验任务 | `experiment-bridge` | `run`, `training`, `gpu`, `experiment`, `system` |
| 实验运行与分析 | `monitor-experiment` | `analyze`, `result`, `training`, `experiment`, `audit` |
| 反馈修正 | `experiment-audit` | `review`, `audit`, `result`, `claim`, `refine` |
| 报告导出 | `paper-writing` | `paper`, `write`, `figure`, `claim`, `rebuttal` |

`WorkflowEngine` 生成纯文本上下文：当前 Run 的 `domain`、`problem_input`、`constraints`，以及当前可用 artifact 的 type、title 和 JSON 文本值。它把该文本交给 Registry，但不把完整 artifact 或完整 Skill 内容用于选择。

## 选择规则

1. 必选 Skill 总是先入选，按注册表顺序加载。
2. 补充候选必须在当前步骤的允许标签集合中至少命中一个 token，且不能已是必选 Skill。
3. 候选分数等于：每个与步骤允许标签匹配的 token 4 分；每个与上下文 token 匹配的 token 2 分；目录名与上下文直接子串匹配额外 1 分。
4. 仅保留分数大于 0 的候选；依次按分数降序、ID 升序排序。
5. 单步最多加载 4 个 Skill；当必选 Skill 超过上限时保留全部必选并不选择补充 Skill。

同样的 Catalog、Run 和 artifact 状态必然得到同样的选择结果。没有补充候选时，继续使用必选 Skill；这不是错误。

## 可追溯性与安全

`skill_router` trace 记录扩展为：`mandatory_skills`、`candidate_scores`、`selected_skills`、`excluded_directories`、`truncated` 与 `instruction_characters`。候选分数记录每个入选或落选候选的标签命中和上下文命中，便于解释选择过程。

Catalog 继续依赖 `SkillLoader` 的目录穿越防护、UTF-8 读取和长度上限。目录中 `allowed-tools`、`Bash`、`Write` 等文本始终只作为 Qwen 的说明文本，不授予应用执行权限。

## 非目标

- 不读取或选择 Codex、Claude、Gemini 变体目录。
- 不使用 Qwen 选择 Skill，也不增加额外模型调用。
- 不自动生成实验 manifest、训练代码或 `train.py`；该能力在后续的可执行实验任务改造中实现。
- 不改变当前 Agent 角色、步骤顺序或 provider 接口。

## 验收与测试

- Catalog 只包含一级根目录 Skill，明确排除三个跨模型目录和 `shared-references`。
- `experiment_task` 的上下文含 `gpu`、`training` 时，保留 `experiment-bridge` 并确定性追加匹配补充 Skill；结果最多四个。
- 同一上下文连续路由两次，候选分数和 Skill 顺序完全相同。
- 没有命中补充项时只返回必选 Skill。
- trace 包含必选项、候选分数、最终项和排除目录。
- 既有安全加载、后端测试和前端构建保持通过。
