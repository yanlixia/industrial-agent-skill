# SessionStory schema

`session-story/v1` 是 collector 与 renderer 之间的唯一契约。

```json
{
  "schema_version": "session-story/v1",
  "generated_at": "2026-10-29T00:00:00Z",
  "target": {
    "kind": "squad_session",
    "id": "...",
    "title": "可读标题",
    "environment": "test",
    "workspace": "deepsense-workspace",
    "squad_id": "...",
    "agent_id": "",
    "root_issue_id": "...",
    "status": "completed"
  },
  "window": {"start": "...", "end": "...", "duration_ms": 0},
  "rounds": [],
  "tasks": [],
  "events": [],
  "topology": [],
  "artifacts": [],
  "side_effects": [],
  "metrics": {},
  "findings": [],
  "provenance": [],
  "gaps": [],
  "raw_evidence": {}
}
```

`window` 是 Wall time 口径。渲染层的 `view.total` 可以比 `window` 略大，用于展示最后一轮结束后 300s 内的 `finalizing`、`final-message artifact` 等尾部证据；该 horizon 不进入 Wall time、Round time 或预算比较。

`raw_evidence` 保存脱敏后的原始事实，键固定为：

| key | 内容 |
|---|---|
| `session` | 会话对象 |
| `session_messages` | 用户/助手消息 |
| `run_events` | runtime 原始事件，按 run id 分组 |
| `task_details` | Task 详情 |
| `task_messages` | Task message（含 tool_use / tool_result） |
| `issues` | 根 issue 与 child issue |
| `issue_comments` | issue 评论 |
| `artifacts` | artifact 原始事件与元数据 |

HTML 的「全量证据」模式、搜索框和 `Export JSON` 都直接消费该字段。原始值递归脱敏后再写入。

## Round

```json
{
  "id": "R2",
  "label": "选择 1",
  "start": "...",
  "end": "...",
  "duration_ms": 104000,
  "task_id": "...",
  "final_task_id": "...",
  "run_id": "...",
  "final_run_id": "...",
  "final_leader_elapsed_ms": 34000,
  "status": "completed",
  "timing_source": "derived"
}
```

`duration_ms` 是用户可见轮耗时，必须优先用用户消息到最终 assistant 消息的 wall time。`elapsed_ms` 只表示最后一次 leader continuation，写入 `final_leader_elapsed_ms`，不能覆盖整轮起点。`task_id` 是用户消息触发的初始 Task，`final_task_id` 是产出最终回复的 continuation Task；两者都属于同一轮。

## Task

```json
{
  "id": "...",
  "kind": "leader | member",
  "title": "设备故障诊断 2026102807000004",
  "agent_name": "V2本地诊断专家",
  "status": "completed",
  "issue_id": "...",
  "parent_task_id": "",
  "created_at": "...",
  "claimed_at": "...",
  "started_at": "...",
  "completed_at": "...",
  "duration_ms": 34000,
  "round_id": "",
  "source": "platform-api"
}
```

团长每次 claim 都可能是一个独立 Task run，但它们不都是 topology child。成员 Task 才是 child。

## Event

```json
{
  "id": "evt-0001",
  "ts": "...",
  "end_ts": "...",
  "duration_ms": 1000,
  "task_id": "...",
  "round_id": "R2",
  "actor": "V2本地诊断专家",
  "kind": "tool",
  "title": "工具 · exec_command",
  "detail": "...",
  "status": "",
  "source": "runtime-events",
  "confidence": "exact",
  "evidence_refs": [],
  "sandbox": {
    "cmd": "...",
    "cwd": "...",
    "io": "...",
    "result_output": "...",
    "result_observed": true,
    "result_source": "runtime-events",
    "files": [],
    "network": [],
    "exit": "not captured"
  },
  "error": {
    "category": "cli_invalid_choice",
    "severity": "error",
    "line": 12,
    "summary": "...",
    "evidence_ref": "run:r1:seq:42"
  }
}
```

工具字段口径：

- `end_ts` / `duration_ms` 只在有真实结束事件时写入；没有 result 的调用保持为空，不按下一个事件推算。
- `result_observed = true` 表示已观测到 result（输出可能为空）；`result_output = ""` + `result_observed = true` 渲染为 `observed empty output`。
- `result_source` 为 `runtime-events`、`task-messages`、`local-db` 之一；`task-messages` 回填不会伪造 `end_ts`。
- `cwd`、`exit` 没有真实来源时必须是 `not captured`，不得写成 `0`、`completed` 或空字符串冒充。

`kind` 的渲染分类：

| kind | 颜色/语义 |
|---|---|
| `platform` | 平台 claim/start/等待 |
| `model` | 模型阶段 |
| `tool` / `sandbox` | 工具/沙箱执行 |
| `child` / `member` | 成员 Task |
| `api` | 外部 API |
| `artifact` | artifact |
| `error` | 失败 |
| `user` / `assistant` | 用户可见轮次边界 |

## Topology

```json
{
  "id": "...",
  "title": "维修预审 FDR...",
  "kind": "leader | member",
  "meta": "V2本地维修专家 · completed · 35s",
  "issue_id": "..."
}
```

根节点标题必须是可读文本；没有业务标题时先用 session title，绝不能再显示裸 UUID。

## Metrics

关键字段：

- `wall_seconds`
- `round_sum_seconds`
- `between_round_gap_seconds`
- `active_seconds`
- `max_round_seconds`
- `leader_run_count`
- `member_task_count`
- `tool_calls`
- `exploration_calls`
- `tools_with_end`
- `tools_with_result`
- `tools_with_output`
- `artifact_count`（可能为 `null`）
- `duplicate_effects`（可能为 `null`）
- `auxiliary_leader_runs`

## Validation

`scripts/session_story.py validate` 检查 schema、target、rounds 和事件时间单调性。渲染器还会处理缺失字段，但 collector 不应故意省略 `source` 和 `confidence`。
