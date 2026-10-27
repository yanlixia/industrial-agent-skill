# 单执行体参考架构

## 一、五个延迟乘数（为什么是一个小时）

| 乘数 | 机制 | 量级 |
|---|---|---|
| M1 任务扇出 | 一个用户轮次 = 团长 task + 1~2 个成员 task，每个 task 独立冷沙箱、独立 LLM 全量读上下文 | ×2~4 |
| M2 LLM 游走 | 探测不存在的 CLI、翻 /opt、猜 URL/端点 | ×2~5 |
| M3 冷启动+技能物化 | 每 task 重新拉全部绑定 skill，有依赖还要 pip install | 每 task +10~40s |
| M4 逐轮推理的等待 | 模型在每次轮询间隔都消耗一次推理，或固定 sleep | ×2~3 |
| M5 失败重试与重复派发 | 无幂等键导致重复建单、重复诊断、重复确认 | 不可控 |

结论：单轮 P95 ≤120s 不是靠调优达成的，是靠结构性消灭 M1~M5。

## 二、总原则

> 每轮一个任务，脚本即专家，issue 树即数据库，LLM 只出现在三个固定点。

- **每轮一个任务**：用户一条消息 → 平台恰好创建一个 task（入口 agent 自己的）→ 产出用户可见回复，全程零成员派发。
- **脚本即专家**：诊断/监控/维修专家不再是多个平台 agent，而是编排入口在自己沙箱内调用的 skill 和 provider。产品概念上的"专家"保留（署名、roster 展示），运行时只有一个执行体。
- **issue 树即数据库**：沙箱每 task 销毁，跨轮状态全部落在平台持久的 issue 树上（marker + child issue + 评论载荷），每轮开始用纯函数重建。
- **LLM 三个固定点**：① 意图兜底（确定性路由判不了的少数歧义，强制三选一）；② 事实抽取与答案匹配；③ 回复措辞（状态机出事实包，LLM 说话，脚本验收）。

```text
用户消息
   │
   ▼
平台建 issue + 入口 agent task（唯一 task）
   │
   ▼  第一个工具调用（固定命令）
turn.py ── ① 重建状态（读 issue 树，纯函数）
        ── ② 意图×状态 路由（确定性表）
        ── ③ 执行轮次计划：providers + 领域 skill
        ── ④ 产出 reply_intent（frozen/facts/tone 三层）
   │
   ▼
LLM 起草中文回复 → validate_reply.py 验收 →（失败一次修正→仍失败模板兜底）
   │
   ▼
最终回复 + 隐藏续链 marker → task 终态
```

## 三、轮次生命周期（8 步状态机）

| 步 | 发生什么 | 延迟归属 | 预算 |
|---|---|---|---|
| 1 | 平台建 issue+task、沙箱启动、skill 物化 | 平台固定成本 | 15~30s |
| 2 | LLM 第 1 轮：执行固定命令 turn.py | 1 次推理 | 3~8s |
| 3 | turn.py 重建状态+路由+执行计划 | 脚本 | 0~领域 API 时长 |
| 4 | 若 continue_loop：原样重跑同一条命令 | 每 slice 1 次推理 | 25s/slice |
| 5 | LLM 按 reply_intent 起草回复 | 1~2 次推理 | 5~15s |
| 6 | validate_reply 验收（+至多一次修正） | 脚本 | <1s |
| 7 | 发最终消息 + marker，task 终态 | 平台 | <5s |
| 8 | 长操作通过 /messages 投影进度 | 脚本旁路 | 0 |

每轮 LLM 推理次数硬上限 10 次，slice 硬上限 12 片（5 分钟）。超限不报错，返回 escalate intent：保留状态、如实告知、留给下一轮。

## 四、四个子系统

### 4.1 路由：意图 × 状态的确定性表

状态集合按域换词不换形，例如：`idle / orders_shown / diagnosing(needs_input) / converged / preview_shown / awaiting_exec_confirm / executing / delivered`。

- 表在 `turn.py` 里是数据不是代码分支，新增业务状态先改表和测试。
- 判定用结构信号：上一轮 reply 里的状态标记、选项编号、正则（工单号格式、纯数字）。90% 在表内闭合；判不了才返回 `ask_router`，让 LLM 用固定三选一问用户，路由本身永不自由发挥。

### 4.2 状态库：issue 树重建，纯函数

- **权威源 = 平台原生存储（issue description / artifact），不是回复文本**。回复会经过 LLM 复述、验收器删改、平台截断（实测单条 3000 字截断，复述 672 字符 marker 损坏 2 字符，DW-OBS-146）——任何「下一轮必须读到」的状态，都要能从 issue 树恢复。marker 只做加速通道：解码/校验失败时**静默回退**到 issue 恢复，绝不因此 escalate。
- 根解析：`<!-- squad_root_issue_id:{id} -->` marker + `--resolve-marker`，但 marker 解析失败必须回退到 issue description 扫描（同命名空间标记），两条路径都要有测试。
- 记录：child issue 即记录（title 前缀区分 DIAG/PREVIEW/EXEC），description 内嵌幂等键 marker，结构化载荷放 description 的代码块。
- 重建：`state.py: rebuild(issues, comments) -> ChainState`，纯函数，用黄金 issue 树 fixture 测试，不需要起平台。
- 每个副作用一个幂等键；重复事件/并发触发收敛为同一结果，配并发测试。

### 4.3 专家 → Skill 映射（消灭 M1）

| 现在的"专家" | 变成 | LLM 参与 |
|---|---|---|
| 团长 | entry agent 本体（agents.md 路由表） | 措辞点③ + 路由兜底点① |
| 诊断专家 | `skills/<域>-diagnose/`（图遍历 run.py，被 turn.py 同沙箱调用） | 事实抽取/答案匹配点②，同一会话内做 |
| 监控专家 | `providers/mes.py` + `providers/iot.py` | 零 LLM；IoT 采样可与图遍历并行 |
| 维修专家 | `skills/<域>-maintenance/`（方案渲染 + 回写）+ turn.py 里的确认门 | 仅措辞 |

### 4.4 渲染与错误

- `reply_intent` 三层：`frozen`（marker/选项/门禁句逐字）、`facts`（数字名词逐字）、`tone_context`（说话素材）；LLM 起草，`validate_reply.py` 验收，失败模板兜底。
- 错误三分类：transient（脚本内退避重试一次→wait_retry 保状态）、user_actionable（给编号选项）、terminal（escalate，原始错误进 issue 评论不进用户回复）。

## 五、速度护栏

每类轮次的 P95 目标 ≤120s：

| 轮次类型 | 调度+冷启+物化 | LLM 推理 | 脚本+领域 API | 合计 |
|---|---|---|---|---|
| 查工单表 | ≤30s | 2~3 次 ≈ 15s | API 3~5s | ~50s |
| 选择→诊断（含 IoT） | ≤30s | 4~6 次 ≈ 30s | 诊断 30s + IoT 并行 5s | ~95s |
| 答排查问题 | ≤30s | 3~4 次 ≈ 20s | 图遍历 <1s | ~55s |
| 确认/执行 | ≤30s | 3~4 次 ≈ 20s | 回写 API 3~5s | ~55s |

第一天必须实测两个数：`T_boot`（消息→task start→skill 物化完成）和 `T_infer`（绑定模型单次推理延迟）。若 T_boot >40s，预算重心转向削减 skill 载荷并上报平台。

## 六、十二条硬规则（架构不变量，进 contracts 和 CI）

1. 每用户轮次恰好一个平台 task；禁止派发 LLM 成员（例外门见下）。
2. 第一个工具调用固定为 turn.py；命令逐字固定，占位符仅运行期值。
3. 控制流只在脚本；LLM 只出现在三个固定点。
4. 一切副作用带幂等键；重复/并发事件收敛。
5. 状态 = issue 树纯函数重建；无跨轮沙箱状态。**权威源是 issue description/artifact；回复内 marker 只是加速通道且必须容错回退**（DW-OBS-145/146）。
6. 等待 = 脚本内切片轮询（25s/片，片内 2s），每片至多一次推理。
7. 回复必须过 validate_reply；失败模板兜底，永不裸错误给用户。**凡 emit 进用户回复的隐形 marker 必须在 validator 豁免清单里——emit/exempt 一致性由 `scripts/check_marker_contract.py` 在 CI 强制，不靠自觉**（DW-OBS-143：UUID 禁令打中自家 marker，逼 LLM 删掉唯一状态通道，且组件测试全绿照放不误）。
8. skill 载荷预算：≤15 文件 / ≤200KB / 仅标准库 / 无运行时安装。
9. 每轮推理 ≤10 次、slice ≤12 片；超限 escalate 保状态。
10. 发布判据 = test 环境黄金剧本定时回放（每轮 P95 ≤120s）+ 事件时间线 + 最终用户可见消息；timings 缺失或超标不得宣布完成。
11. **测试必须含往返契约**：查询轮产出 → 渲染 → 验收通过 → 下一轮从恢复路径拿回同一状态。组件各自正确、组合必死（v3 实证：374 个组件级测试全绿，链路 100% 断）。
12. **子进程错误通道协议**：子脚本失败时错误 message 必须落在父进程读取的通道（或父进程两通道都读），并有测试断言父进程拿到真实 message；兜底文案吞根因 = 把所有故障折叠成一种表象（DW-OBS-144）。

## 七、例外门：什么时候才允许派子任务

只有同时满足以下两条才允许 spawn child task：

1. 领域计算确定超过单轮预算（如 >90s 且不可切片，例如大规模报告生成、长时间仿真）；
2. 用户不需要在等待期间交互。

且子任务必须是确定性 skill 任务：一个薄 agent，其 agents.md 只有一句"执行 skill X 的固定命令"，token 消耗趋近于零；父轮用 slice 轮询其终态，artifact 回流。永远不允许自由 LLM 成员加入默认路径。
