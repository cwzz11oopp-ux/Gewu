# Round 6 Step 1 — 实验页信息架构重构报告

## 范围与约束

本次仅修改前端页面与只读 read-model 映射。未修改 scientific workflow、历史 Artifact、Experiment Result、Harness、Validator 或 dataset contract；未创建生产 Run，未重跑生产实验，未创建 Git commit。

核验对象为既有 Run `run_a5c60cfe56ff`，读取使用现有正式只读 API。

## 原页面的重复来源

- 顶部双轨时间线、工程处置、迭代贡献、实验对比表和右侧检视器都在重复同一 Experiment / revision。
- 性能演化从“已完成”记录中选指标，工程修复版本可能被混入，不能代表科学演化。
- Current Experiment 原本取列表末项；历史中最新的审计失败 Result 会造成“已完成 + 32%”这样的语义冲突。

## 新信息架构

页面收敛为五个区域：

1. **运行概览**：问题、状态、阶段、有效科学实验数、工程处置数、数据集、资源、最新运行时长。
2. **实验历程**：唯一的历史入口；横向紧凑节点与“全部 / 科学实验 / 工程处置”筛选。
3. **当前有效实验**：当前被选择的有效结果、状态、完成度、主指标与真实详情入口。
4. **科学性能演化**：只显示有效科学实验；一个有效结果时改为结果摘要，多个可比科学版本时才使用折线图。
5. **当前科学结论**：假设状态、模型比较、差异、种子、参数匹配、Audit 与最多三项限制。

已删除“迭代贡献”、实验对比表和右侧重复检视器；工程失败不再在页面其他区域重复。

## Scientific / Engineering 分类

`ExperimentItem.classification` 的数据合同已收紧：只有同时满足下列条件的 Result 才是 scientific：

- `is_real_experiment === true`
- `audit.integrity_status === "passed"`

其余失败、审计失败或非真实实验结果统一显示为 engineering。分类不依赖标题、版本号或推测字段。

对于 `run_a5c60cfe56ff`：

- 有效科学实验：1（`experiment_4`，Result Artifact `art_40c3b329e885`）
- 工程处置：9（包括 CLI/Bundles 失败与两个审计失败版本）
- 当前有效实验：`experiment_4`，已完成，完成度 100%。

## 科学性能与结论映射

- 图表候选仅来自已完成且 `classification === "scientific"` 的记录。
- 主指标来自实验计划声明的 evaluation；无声明时才使用结果中第一个非时间/方差指标。
- 指标方向仍按计划方向判断，lower-is-better 时下降才表示改善。
- 性能演化始终保留折线图；仅一条有效科学结果时绘制单个真实点，并明确提示尚不能形成趋势。
- 结论直接读取已通过审计 Result 的 `analysis.comparisons`、`metrics`、`seeds`、`audit`，以及最新 `scientific_conclusion` 的假设状态和限制。
- 已对当前 Result-analysis 中三类结构化英文限制（容量代理、Fashion-MNIST 泛化范围、未去重数据）增加中文展示映射；该映射只作用于 UI，历史 Artifact 原文保持不变，未知限制不擅自改写。

本 Run 的真实展示为：Held-out Test Accuracy 91.362，Small CNN 对 Capacity-Matched MLP 的差异 +2.474 pp，5/5 seeds，CNN 421,642 / MLP 402,570，Audit passed。

## 性能演化最终实现

- Card 顶部新增指标选择器，默认使用持久化 `primaryMetric`；没有 primary metric 时，才由 view-model 选择第一个可用的真实指标。组件本身不猜测指标。
- 可选项来自已通过审计、真实 Result Artifact 的数值 metrics，例如 Held-out Test Accuracy、CNN Final Loss、MLP Final Loss、Training Loss Convergence Rate；不插值、不填零、不从结论文本反推数值。
- 每条点均保留 `experimentId`、`artifactId`、metric key、metric value。Tooltip 显示用户可读 revision 标签、Experiment ID、当前值、相对上一有效结果的原始变化、按方向判定的趋势和 Result Artifact ID。
- 连线前检查持久化的数据集、metric/evaluation 定义和评估方法合同；不一致或缺失的版本不被连接，也不会作为工程版本补点。
- 方向由实验计划的 metric direction 映射；higher-is-better 与 lower-is-better 分别计算改善/下降。顶部摘要显示当前、初始、总变化和最佳值。
- 当前 Run 仅有 1 条通过独立审计的科学结果，因此图表显示单点和“至少 2 个相同科学合同结果才可形成趋势”的提示。这是数据事实，不以摘要卡、伪趋势或工程版本替代。

## 实验详情

时间线节点与“查看实验详情”按钮均打开真实 Drawer。Drawer 只显示已有字段：Experiment ID、类型、状态、父修订、假设、数据集、种子、参数、主指标/指标、Result Artifact、Audit、修订/失败原因、科学反馈、尝试记录及关联文件；缺失值不显示 `undefined`、`null` 或伪造占位数据。

## 修改文件

- `frontend/src/components/researchViewModel.ts`
- `frontend/src/components/workspace/ExperimentPage.tsx`
- `frontend/src/components/workspace/MetricCharts.tsx`
- `frontend/src/styles.css`
- `frontend/tests/research-view-model-contract.test.mjs`

## 验证结果

- `node --experimental-strip-types --test frontend/tests/presentation.test.ts frontend/tests/research-view-model-contract.test.mjs`：**21 passed**。
- `pnpm --dir frontend build`：**passed**（TypeScript 与 Vite production build）。
- 只读浏览器核验：既有 Run 正常打开；统一时间线、筛选、100% 完成度、科学结果摘要、科学结论与实验 Drawer 均已验证。
