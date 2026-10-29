# 数据源与采集边界

## Cloud

已验证的 Agent Teams 接口：

| 接口 | 用途 |
|---|---|
| `GET /api/chat/sessions?status=all` | 定位 session 和标题 |
| `GET /api/chat/sessions/{id}/messages` | 用户/assistant 可见轮次，assistant 带 `elapsed_ms` |
| `GET /api/chat/runs/{run_id}/events-list` | `subtask:created`、`member:*`、`tool_running`、`artifact:created`、`final:message` |
| `GET /api/tasks/{task_id}` | agent、issue、生命周期、prompt、output、timeline |
| `GET /api/tasks/{task_id}/messages` | 完整 `tool_use` / `tool_result`，包含命令和输出 |
| `GET /api/issues/{id}` | 根 issue 标题；部分 child issue 会 404 |

实测注意：

- `/api/issues/{root}/children` 可能返回空；不能据此判断没有 child。
- child 拓扑优先从 run events 的 `subtask:created` 还原。
- 一个 `member:tool_running` 逻辑调用会连续发两条事件；collector 以 `member:tool_result` 为边界去重。
- task prompt 第一行通常是 `ISS-xxxx: 标题`，issue API 404 时可作为 fallback 标题。
- `artifact:created` 里 `final-message.txt`、`turn-trace-*.json` 是运行产物；默认事件流只把业务 artifact 放进关键事件。

云端采集的 token：

- 默认通过 `dp-ask-teams/scripts/ask_teams_agent.py` 从桌面会话同步。
- 也可 `--token` 显式传入，或 `--no-sync-token` 使用环境变量。

## Local opencode.db

只读打开 SQLite：

| 平台 | 路径 |
|---|---|
| macOS prod | `~/Library/Application Support/com.deepexi.deepworks/deepworks-engine-data/xdg/data/opencode/opencode.db` |
| macOS test | `~/Library/Application Support/com.deepexi.deepworks.test/deepworks-engine-data/xdg/data/opencode/opencode.db` |
| macOS dev | `~/Library/Application Support/com.deepexi.deepworks.test.dev/deepworks-dev-data/xdg/data/opencode/opencode.db` |
| Windows | `%APPDATA%/com.deepexi.deepworks*/.../opencode.db` |

表关系：

- `session`：id、title、directory、time_created（ms）
- `message`：session_id、time_created、data JSON（role、agent 在 JSON）
- `part`：message_id、time_created、data JSON（text、reasoning、tool、tool state）

轮次边界是「user 到下一条 user」，不是第一条 assistant。一个 turn 可能包含多条 assistant 消息和多个 tool part；结束时间取该 turn 最后一个 part。

## Saved artifacts

acceptance evidence 目录至少需要 `summary.json`。可选：

- `timeline-root.json`
- `child-topology.json`
- `task-*.json`
- `*probe*.json`

没有 artifact 清单时，`artifact_count` 必须是 `null`，不能写 0。

## 数据缺口示例

- 只有 session messages：没有 Task claim/start，不能拆平台等待。
- 只有 acceptance JSON：tool 可能只有命令摘要，没有 cwd/网络/退出码。
- 没有副作用账本：`external_calls` 只表示「已观测到的 API 事件」，不是业务调用次数。
- 没有 artifact 事件：只能写不可用，不能断言没有产物。
