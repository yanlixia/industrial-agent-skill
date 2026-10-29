---
name: session-storyboard
description: 把任意专家或专家团的真实会话还原成可播放的 HTML 故事板。展示总时长、逐轮耗时、团长/成员 Task 时间线、沙箱命令与输出、子任务拓扑、artifact、确定性 findings 和数据缺口。用于「会话回放」「这次会话到底在干什么」「把执行过程画出来」「看看沙箱里做了什么」「分析为什么这轮慢」等请求。
---

# Session Storyboard

把一次真实 Agent 会话转成三件产物：

```text
<out>/session-trace.json     # 可审计的规范化事实
<out>/session-story.html     # 单文件、离线、可播放
<out>/collection-report.txt  # 数据源、覆盖率和缺口
```

HTML 不依赖 CDN。时间、拓扑和指标都来自 trace；缺失数据显式显示，不补造。

HTML 内置四个可交互层：

- **Session / R1 / R2 视图切换**：默认打开最慢一轮；ruler、泳道、事件流、播放头和 scrubber 同步缩放。
- **Task 抽屉**：点开任一 Task 查看生命周期、完整 prompt、可见输出、每个工具命令、完整结果、错误、artifact、usage 与 attempts。
- **错误结构化**：错误事件带 `category / severity / line / summary / evidence_ref`；源码检查命令里出现的错误字符串不会被误报成运行失败。
- **摘要 / 全量证据 + JSON 导出**：摘要视图只展示关键事件；全量视图可检索原始证据，`Export JSON` 导出脱敏后的完整 trace。

## 什么时候用

- 用户问「这条会话整个过程在做什么」「时间花在哪」「为什么慢」。
- 需要看团长/成员的 claim、启动、模型动作、工具、沙箱命令、回传。
- 需要给非工程同学展示一次专家团执行过程。
- 需要比较两条会话的轮次、成员 Task、工具调用或重复副作用。

不用于：只看单条日志、只查一个 SQL、或替代平台的实时监控。

## 选择数据源

按可用信息选，不要默认抓云：

| 输入 | `--source` | 说明 |
|---|---|---|
| 云端 chat session UUID | `cloud` | 最完整：可见消息、run events、task、task messages |
| 本机 `ses_*` session | `local` | 读 `opencode.db`；适合单专家桌面会话 |
| 已保存 acceptance/evidence 目录 | `artifacts` | 无云 token 时使用；字段取决于目录内容 |

先确认环境和 workspace。DeepWorks Test 默认 `--env test --workspace deepsense-workspace`。

## 标准流程

```bash
SKILL_DIR="${CODEX_HOME:-$HOME/.codex}/skills/session-storyboard"
# 如果 skill 实际在 ~/.agents/skills，则：
# SKILL_DIR="$HOME/.agents/skills/session-storyboard"

python3 "$SKILL_DIR/scripts/session_story.py" run \
  --source cloud \
  --env test \
  --session <session-uuid> \
  --workspace deepsense-workspace \
  --out tmp/session-story/<session-id>
```

本机单专家：

```bash
python3 "$SKILL_DIR/scripts/session_story.py" run \
  --source local --env prod --session ses_xxx \
  --out tmp/session-story/ses_xxx
```

已有 evidence：

```bash
python3 "$SKILL_DIR/scripts/session_story.py" run \
  --source artifacts --artifacts-dir <evidence-dir> \
  --out tmp/session-story/<name>
```

只重渲染或校验：

```bash
python3 "$SKILL_DIR/scripts/session_story.py" render <trace.json> --out <story.html>
python3 "$SKILL_DIR/scripts/session_story.py" validate <trace.json>
```

生成后先读 `collection-report.txt`，再打开 HTML 核对：

- 轮次数和每轮耗时是否与平台消息一致。
- 成员 Task 数是否等于真实 child 数，不把团长 run 算成 child。
- tool 调用是否去重；成员命令是否能看见。
- artifact 是 `0`、具体数量，还是 `—`（不可用）。
- 拓扑根节点不是裸 UUID。
- `tool coverage` 同时看 `end`、`result` 和 `non-empty output`：`result` 已观测但输出为空，表示命令确实返回空，不是缺数据；没有 result 才显示 `not captured`。
- 轮后出现的 `finalizing`、`final-message artifact` 属于可视化 horizon，不应改变 Wall time 或轮耗时口径。

## 必须遵守的口径

- **Wall time**：第一条用户消息到最后一个最终回复，包含轮间空档。
- **Round time**：一次用户输入到该轮最终回复。`120s` 预算只比较 round，不比较 Wall time。
- **Active time**：Task/工具有运行的时间并集，只是下界。
- **Gap**：Wall time 减各轮耗时之和，是轮间空档。
- **Visual horizon**：仅用于渲染，把最后一轮结束后 300s 内的 `finalizing` / artifact 等尾部证据纳入视图；它不进入 Wall time、Round time 或预算比较。

不要把 `Σ rounds`、Wall time、单轮最大值混成一个「总耗时」。

## 可信等级

- `exact`：服务端消息或事件直接记录的时间/状态。
- `derived`：两个精确时间相减，或由 Task 生命周期推导。
- `observed`：轮询、日志或外部探针观测，存在采样误差。
- `inferred`：为了叙事连接而推断，必须可见标注，不进入关键指标。

任何字段没有来源就显示不可用。`artifact_count = null` 表示没有 artifact 清单，不表示 0。`duplicate_effects = null` 表示没有幂等账本，不表示没有重复副作用。工具结果为空时用 `result_observed = true` + `observed empty output` 表示“结果已观测且为空”；只有 `result_observed` 缺失才是 `not captured`。

## Findings 与优化建议

先看事实，再给建议：

1. 单轮超预算：先拆 claim/start/Task/尾部，不要直接说「脚本慢」。
2. 成员 Task 地板成本：Task 级 30~50s 中可能只有 1~2s 是确定性命令。
3. 探索型工具调用：文本启发式，点开命令核对；`context/skills.json` 引导加载不算。
4. 同一 issue 多个 Task run / leader 额外 claim：按 `run_id` 比较，旧 continuation 不应进入关键路径。
5. 执行尾部：最后一个成员完成后到最终回复的 leader 汇总时间。
6. artifact 或副作用不可用：只能报告覆盖缺口，不能下「没有调用」的结论。

优化建议必须绑定 trace 证据。完整规则见 `references/findings-rules.md`，其中包含工业智能体已沉淀的经验：单命令自包含、不重复建 member hop、artifact-first handoff、幂等键、live 本体默认为主。

### 反哺 industrial-agent-builder

当一条优化建议同时满足以下条件时，才把它列为反馈候选：

- 跨领域通用，不依赖某个业务仓库、某次平台故障或某个临时环境；
- 有 `session-trace.json` 中的可引用证据；
- 能落成 industrial-agent-builder 的 `scaffold / contract / reference / test / tooling` 修改；
- 有明确验证方法和指标，不接受只写结论。

先把本次会话的全部候选一次性列给用户，逐项说明证据、机制、建议和验收方式；用户明确选择之后才能提交。未获用户逐项确认的候选不得创建 issue。

用户确认后，为每个候选写一个 JSON 文件并执行：

```bash
python3 "$SKILL_DIR/scripts/feedback_issue.py" \
  --candidate tmp/session-story/<session-id>/feedback/<slug>.json \
  --trace tmp/session-story/<session-id>/session-trace.json
```

候选 JSON 的 schema 见 `references/feedback-loop.md`。脚本会校验通用性、证据和脱敏，生成稳定幂等指纹，查重后创建 `yanlixia/industrial-agent-skill` issue。不要绕过脚本直接调用 `gh issue create`。没有 `gh` 或未登录时，只保留本地草稿并告知用户，不得宣称已反馈。

## 安全

- 默认只保留命令、cwd、状态、输出摘要；token、Cookie、password、api_key 会脱敏。
- 不把完整环境变量、会话 Cookie 或未脱敏输入写入 HTML。
- trace 和 HTML 默认留在 `tmp/`，不提交仓库。
- 输出目录里不要放真实凭据或生产工单附件。

## 失败时

- 云端 401/403：先用 `dp-ask-teams` 的 token 同步能力，或传 `--token`。
- `source=local` 找不到 session：先确认 `--env`；同一台机器可能有 prod/test/dev 三套库。
- 只有 messages、没有 Task：页面会降级为单泳道，并在 gaps 中说明。
- run events 为空：仍可从 session messages 和 task detail 还原轮次，但沙箱细节会缺失。

## 自检

```bash
cd "$SKILL_DIR"
python3 -m unittest discover -s tests -v
python3 scripts/session_story.py validate <trace.json>
```

参考文档：

- `references/session-story-schema.md`
- `references/data-sources.md`
- `references/timing-rules.md`
- `references/findings-rules.md`
- `references/feedback-loop.md`
