# Round 6 Step 1：科研报告可视化能力接入与前端科学演化展示修复

日期：2026-08-15  
范围：仅代码、测试与本报告；未执行新的生产 E2E Run，未修改历史 Run 或 Artifact。

## 1. 结论

已建立“模型决定是否选图，确定性渲染器按持久化数据绘图”的报告可视化链，并修复前端将工程处置、失败记录和科学性能演化混为一谈的问题。新报告会持久化 `Report Spec`，其中每张图都包含其 `source_artifact_ids`；Word 导出只渲染该 spec，绝不从正文反推数值或补造训练曲线。

历史正式 Run `run_a5c60cfe56ff` 保持只读：状态仍为 `completed`，Artifact 总数仍为 186，最终报告仍是 `art_d583cc7faf0e`，最终真实结果仍是 `art_40c3b329e885`。

## 2. Skills 审计与实际调用链

| 项目 Skill | 适用能力 | 审计结论 |
| --- | --- | --- |
| `paper-figure` | 条形图、折线图、分组对比、表格 | 能指导数据图选择，但此前未进入正式报告运行时。 |
| `figure-spec` | 工作流、架构、管线的 FigureSpec → SVG | 文档指向 `tools/figure_renderer.py`，项目中不存在该运行时工具，不能把文档当作可执行能力。 |
| `mermaid-diagram` | 简单流程与时间线 | 适合轻量图，但此前也未被正式报告导出调用。 |
| `paper-illustration` | 定性/自然图像 | 不适用于不可伪造的科学统计图，未采用。 |
| `competition-report`、`report-quality-audit` | 正式报告正文与事实审核 | 原正式链路实际使用的两项 Skill，继续保留。 |

原真实路径为：`WriterAgent.build_report` 生成叙述章节 → `reporting.build_report_docx` 仅写入正文、表格 → API 下载 Word。根因是该路径不存在 `ReportSpec/FigureSpec/ChartSpec`，没有渲染器，也没有图表选择合同；所以图表 Skills 即使存在也不会改变导出结果。

本次未把 `figure-spec` Skill 文档中的不存在工具伪装为运行时依赖，而是在实际 Writer/Exporter 链中补齐本地确定性实现。Skill registry 保持原有报告入口，避免无效文档指令挤占正式审核上下文。

## 3. 后端变更

### 数据与渲染合同

- 新增 `backend/app/report_visualization.py`：`ChartSpec`、`FigureSpec`、`OmittedFigureSpec`、`ReportSpec`，schema 版本为 `round6.visual-report.v1`。
- `WriterAgent` 的既有 `writer.report_outline` 调用新增受限的 `selected_figure_ids` 与 `figure_rationale`：模型只能从固定候选中决定是否选图，不能给出任意数字或图形命令。
- 所有图表数据由 `build_report_spec()` 从持久化的 `plan`、`experiment_result`、`revision` 等 Artifact 提取；每图记录输入 Artifact ID。
- 确定性渲染器使用同一 spec 生成 CJK 安全 PNG，并由 `python-docx` 嵌入 Word。字体优先使用 Windows 的微软雅黑/黑体，缺失时才回退。
- 图形候选包括：研究证据链、受控变量表、分随机种子对比、主要数值对比、训练曲线、工程/科学分轨时间线。
- 训练曲线只在真实 `epoch_metrics` 或 `training_history` 已持久化时生成；否则 `omitted_figures` 明确写出“未发现真实 epoch/step 指标”，不会生成拟合或示意曲线。
- `reporting.build_report_docx()` 只渲染已验证的 `Report Spec`；旧报告没有该字段时保持原行为，不会写入或回填任何历史 Artifact。

### 语言合同

- `RunRecord` 与 `ResearchState` 增加语言字段。Run 未显式设置语言时保留原有“根据输入内容自动判定”的兼容行为；显式 `zh-CN` 时由既有 engine 指令统一约束面向用户输出为中文。
- 新增的图标题、说明、数据缺口、前端状态和悬浮说明均采用简体中文。机器指标键仍保留原样以确保可追溯与可复现。

## 4. 前端变更

- `researchViewModel` 为每个实验建立 `primaryMetric`、优化方向、同指标基线和按优化方向归一后的变化量。`loss/error/time` 自动按“越低越好”，`accuracy/F1/AUC` 自动按“越高越好”；计划中声明的方向优先。
- 性能演化提示会显示“数值越高越好 / 数值越低越好 / 未声明”，SVG 点位 tooltip 同时包含指标值与语义。
- 失败、诊断与工程处置标为“工程”；有效实验标为“科学”。工程记录不再参与科学性能对比，实验对比表中明确显示“不计入科学对比”。
- “查看实验详情”保持真实选中状态并打开已有详情面板，参数、运行尝试、关联路径、指标与失败原因均来自对应实验记录；同时将 `Attempts`/`Artifacts` 等读者可见标签改为中文。
- 图表轴字体优先微软雅黑，新增性能语义条和工程/科学视觉分轨，避免中文标签回退为不稳定的拉丁字体。

## 5. 验证

### Focused

- `python -m pytest tests/backend/test_report_export.py tests/backend/test_report_visualization.py tests/backend/test_workflow_skills.py -q`：68 passed（修正 Skill 兼容后）。
- `node --experimental-strip-types --test frontend/tests/presentation.test.ts frontend/tests/research-view-model-contract.test.mjs`：20 passed。
- `pnpm --dir frontend build`：通过。

新增后端回归覆盖：持久化计划/结果 → FigureSpec → 主要对比与多 seed 图 → 训练曲线缺失原因 → 确定性 PNG → Word 内嵌图；同时验证数值和 Artifact lineage 未被替换。

新增前端合同回归覆盖：指标方向、低值指标的改善计算、工程/科学分离、实验详情选中路径及图表 tooltip 语义。

### 完整后端回归

`python -m pytest tests/backend -q`：**523 passed, 2 skipped**（82.93s）。

## 6. 历史正式 Run 的只读确认

通过 `GET /api/runs/run_a5c60cfe56ff` 和 `GET /api/runs/run_a5c60cfe56ff/report` 读取：

| 字段 | 值 |
| --- | --- |
| Run 状态 | `completed` |
| current_step | `report_export` |
| Artifact 数 | 186 |
| 最终报告 | `art_d583cc7faf0e` |
| 最终真实实验结果 | `art_40c3b329e885` |
| 历史报告 `Report Spec` | 不存在（未回填、未修改） |

没有调用启动、继续、重跑、创建 Artifact 或下载写入端点；未创建 Run，未重跑已完成步骤，未修改 Validator、Bundle、Harness、dataset 或科学审计合同。

## 7. 边界与后续

现有公开导出端点是 DOCX/ZIP；本次将确定性图嵌入 DOCX，供既有 Word→PDF 流程保留图像。项目当前没有独立 PDF API，因此没有在 Step 1 擅自扩展一个新的发布格式或运行外部转换器。

Round 6 Step 1 到此结束。未进入 Step 2，未创建 Git commit。

## 8. 截图反馈后的展示层复核修正

用户截图揭示了初版仍遗留的两个展示缺陷：

- 旧响应式规则覆盖了标题断行，机器任务名或长中文目的会横跨时间线卡片；
- 虽然“迭代贡献”已区分工程/科学，顶部时间线仍把所有版本混在同一轨道。

已修正为两条独立轨道：`科学实验` 只显示有效科学结果，`工程处置` 只显示失败、诊断和修复相关记录；每张卡片采用两行中文标题截断，机器任务名只保留在 hover 提示与实验详情的“技术任务名”字段。移除了会穿过卡片文本的旧连接线。

使用本地浏览器只读加载 `run_a5c60cfe56ff` 后进行了最终视觉复核：科学轨 3 条记录、工程轨 7 条记录，长标题已正确截断且无文本重叠。随后运行前端相关合同测试与生产构建。
