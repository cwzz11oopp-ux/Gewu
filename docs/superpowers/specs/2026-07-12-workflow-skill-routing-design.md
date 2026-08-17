# 工作流 Skill 路由设计

## 目标

将现有的 `WorkflowEngine` 从“按步骤调用固定 Agent”升级为“调度层按步骤、输入 artifact 与执行模式选择并加载 Skill，Agent 在 Skill 约束下完成工作”。Agent 仍是稳定的职责边界；Skill 是可复用的工作协议，不与 Agent 一一绑定。

本次只实现本地 `skills/` 目录的受控读取、静态步骤路由、向 LLM 注入 Skill 指令，以及可追溯记录。不会让模型自行搜索整个目录、不会执行 Skill 中的 shell 指令，也不会在本次改造中生成训练脚本或改变 GPU provider 的执行语义。

## 根因

`skills/**/SKILL.md` 在仓库中存在，但运行时代码没有读取或解析它们。各 Agent 直接调用 `LLMProvider.generate_json(task, inputs, schema_hint)`；`WorkflowEngine` 只把固定步骤分派给固定 Agent。因此界面与文档中的 “Research Skill”、“Planning Skill” 只是 Agent 名称，不代表其实际使用 `SKILL.md`。

## 架构

### 职责边界

- `WorkflowEngine`：根据 `step_id` 选择 Skill，构造受控上下文，保留步骤顺序、锁定、artifact 与异常处理的所有权。
- `SkillRegistry`：维护显式的 `step_id -> skill_id[]` 映射；本次不使用 LLM 动态选 Skill。
- `SkillLoader`：只从仓库根目录下的 `skills/<skill_id>/SKILL.md` 读取 UTF-8 文本，解析 YAML frontmatter 的 `name`、`description`，并返回正文；拒绝绝对路径、`..` 路径与缺失文件。
- Agent：接收已解析的 `SkillContext`，只负责把它传给 LLM provider；不访问文件系统、不决定下一步。
- `LLMProvider`：新增可选 `instructions` 参数。Qwen provider 将其作为系统级工作约束发送；Mock provider 记录但不依赖它。

### 初始路由表

| 工作流步骤 | Agent | Skill |
| --- | --- | --- |
| `problem_understanding` | Research | `idea-discovery` |
| `knowledge_integration` | Research | `research-lit` |
| `hypothesis_generation` | Hypothesis | `idea-discovery` |
| `evidence_reasoning` | Critic | `research-review` |
| `research_plan` | Planning | `experiment-plan`, `ablation-planner` |
| `experiment_task` | Experiment | `experiment-bridge`, `run-experiment` |
| `experiment_run_analysis` | Experiment | `monitor-experiment`, `analyze-results` |
| `feedback_revision` | Critic | `experiment-audit`, `research-review` |
| `report_export` | Writer | `paper-writing` |

如某个文件不存在，调度层应以稳定错误 `SKILL_NOT_FOUND:<id>` 停止该步骤，不能悄悄退回到无 Skill 的 LLM 调用。

### 注入格式与上下文边界

每个已加载 Skill 都格式化为：ID、说明、正文。正文保留原文；一次步骤最多加载路由表中的文件，不作目录遍历。为了防止提示词膨胀，单个文件截断为 12,000 个 Unicode 字符，总注入上限为 32,000 个字符；截断状态写入 trace。

`SkillContext` 仅含：`id`、`name`、`description`、`instructions`、`truncated`。Agent 把所有上下文合成给 LLM provider；不把文件路径或任意 shell 权限交给模型。

### 可追溯性

每个使用 LLM 的步骤事件的 `tool_calls` 新增一个 `skill_router` 记录，包含路由的 Skill ID、是否截断和总字符数。实验 provider 步骤同样记录该记录，即使执行器尚不消费提示词。这样 artifact 可审计“由哪个 Agent、使用哪些 Skill、在哪一步产生”。

## 交互与错误处理

- Skill 文件缺失：API 返回 400，错误码为 `SKILL_NOT_FOUND:<id>`；前端复用现有错误条展示，不继续后续自动步骤。
- Skill 文件内容为空：同样视为 `SKILL_NOT_FOUND:<id>`。
- 不解析或执行 `allowed-tools`、`Write`、`Bash` 等声明；它们是文档内容，不授予应用额外权限。
- 现有用户 Run 不自动重写；从对应步骤重新运行时才使用新的 Skill 路由。

## 测试

- `SkillLoader`：正确加载允许路径；拒绝目录穿越；缺失和空文件返回稳定错误；分别验证单文件和总长度截断。
- `SkillRegistry`：每个现有步骤返回预期有序 Skill 列表。
- LLM provider：Qwen 请求包含合成 instructions；Mock 调用兼容新的可选参数。
- Workflow：运行 `research_plan` 时 trace 记录 `experiment-plan` 与 `ablation-planner`；缺失路由文件时该步骤失败且没有新增 artifact。
- 现有后端与前端构建测试继续通过。

## 非目标与后续

本改造提供实验设计 Skill 的真实运行时约束，但不自动把自然语言计划转成模型代码。下一阶段将以 `experiment_task` 输出的 manifest 为边界，增加模型/数据/指标/脚本生成与预检，之后再允许真实 GPU 执行。
