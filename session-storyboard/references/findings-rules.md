# Findings 规则与优化建议

所有 finding 必须来自 trace 事实，并带 evidence 字段。LLM 可以润色，但不能生成数值、拓扑或副作用结论。

## 确定性规则

| 规则 | 触发 | 结论边界 |
|---|---|---|
| 单轮超预算 | `max_round > budget` | 只说该轮超预算 |
| 两个时间口径 | 多轮且 Wall ≠ Round sum | 分别显示 Wall、Σ rounds、gap |
| 探索型工具 | 命令文本命中 `ls/--help/grep/rg/cat/sed -n/head/tail/find/pwd/task.json` | 文本启发式，必须点事件核对；skills.json bootstrap 不算 |
| 同一 child issue 多 run | 同一 `issue_id` 下多个 member Task | 可能 continuation，按 run_id 比较 |
| leader 额外 claim | leader Task 没有映射到用户轮次 | 可能续跑；核对幂等键和副作用 |
| 成员地板成本 | 有成员 Task 且完成时间 | 报告数量、平均耗时，不直接归因脚本 |
| 执行尾部 | 最慢轮最后一个成员完成到 round end ≥5s | 归为 leader 汇总/校验/回复 |
| 缺 artifact | `artifact_count is null` | 只说覆盖不可用，不说 0 |
| 缺副作用账本 | `duplicate_effects is null` | 0 个 API event 只代表未观测到 |
| 沙箱缺上下文 | tool 有 cmd 无 cwd | 说明需要 runtime 日志或完整 task messages |

## 优化建议：从工业智能体经验来

### 1. 先量 Task，再量脚本

先看成员 Task 的 claim/start/执行拆分。成员 Task 40~50s 里可能只有 1~2s 是确定性命令。不要按整轮时间优化 `run.py`。

### 2. 每个成员每轮只跑一条自包含命令

避免 `ls`、`--help`、读 `task.json` 再决定下一步。固定步骤应内联到一条脚本；否则模型回合和工具往返会放大地板成本。

### 3. 不为正式卡和执行卡各建一个 member hop

确认/执行如果共享同一业务根，可内联成一次成员执行。新增 hop 会叠加平台 claim/start/模型回合，而不是只加几秒脚本。

### 4. continuation 先比 run_id

同一 issue 多个 Task run、leader 额外 claim 不一定是关键路径。旧 run 的 continuation 通常已完成或被新 run 取代；比较 `run_id` 和幂等键，不要把它们串行相加。

### 5. artifact-first handoff

成员先写 artifact / issue description，再结束 Task；leader 可在 artifact 可见后继续，但必须有幂等键。否则只能等 Task done，尾延迟变大。

### 6. 一切副作用带幂等键

重复确认、并发事件、旧 continuation 必须收敛到同一终态。没有结构化副作用账本时，页面只能显示「不可判定」，不能宣称 0 次调用。

### 7. 本体默认走 live

live 本体是默认路径；快照只能显式 opt-in。把 ontology 快照当默认性能优化会引入新鲜度和一致性问题，不应作为通用建议。

### 8. 发布判据只认新会话

新 Test 会话 + Skill/Agent/squad 版本核对 + 完整事件时间线 + 副作用计数 + 最终用户可见消息。旧会话、旧 issue 或「子任务已创建」不能作为完成证据。

## 合成建议

HTML 的 Findings 只显示短句。给用户的详细优化建议按这个顺序：

1. 事实：哪一轮/哪个 Task/哪条命令。
2. 机制：平台地板、模型回合、工具往返、leader 尾部、重复 hop。
3. 动作：具体改 orchestrator、skill、member prompt 还是采集。
4. 验证：新 Test 会话 + 哪个指标要下降。
