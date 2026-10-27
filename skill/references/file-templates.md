# 文件契约与模板

## 一、单专家仓库

```text
entry/
  agents.md
  soul.md
skills/<domain>/
  SKILL.md
  scripts/
    turn.py
    state.py
    render.py
    domain.py
    runtime.py              # 随 Skill 发布，不能依赖仓库根目录
    validate_reply.py
contracts/
  turn-states.md
  invariants.json
  result.schema.json
  markers.json
tests/
  fixtures/
  test_smoke.py
  test_runtime_contract.py
  test_state_recovery.py
docs/
  deploy.md
  checklist.md
scripts/
  doctor.py
  validate_reply.py
  check_marker_contract.py
  runtime.py
deepworks.manifest.json
```

## 二、专家团仓库

```text
entry/
  agents.md                 # 团长
  soul.md
skills/<domain>-orchestrate/
  SKILL.md
  scripts/
    turn.py                 # 团长唯一入口
    state.py
    dispatch.py
    render.py
    plan.py
    runtime.py
    validate_reply.py
  config/
    team.json               # 线上读取的成员 Agent ID / squad ID
experts/<role>/
  AGENTS.md                 # 成员入口
  soul.md
  skills/<role>/
    SKILL.md
    scripts/
      run.py                # 成员唯一入口
      domain.py
      runtime.py
      validate_reply.py
contracts/
  team-plan.json
  dispatch.schema.json
  markers.json
tests/
  fixtures/
  test_smoke.py
  test_runtime_contract.py
  test_state_recovery.py
  test_team_flow.py
docs/
  deploy.md
  checklist.md
scripts/
  doctor.py
  validate_reply.py
  check_marker_contract.py
  runtime.py
deepworks.manifest.json
```

## 三、入口文件

### 单专家 `entry/agents.md`

```markdown
# {产品名}

## 身份
你是{产品名}，只处理{领域}问题。不建团、不派发、不创建 child issue。

## 唯一第一动作
每个用户轮次，第一个工具调用必须是执行主 Skill 的 turn.py。
命令从 `context/skills.json` 解析，禁止全盘搜索。

## 读结果
- `continue_loop=true` 且 `reply_required=false`：原样重跑同一命令。
- `reply_required=true`：按 reply_intent 渲染唯一用户回复。
- 渲染后必须用同 Skill 的 `validate_reply.py` 验收；失败修正一次，再模板兜底。
- `ok=false`：按 error.class 渲染，不裸报错。

## 输出规则
中文、结论先行、非空；不暴露 JSON、命令、内部路径、UUID、日志。

## 绝对禁止
空回复 / 自行改路由 / 多轮状态放 /tmp / 失败时重复副作用。
```

### 专家团 `entry/agents.md`（团长）

```markdown
# {产品名}团长

## 身份
你是团长，只做编排、派发、状态推进和最终回复。
专业判断由成员完成，你不替成员诊断、分析或回写。

## 唯一第一动作
每个用户轮次，第一个工具调用必须是执行 orchestrate Skill 的 turn.py。
命令从 `context/skills.json` 解析，禁止全盘搜索。

## 派发规则
- 只派发状态机给出的 ready child。
- 一次一个 ready child；并行必须有明确拓扑。
- 每个 child 带 parent、role、幂等键、输入载荷和验收标准。
- child assignee 模式下，不用自然语言 @mention 代替真实指派；若用平台原生 mention，必须同时带 child issue 链接。
- 成员完成后读取结构化结果，再决定下一步或最终回复。

## 最终回复
必须综合成员产物，不能只说“已完成”或“已转交”。

## 绝对禁止
替成员执行 / 成员互相派发 / 重复创建 child / 丢证据链 / 裸错误。
```

### 成员 `experts/<role>/AGENTS.md`

```markdown
# {角色名}专家

## 身份
你只执行{角色名}的 Skill。不派发成员、不改团长状态、不创建 child。

## 唯一第一动作
执行本角色 run.py，输入来自 child issue / context。

## 输出
写 `artifacts/member-result.json` 和必要业务 artifact；失败返回结构化 error。
不要直接给用户发未经团长汇总的最终回复。

## 绝对禁止
继续派发 / 跨角色改写 / 把中间 JSON 当用户回复 / 失败吞根因。
```

## 四、Skill 契约

每个 `SKILL.md` 只写入口契约，不写能力散文：

```markdown
---
name: <domain>
description: 一句话：唯一入口 + 方法 + 产出
version: 0.1.0
---

## 输入
--context task.json / agent.json / previous_tasks.json

## 输出
/tmp/turn.json 或结构化 result.json，字段与 contracts/schemas 对齐。

## 状态依赖
只依赖 issue/artifact；不依赖跨轮 /tmp。

## 幂等键
逐个列出副作用及其键。

## 失败
transient / user_actionable / terminal 三分类。

## 载荷
≤15 文件、≤200KB、仅标准库、无运行时安装。
```

## 五、turn.py CLI

```bash
python3 skills/<domain>/scripts/turn.py \
  --context context/task.json \
  --context context/agent.json \
  --context context/previous_tasks.json \
  [--continue] [--event answer|confirm] \
  --output /tmp/turn.json
```

输出：

```json
{
  "ok": true,
  "continue_loop": false,
  "reply_required": true,
  "reply_intent": {
    "kind": "result",
    "frozen": {"marker": "<!-- squad_root_issue_id:... -->"},
    "facts": {},
    "tone_context": {}
  },
  "timings": {"total_ms": 1200}
}
```

`timings` 每轮必须有。

## 六、成员 run.py CLI

```bash
python3 experts/<role>/skills/<role>/scripts/run.py \
  --context context/task.json \
  --context context/agent.json \
  --output /tmp/result.json
```

输出：

```json
{
  "ok": true,
  "role": "<role>",
  "member_result_key": "<stable-idempotency-key>",
  "result": {"summary": "...", "artifacts": []},
  "reply_intent": {"kind": "result", "frozen": {}, "facts": {}, "tone_context": {}},
  "timings": {"total_ms": 800}
}
```

`run.py` 从 child issue 描述里的输入 JSON 取业务载荷，并把结果写到 `artifacts/member-result.json`；`member_result_key` 让重复执行收敛。团长按 child id / role 找回结果，绝不把输入 JSON 当输出。

## 七、manifest

`deepworks.manifest.json` 是生成物和发布面的索引：

```json
{
  "schema": "industrial-agent/1",
  "mode": "single|team",
  "domain": "energy",
  "title": "能源诊断专家",
  "runtime": "single|leader",
  "leader": {"name": "能源诊断专家", "instructions": "entry/agents.md", "skill": "skills/energy/SKILL.md", "agentId": "<id>"},
  "members": [
    {"role": "diagnosis", "runtime": "member", "instructions": "experts/diagnosis/AGENTS.md", "skill": "experts/diagnosis/skills/diagnosis/SKILL.md", "agentId": "<id>"}
  ],
  "skills": ["skills/energy/SKILL.md"],
  "squad": {"id": "<squad-id>", "name": "能源诊断专家团", "leaderRole": "leader", "memberRoles": ["diagnosis"]}
}
```

`deepworks.manifest.json` 是仓库里的发布索引，不是线上运行配置。专家团线上先读
`skills/<domain>-orchestrate/config/team.json`，再读环境变量
`DEEPWORKS_AGENT_ID_<ROLE>`；两者都没有就明确失败，不猜。

## 八、契约层

- `turn-states.md`：状态、允许迁移、用户可见动作。
- `invariants.json`：副作用基数（例如一个诊断一个预览、一次确认一次回写）。
- `dispatch.schema.json`：团队 child 的必填字段。
- `result.schema.json`：成员回传结构化结果的字段。
- `markers.json`：回复层文件、续链 marker 命名空间。
- 新增状态、child、公开命令或副作用前，先改契约和测试。

## 九、测试最小集

- `test_smoke.py`：入口能跑，输出 JSON，有 timings，回复能过验收。
- `test_runtime_contract.py`：幂等键、回复 marker、子进程真实错误。
- `test_team_flow.py`：串行 plan、等待 active member、全成员完成后汇总。
- `doctor.py`：把单个 Skill 复制到临时目录运行，检查线上自包含；`--release` 检查真实 ID。
- `state_roundtrip.py`：产出 -> 渲染 -> 验收 -> 下一轮恢复（脚本位于 `scripts/`）。

## 十、发布面

同批发布：

- 团长/成员 instructions
- 所有 Skill 版本
- squad 绑定
- runtime 绑定
- release manifest
