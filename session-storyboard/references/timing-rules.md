# 时间口径

## 四个时间

```text
Wall time      = 最后最终回复 - 第一条用户消息
Round time     = 该轮最终回复 - 该轮用户消息
Round sum      = Σ Round time
Between gap    = Wall time - Round sum
Active time    = 所有 Task/工具 span 的并集（下界）
Visual horizon = 最后一轮结束 + 300s 内的尾部证据（只影响渲染，不影响上面四个指标）
```

`finalizing`、`final-message.txt artifact` 可能在最终 assistant 回复之后才写入。它们应进入可视化的 Session / 最后一轮视图，但不得改变 Wall time、Round time、Round sum 或 Gap。旧轮次的 stale artifact 事件不能拉伸旧轮次的 zoom 范围。

### 预算看什么

`120s` 是单轮预算时，只看 `max_round_seconds`：

- ✅「最慢一轮 104s，未超 120s」
- ❌「整个会话 8.2m，超了 120s」

多轮会话的 Wall time 天然大于 120s，不代表单轮超时。

### Task 内部再拆

成员 Task 的 30~50s 不等于脚本执行 30~50s。优先拆：

```text
created → claimed        平台排队/认领
claimed → started        runtime/沙箱/模型启动
started → completed      模型回合 + 工具 + 收尾
completed → round end    leader 汇总和最终回复
```

## 时间来源

| 来源 | 可信度 | 说明 |
|---|---|---|
| 用户消息 → assistant 消息时间差 | exact | Round time 的首选口径；含初始 leader、成员、continuation |
| assistant `elapsed_ms` | exact（局部） | 只表示最后一次 leader continuation，不等于整轮耗时 |
| task `created/claimed/started/completed` | exact | 生命周期 |
| run events `created_at` | exact | 秒级 |
| task messages tool_use/result | exact（相对顺序） | 若没有独立结束时间，tool span 标 derived |
| 日志/轮询 | observed | 有采样误差 |
| 为叙事连接而补 | inferred | 必须可见标注，不进入关键指标 |

## 常见错误

1. 把重复 continuation 的 Task 时长加进关键路径。
2. 把 leader 多轮 claim 当成多个 child。
3. 把工具调用数当业务副作用数。
4. 把 `artifact_count=null` 当 0。
5. 把本地会话的第一条 assistant 当作整轮结束；实际后面还有 tool part。
6. 用 assistant `elapsed_ms` 覆盖整轮时长；多 Task 轮次应保留用户消息起点并计算 wall time。
7. 只按用户消息的 Task ID 归属轮次；产出最终回复的 continuation Task 也必须有 `round_id`。
8. 没有最终回复仍生成 assistant 事件，导致空时间戳或把进行中会话误报为 completed。
