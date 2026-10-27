---
name: industrial-agent-builder
description: 设计并编写高性能工业智能体（Agent Teams 专家/专家团）。当用户要写工业 agent、设备运维/诊断/巡检类智能体、给专家团提速、或生成 industrial-agent-template 模板仓库时使用。不适用于普通聊天机器人或与 Agent Teams 无关的业务系统。
---

# 工业智能体构建规范

把 DeepSense 已验证的单执行体架构复用到新的工业智能体：**每轮一个 task、脚本即专家、issue 树即数据库、LLM 只出现在三个固定点**。目标是单轮 P95 ≤120s，而不是"能回答"。

## 工作流

1. **先过决策门**。读 `references/decision-gates.md`，判定这单活是 workflow、单专家+Skill，还是真的需要建团。默认降级：大多数"专家团"应降为单专家或纯 workflow，这是最大的一笔延迟收益。
2. **选运行时档位**。A exec-host / B bridge 内联 / C 平台原生。对用户承诺延迟的业务只允许 A/B；C 只用于探索和演示。
3. **按模板生成文件**。用 `references/file-templates.md` 和 `assets/scaffold/` 产出 `entry/agents.md`（≤120 行）、`entry/soul.md`（≤30 行）、`skills/<域>-orchestrate/SKILL.md`、`turn.py` CLI 契约。过程细节下沉到 SKILL.md 和脚本，不进 agents.md。
4. **实现时守住十二条硬规则**。读 `references/architecture.md`，把不变量写进 `contracts/` 和 CI，不靠模型自觉。其中六条不可妥协：
   - 每用户轮次恰好一个平台 task，禁止派发自由 LLM 成员（例外门见 architecture.md）。
   - 第一个工具调用固定为 `turn.py`，命令逐字固定，占位符仅允许运行期值。
   - 一切副作用带幂等键；状态由 issue 树纯函数重建，无跨轮沙箱状态。**权威源 = issue description/artifact；回复 marker 只是加速通道，损坏必须静默回退**。
   - skill 载荷 ≤15 个文件 / ≤200KB / 仅 Python 标准库 / 无运行时安装。
   - **emit/exempt 一致性进 CI**：凡写进用户回复的隐形 marker 必须在 validate_reply 豁免清单，用 `scripts/check_marker_contract.py`（scaffold 自带）强制，不靠自觉。
5. **测试与验收**。路由全矩阵 + 幂等并发 + 黄金 issue 树 fixture + **往返契约**（查询轮产出→渲染→验收→下一轮恢复同一状态，组件级全绿不代表链路通）；子进程错误通道断言。test 环境黄金剧本用 `scripts/remote_probe.py` 模式探针定时回放，延迟一行一轮入台账；每轮 P95 ≤120s 且 `timings` 齐全。缺 timings 或超标不得宣布完成。

## 判断红线

- LLM 永远不做编排；控制流只在脚本。
- LLM 只出现在三个固定点：意图兜底（固定三选一）、事实抽取与答案匹配、回复措辞（过 `validate_reply`）。
- 回复必须过 `validate_reply`；失败一次修正，仍失败用模板兜底，永不裸错误给用户。
- 等待在脚本内切片轮询（25s/片，片内 2s），每片至多一次推理。
- 每轮推理 ≤10 次、slice ≤12 片；超限返回 `escalate` 保状态，不无限重试。

## 产物

一个可直接落地的智能体仓库骨架：`entry/` + `skills/` + `contracts/` + `tests/`，带幂等键定义、路由状态表和验收剧本。若用户只想要方案，先输出决策门结论和架构图，不要直接写代码。
