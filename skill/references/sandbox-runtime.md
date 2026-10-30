# Agent Teams 沙箱运行时

这份文档回答一个问题：**一个成员 Task 从创建到在沙箱里跑完，究竟经过哪些判定？**
结论都来自平台源码 `deepexi-agent-teams`，不是猜测。写派发/排障代码前先读这一节，能省掉大部分“为什么一直 QUEUED”。

## 一、四种运行时模式

`backend/agent-teams-common/src/agent_teams_common/runtime/modes.py`：

| 模式 | 谁来跑 | 说明 |
|---|---|---|
| `local` | 用户桌面进程 | 调试用，不等于生产执行面 |
| `daemon` | 远端自建 daemon | 走 daemon claim 接口 |
| `cloud` | 远端托管 daemon | 同上 |
| `managed_sandbox` | 平台沙箱 worker | 原生专家团成员应当使用这一种 |

平台沙箱 worker 只处理 `runtime_mode == "managed_sandbox"` 且 `status == "online"` 的 runtime（`runtime/sandbox/claim_service.py`）。把成员 Agent 绑到本地 exec-host，即使它显示 online，成员 Task 也不会被沙箱 worker 领取。

## 二、Task 生命周期

状态：`queued → claimed → running → completed | failed | cancelled`（`models/task.py`）。

关键字段：

- `dispatch_generation`：每次重排 +1；claim 用 `WHERE dispatch_generation = ?` 做 CAS，旧代次永远抢不到。
- `state_version`：每次状态推进 +1，前端据此判断新鲜度。
- `attempt`：第几次执行；`max_attempts` 默认 3。
- `claimed_at` / `started_at` / `finished_at`：排障时间线的主证据。
- `lease_until` / `last_heartbeat_at`：租约默认 1200 秒，心跳默认 20 秒（`daemon/auth.py`）。
- `runtime_id`：创建 task 时就写入，取 agent version 的 `runtime_id`，其次 agent 自身的 `runtime_id`（`runtime/task_execution.py: get_effective_agent_runtime_id`）。

推论：**Task 的 runtime 绑定在创建时确定，不会因为你把 Agent 改绑到新 runtime 而自动迁移。** 只有 `requeue_task_for_dispatch` 会把 `runtime_id` 清成 `None`，之后才允许其他兼容 runtime 领取。

## 三、claim 的完整判定链

`runtime/sandbox/claim_service.py::_claim_next_sandbox_task` 按顺序判定，任一失败就静默跳过候选：

1. runtime 存在，且 `status=online`、`runtime_mode=managed_sandbox`；
2. tenant 匹配；
3. workspace 在 `authorized_workspace_ids` 内；
4. `Task.status = queued`；
5. `Task.runtime_id` 等于本 runtime，或为空；
6. `dispatch_not_before` 已到（退避窗口结束）；
7. 该 task 没有非终态且已分配 sandbox 的 `SandboxRun`（`cleaned/expired/cancelled` 除外）；
8. issue 级串行化：若 source issue 是 `assignee_type=squad`，且该 issue 已有 `claimed/running/dispatched` 的 task，则跳过（`tasks/squad_serialization.py`）；
9. agent version 存在，且 `task.runtime_id` / version 配置的 runtime / provider 三者之一匹配本 runtime；
10. 安全策略允许本 runtime 类型与 capabilities（`security/policy.py::runtime_satisfies_security_policy`）。

全部通过后执行一次 CAS update；`rowcount != 1` 是正常的抢占失败，不是异常。

**唤醒机制**：Redis/内存 wakeup 只是提示；权威来源是 DB 扫描，`sandbox_db_scan_interval_seconds` 默认 2.0 秒，`sandbox_claim_batch_size` 默认 20。所以健康的成员 task 正常在数秒内 claim；写“固定 sleep 10 秒再查”是在掩盖问题，不是优化。

## 四、两种“排队”不是一回事

- **容量排队**：runtime 在线但并发占满，`current_running_tasks >= max_concurrency`。
- **永远排队**：绑定了一个离线/暂停/非 managed_sandbox 的 runtime，或 agent version 被安全策略拒绝。此时 claim 循环每次都会跳过它，页面看起来“在线但不动”。

判定方法：同一 runtime 发一条**新**消息。

- 新 task 秒 claim、老 task 不动 → 老 task 自身被绑定或 run 锁死，查 `runtime_id` 和 `SandboxRun`。
- 新 task 也排队 → runtime/容量/策略问题。

## 五、沙箱实体：volume、生命周期、网络

配置模型在 `runtime/sandbox/profiles.py`：

| 配置 | 默认 | 影响 |
|---|---|---|
| `volume.mode` | `task_volume` | 每个 task 独立 PVC；`session_volume` 跨轮保留，`agent_volume` 跨会话保留 |
| `lifecycle.mode` | `per_task` | `session` 会在同一 chat session 内复用沙箱实例 |
| `lifecycle.idle_ttl_seconds` | 300（60~86400） | session 沙箱空闲多久销毁 |
| `network.mode` | `restricted` | `disabled` 完全断网；`open` 全放行 |
| `network.allowed_domains` | 空 | `restricted` 下的出站白名单 |
| `resources` | 2C / 4Gi / 5Gi | 资源不足会直接体现在沙箱创建或执行失败 |

实践含义：

- 业务要调 MES/Foil 等外部 API，就必须让出站域名在 `allowed_domains` 里；`disabled` 下任何外呼都会失败，且错误看起来像业务超时。
- `per_task` + `task_volume` 每次都是冷启动；把 `session` + `session_volume` 当默认加速手段前，先确认跨轮状态没有被旧文件污染。
- `session` 复用只在 `cleanup_policy == "session_idle_destroy"` 且 `lifecycle.mode == "session"` 生效；空闲超时后仍是冷启动。

## 六、沙箱里的目录与 Skill 注入

`runtime/execution_env.py` 与 `runtime/sandbox/bundle.py` 固定布局：

```
/workspace/
  context/claim.json        # 本次 claim 快照
  context/agent.json
  context/task.json
  context/previous_tasks.json
  context/upstream_results.json
  context/skills/<skill>/   # 只放该 AgentVersion 绑定的 Skill
  artifacts/                # 任务产物；完成时随 task 发布
  logs/  tmp/
  input/prior-artifacts/    # 上游产物副本
```

三条硬事实：

1. **只注入绑定的 Skill 目录**。依赖仓库根目录 `scripts/` 的 Skill 在沙箱里必然 `ModuleNotFoundError`。
2. **注入的是 Skill 的已发布版本**（`published_version_id` 存在时优先）；草稿改动不上线。
3. Skill 绑定挂在 **agent_version** 上（`AgentSkillBinding.agent_version_id`），不是挂在 Agent 上。换 Skill 集合要么发新 agent version，要么确认绑定记录已更新。

## 七、跨沙箱状态：唯一可靠的事实边界

每次用户消息都是一个**新 task**；`volume.mode=task_volume` + `lifecycle.mode=per_task` 是默认值，同一会话的相邻两轮会拿到**不同 sandbox**（DW-OBS-015 实测 `155534e3…` 与 `42af8aea…` 不是同一个）。

| 载体 | 跨轮是否还在 | 适用 |
|---|---|---|
| `/tmp/**` | 否（同一 task 内多次工具调用还在） | 单 task 内的重试缓存、批次进度暂存 |
| 工作区临时文件 | 否 | 同上 |
| `artifacts/` 产物 | 是，随 task 发布为平台 artifact | 成员结果、报告、可下载交付物 |
| child issue description / comment | 是 | 业务状态、幂等标记、成员输入 |
| root issue description | 是 | 跨轮唯一根、权威状态副本 |
| Task/Issue 字段（status、assignee、时间戳） | 是 | 观测与判定 |

四条硬规则：

1. **跨轮身份必须从平台重建。** 只把 `diagnosis_issue_id` 之类写进 `/tmp/progress.json`，只能保护“同一 task 内的重试”。跨 task 必须能从 root children、`/task-runs` 或 issue description 重新解析出同一个身份。
2. **`/tmp` 丢进度必须落到结构化失败关闭**，不能回退成“再派一次”。重复派发会造出第二个 child，页面上出现两张调度卡，业务侧无法收敛。
3. **回到平台读，也不等于拿到真相。** 非 owner token 的 issue 可见性只覆盖 primary owner 或自己创建的 issue；`children=[]`、单条 `404` 都不能当作“不存在”的证据（见 `PITFALLS.md`「issue 可见性」）。
4. 需要跨轮比较的幂等键与计时，写成字符串放进 description/comment；不要依赖文件 mtime 或系统时钟。

### 跨轮输入到底给了什么

新 task 的沙箱不是“上一轮沙箱的续集”，平台只补丁式地给两类东西（DW-OBS-145 源码核对：`api/chat.py::_prior_chat_session_context` / `_truncate_chat_context`、`runtime/sandbox/bundle.py::_previous_tasks_payload`）：

- `task.input` 里内嵌最近 12 条聊天消息，单条截断 3000 字、总量截断 12000 字；
- `context/previous_tasks.json` 只透传 `work_dir` 与 artifact 引用，**不含回复文本**。

实测 `context/task.json` 的形状确认了这一点：业务上下文在 `context.chat`（session/issue/user_message id）与 `context.previous_tasks.items`（历史 task_id 列表），而不是上一轮的 workspace。

两个直接后果：

1. 把状态塞进用户可见回复再“读回来”，等于把状态交给截断器和模型改写；跨轮必须回到 issue/artifact。
2. 成员结果只存在于 artifact 时，团长要按 `previous_tasks.items[].task_id` 反查 artifact，而不是指望它出现在回复里。

推论：**任何“跨轮状态”设计，要么保存在 issue/artifact，要么每轮从平台重建。** 二者都不满足时，正确行为是失败关闭并给出可执行提示，而不是猜一个默认值继续跑。

## 八、三个“沙箱”别混

排查时最常犯的错误，是把三种完全不同的执行面混为一谈：

| 名字 | 谁在跑 | 典型症状 | 判定方法 |
|---|---|---|---|
| Codex CLI 本地沙箱 | 你终端里的开发代理 | 连不上 `127.0.0.1:9228`、DNS 解析失败、`bind: Operation not permitted` | 本地工具链问题，与平台链路无关；不要写进产品结论 |
| DeepWorks `managed_sandbox` | 平台成员 Task 的执行面 | 成员 task `queued`、`SandboxRun` 创建失败、外呼超时 | 看 `/task-runs`、`sandbox_runs`、runtime `status` |
| `local` / `daemon` runtime（含 exec-host） | 用户桌面或自建 daemon | 成员卡永久 QUEUED（绑到离线 runtime） | 看 task 的 `runtime_id` 与该 runtime 心跳 |

判据：**产品结论只能来自 2 和 3 的真实证据。** 本地沙箱里“跑通了 mock”最多算单元证据；本地沙箱“连不上”也不能当作平台不可用。

## 九、观测点

| 想看什么 | 查哪里 |
|---|---|
| 成员是否真被派发 | `GET /api/issues/{issue_id}/task-runs`，返回 `source_type=issue, source_ref=issue.id` 的 Task |
| 为什么没被领取 | Task 的 `runtime_id`、`status`、`dispatch_not_before`、`attempt` |
| 沙箱是否创建/复用 | `sandbox_runs`（状态含 `create_uncertain`）、worker 日志 `reuse_sandbox` 阶段 |
| 运行时长 | `claimed_at → started_at → finished_at`，三段分别归因排队、启动、执行 |
| runtime 是否真活 | `/api/runtimes` 的 `status` + `last_heartbeat_at` |

原生专家调度卡来自**真实成员 Task**。Skill 内联或 child 无 assignee 时，`task-runs` 不会出现成员执行记录，页面也不会有调度卡。

## 十、性能预算

| 阶段 | 健康值 | 说明 |
|---|---|---|
| 创建 → claim | ≤3s | DB 扫描 2s + CAS；超过 10s 必有异常 |
| claim → started | 冷启动通常 10~40s | 含沙箱创建、镜像/卷挂载、agent 启动；`sandbox_runtime_agent_start_timeout_seconds` 默认 60 |
| 预热 session 复用 | 明显低于冷启动 | 仅 `lifecycle.mode=session` 且未过 idle TTL |
| 单任务执行 | 由业务决定 | 超过产品 SLA 时先看模型轮数与外部 API，而不是继续加 sleep |

成员 Task 连续无进展时，编排侧应在 180 秒内返回结构化 `member_runtime_stalled`，保留调度卡，不得改成内联执行。

## 十一、源码索引

- claim 活路径：`backend/agent-teams-common/src/agent_teams_common/runtime/sandbox/claim_service.py`
- 重复副本：`backend/deepexi-agent-teams-backend-sandbox-worker/src/.../runtime/sandbox/claim_service.py`（改平台必须两份同步，并在变更说明里注明活路径）
- 重排/恢复：`runtime/sandbox/reconcile.py`、`runtime/sandbox/recovery.py`、`tasks/enqueue.py`
- issue 串行化：`tasks/squad_serialization.py`
- task 入队与 runtime 绑定：`agent_flows/issue_to_task.py::enqueue_issue_task_for_agent`
- 上下文注入：`runtime/execution_env.py`、`runtime/context_files.py`
- 沙箱 profile：`runtime/sandbox/profiles.py`、`schemas/runtime.py`

## 十二、真实 Test 观测（2026-10-28，原生专家团）

一轮 managed_sandbox 团长任务的真实数字（squad `6cbca882`，runtime `9ad27dfb`，`max_concurrency=50`）：

| 段 | 值 | 来源 |
|---|---|---|
| dispatch → claim | 0s | `/task-runs` 的 `dispatched_at` |
| claim → started | 6s | `started_at`（含 sandbox 冷启动、`workspace_cache_miss`） |
| started → completed | 189s | `completed_at` |
| 端到端 | 195s | 超出 180s 预算 |

结论：**冷启动不是瓶颈**。claim→start 只有 6s，189s 全在模型单轮执行（工具往返）。要压总时长，先压模型轮次和工具调用次数，不要先动沙箱参数。

四条容易踩的观测坑：

1. **事件流时间戳不可用于阶段耗时**。`member:sandbox_creating`…`member:executing` 的 `created_at` 会全部等于 claim 那一秒（批量落库），真实 `started_at` 却晚 6s。阶段耗时只认 task 字段：API 用 `dispatched_at` / `started_at` / `completed_at`，DB 用 `claimed_at` / `finished_at`。
2. **API 时间是无时区本地时间**（如 `2026-10-28T11:33:13`，Asia/Shanghai）。按 UTC 解析会让 `now - created` 变成负数，`never_claimed` / `member_stalled` 永不触发。审计工具必须把 naive 时间按本机本地时区解析，并保留回归测试。
3. **`GET /api/tasks/{id}` 会滞后**。实测 `completed_at` 写入后数分钟内单次读取仍返回 `running`。判定卡死前必须重读，或用 `/task-runs` + 事件终态交叉确认。
4. **`relay_content is empty` 是编排脚本缺陷，不是沙箱故障**。典型前兆是团长连续以缺参方式调用 `render_reply.py`。同 runtime 另一轮 6s 内 start、正常完成，足以排除容量/离线。

发布面还有一条硬门禁：`POST /api/skills/{id}/publish` 是否直发取决于 `button_skills_publish_direct` 菜单权限或 group publish owner。普通成员发布只会得到 `pending_review`，`approve` 返回 403；在审批通过前，agent worker 继续执行旧的 `published_version_no`，任何"已上传"都不等于"已生效"。
