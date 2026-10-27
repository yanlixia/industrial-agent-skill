# 两种形态的参考架构

## 一、总览

### 单专家

```text
用户消息
  -> 平台创建 issue + task
  -> 单专家第一个动作：turn.py
  -> 重建状态 / 路由 / 调 provider
  -> reply_intent
  -> LLM 措辞 -> validate_reply -> 最终回复
```

没有 squad、没有 child issue、没有成员派发。

### 专家团

```text
用户消息
  -> 平台创建 issue + 团长 task
  -> 团长第一个动作：turn.py
  -> 读 squad / issue 树 / 计划表
  -> 派发一个 ready child 给成员 Agent
  -> 成员 task 执行 run.py
  -> 成员发布 member-result.json artifact，child 完成
  -> 团长继续编排或最终汇总
```

成员是真实 Agent 时，就走平台原生 child issue + assignee 派发。只有产品接受“成员只是能力投影”时，才改为团长沙箱内确定性内联。

## 二、共同执行模型

### 1. 每轮只有一个入口

- 单专家：`turn.py`
- 团长：`turn.py`
- 成员：`run.py`

入口必须固定、可逐字复制，不接受“模型自己找脚本”。`agents.md` 里的固定命令只允许替换运行期值。

### 2. 控制流只在脚本

LLM 只出现在三个固定点：

1. 意图兜底：确定性路由判不了时，用固定选项问用户。
2. 事实抽取/答案匹配：从输入里取结构化事实。
3. 回复措辞：把 `reply_intent` 说成人话。

派发、状态迁移、幂等、副作用、最终动作都由脚本决定。

### 3. 状态以 issue/artifact 为准

- 权威状态：issue description、child issue、comment payload、artifact。
- 回复 marker：只做加速通道。
- marker 损坏/缺失：静默回退 issue/artifact，不向用户报错，不要求重选。
- 不依赖跨轮 `/tmp`。
- Skill 目录自包含：`runtime.py`、`validate_reply.py` 放在 Skill 自己的 `scripts/` 下。

### 4. 回复三层

```json
{
  "kind": "needs_input | progress | result | preview | final | wait_retry | error",
  "frozen": {"marker": "...", "options": [], "phrases": []},
  "facts": {"业务字段": "..."},
  "tone_context": {"user_last_said": "...", "already_asked": []}
}
```

- `frozen`：marker、选项编号/文案、门禁句，逐字保留。
- `facts`：数字、编号、结构化结果，逐字保留。
- `tone_context`：给 LLM 的说话素材，可以重组但不得新增事实。

回复必须过 `validate_reply.py`；失败修正一次，仍失败用模板兜底。

## 三、专家团派发契约

### 团长

- 只做路由、派发、状态推进、最终用户回复。
- 不替成员做专业判断。
- 一次只派发一个 ready child；并行必须有明确拓扑和收敛测试。
- 派发前检查幂等键，重复事件不得产生等价 child。
- 成员完成后读取结构化结果，不靠聊天文本猜结果。
- 成员把结果 JSON 写到任务目录的 `artifacts/member-result.json`；团长在 child 完成后读取 artifact。
- 成员从 child issue 输入 JSON 取业务载荷，不把标题当 request。
- 最终回复必须综合成员产物，不能只说“已完成”。

### 成员

- 只执行本角色 Skill，不继续派发成员。
- 输入来自 child issue description 或显式 context，不依赖团长沙箱。
- 输出 `artifacts/member-result.json` + 必要业务 artifact。
- 失败必须返回结构化错误，父进程能读到真实 message。

### child issue 最小字段

```text
parent_issue_id
role
idempotency_key
input_payload
acceptance_criteria
deadline_seconds
```

description 里建议带：

```html
<!-- team_root_issue_id:{root_id} -->
<!-- team_role:{role} -->
<!-- role_dispatch_key:{key} -->
```

## 四、幂等与并发

每个副作用必须有业务幂等键，例如：

```text
{root_issue_id}|{role}|{business_id}|{operation}
```

必须测试：

- 同一用户消息重复投递。
- 同一 child 完成事件重复到达。
- 用户重复确认。
- 两个并发 task 同时尝试派发同一个 child。

期望结果：只产生一个业务副作用，后续事件收敛为同一结果。

## 五、错误分类

### transient

- 网络抖动、瞬时 401/429、临时超时。
- 脚本内退避重试一次。
- 仍失败返回 `wait_retry`，保留状态。

### user_actionable

- 用户输入无效、缺少必要参数。
- 返回编号选项，不暴露内部细节。

### terminal

- 配置、权限、数据契约、不可恢复外部错误。
- 返回 `escalate`，原始错误写 issue comment/audit。
- 用户只看到致歉、失败位置、状态是否保留、下一步建议。

子进程失败时，父进程必须优先读 stderr；stderr 为空再解析 stdout 的 error JSON；两者都没有才用兜底文案。

## 六、性能与载荷

- 单专家首响 P95 ≤120s。
- 专家团首响 P95 ≤120s；完整链默认 ≤180s，按产品 SLA。
- 轮询在脚本内切片：默认 25s/片，片内 2s；每片最多一次推理。
- 每轮推理和 slice 设硬上限；超限 escalate 保状态。
- Skill 目标：≤15 文件 / ≤200KB / 仅标准库 / 无运行时安装。
- 大资源走 provider API 和任务内缓存，不塞进 Skill。

## 七、DeepWorks 契约

- 入口 Agent 的 context：`context/task.json`、`context/agent.json`、`context/previous_tasks.json`。
- Skill 声明：`context/skills.json`。
- 平台 CLI：优先使用 worker 内可执行的 `deepexi`。
- 团队运行配置：团长 Skill 的 `config/team.json`，或 `DEEPWORKS_AGENT_ID_<ROLE>`。
- 成员派发：`deepexi issue create --parent ... --assignee-type agent --assignee-id ...`。
- 子任务完成：父 issue 收到 child 结果/事件；团长用 `turn.py` 重建状态。
- 发布面：团长 instructions、成员 instructions、各 Skill、squad 绑定、runtime 绑定必须同批。
- test 验收：新会话 + 完整事件时间线 + 最终用户消息 + 计数指标。

## 八、反模式

- 为了让链路更快，直接把产品定义的团降成单专家。
- 团长只发自然语言 @mention，既不建 child，也不带 child 链接，靠成员猜任务。
- 成员之间互相 @mention，形成派发环。
- 把成员 JSON 直接发成用户可见 comment，或把 child 描述里的输入载荷误当成员结果。
- 把跨轮状态只塞进回复 marker。
- 组件测试全绿，但没有“团长产出 -> 派发 -> 成员返回 -> 团长恢复”的链路测试。
- 用兜底文案覆盖子进程真实错误。
- 只更新 Skill，不更新 Agent instructions、squad 绑定和 runtime。
- 本地跑通但把运行依赖留在 Skill 目录外，线上只挂载 Skill 时直接崩。
