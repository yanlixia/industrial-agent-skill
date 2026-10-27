# DeepWorks 落地约定

## 一、运行时上下文

入口 Agent 每个 task 通常能拿到：

```text
context/task.json
context/agent.json
context/previous_tasks.json
context/skills.json
context/prior_artifacts.json
```

Skill 可以声明自己的路径。不要假设 Skill 一定在当前目录；按 `context/skills.json` 解析，并保留固定 fallback 链。

真实 `context/task.json` 的 `input` 不是纯用户原话，常见形式有：

```text
ISS-2809: 查看维修工单

DeepWorks chat squad run.

User request:
查看维修工单
```

```text
Original issue task:
ISS-2809: 查看维修工单

[SQUAD MEMBER RESULT]
Treat this squad member's output as evidence for the original issue...
```

```text
ISS-2: [diagnosis] diagnosis 任务

角色：diagnosis
{"request":"处理设备故障","completed_roles":[]}

<!-- team_root_issue_id:... -->
```

所以不要直接取 `task.input` 第一行当业务请求。团长优先解析 `Current user request:` / `User request:` / `Original issue task:`；成员从 child issue 描述里的输入 JSON 取 `request`。

发布时通常只挂载 Skill 目录。`runtime.py`、`validate_reply.py` 等运行依赖必须放在 Skill 自己的 `scripts/` 下，不能依赖仓库根目录。

## 二、入口命令

不要在 `agents.md` 里写 `$SKILL_PATH` 这种平台不一定注入的变量。推荐写一个固定 Python bootstrap：

```python
import json, runpy, sys
from pathlib import Path

with Path("context/skills.json").open(encoding="utf-8") as f:
    payload = json.load(f)

def find(value):
    if isinstance(value, dict):
        if value.get("name") == "<skill-name>":
            return str(value.get("path") or "")
        for item in value.values():
            found = find(item)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = find(item)
            if found:
                return found
    return ""

base = Path(find(payload))
Path("/tmp/agent_skill_path").write_text(str(base), encoding="utf-8")
candidates = [
    base / "scripts" / "turn.py",
    Path("skills/<skill-name>/scripts/turn.py").resolve(),
]
script = next((p for p in candidates if p.is_file()), None)
if script is None:
    raise SystemExit("<skill-name> turn.py not found")
sys.argv = [
    "turn.py",
    "--context", "context/task.json",
    "--context", "context/agent.json",
    "--context", "context/previous_tasks.json",
    "--output", "/tmp/turn.json",
]
runpy.run_path(str(script), run_name="__main__")
```

关键点：

- 只用声明路径 + 固定候选，不 `find` 全盘。
- 找不到就结构化失败，不猜。
- 第一条命令就执行，不先读源码、帮助或无关文件。
- 渲染回复后，用 `base/scripts/validate_reply.py` 验收；不要依赖仓库根目录。

## 三、平台 CLI

优先使用 worker 内可执行的 `deepexi`：

```bash
deepexi issue create --title ... --description ... --parent <root_issue_id> \
  --assignee-type agent --assignee-id <member_agent_id> --output json
```

其他常用：

```bash
deepexi issue status <issue-id> in_review --output json
deepexi issue comment add <issue-id> --content-file <path> --output json
deepexi issue comment list <issue-id> --output json
deepexi artifact list <issue-id> --output json
deepexi artifact download <artifact-id> --output-file <path>
deepexi squad activity <issue-id> action --reason "..." --output json
```

CLI 路径解析顺序：

1. `--deepexi-bin` / `DEEPEXI_BIN`
2. `/workspace/bin/deepexi`
3. `/app/bin/deepexi`
4. `/opt/deepexi/bin/deepexi`
5. `shutil.which("deepexi")`

找不到就返回可操作错误，不要遍历目录。

## 四、状态与 marker

共同 marker：

```html
<!-- squad_root_issue_id:{root_id} -->
```

团队续跑：

```html
<!-- squad_leader_continuation:member_result -->
```

领域状态 marker 必须注册到 `contracts/markers.json`，并加入 `validate_reply.py` 的续链豁免。

规则：

- marker 只做加速通道。
- marker 损坏/缺失时从 issue description、child issue、artifact 恢复。
- 只有所有恢复通道都失败时才 escalate。
- 回复长度要控制，平台历史上单条消息会被截断。

## 五、专家团

### 创建顺序

1. 创建每个成员 Agent，绑定各自 Skill。
2. 创建团长 Agent，绑定 orchestrate Skill。
3. 创建 squad，leader = 团长，members = 成员。
4. 记录所有 remote ID 到 release manifest。
5. 新建 test 会话，完整跑一遍。

### 派发

真实成员默认走**确定性 child issue 派发**：

1. 团长 `turn.py` 生成 ready child。
2. `deepexi issue create --parent ... --assignee-type agent --assignee-id ...` 创建并指派。
3. 成员 `run.py` 在 child issue 回复结构化 JSON。
4. 团长重新触发时读取 child 状态和 `member-result.json` artifact，再推进下一步。
5. 全部角色完成后汇总最终回复，并调用 `deepexi squad activity` 记录本轮评估。

平台 Squad Operating Protocol 仍然存在。若采用原生 @mention 派发，必须在同一条 mention 里带上 child issue id/链接；不要同一轮同时使用两套派发方式。v3 已验证 child assignee 方案可用，但每次新租户/新版本仍要在 test 会话复核。

一个 child 一个幂等键；重复完成事件不能重复建下一步。团长最终回复必须综合成员结果。

成员读到的是 child issue 的 `input`：标题 + description。description 里包含 leader 写入的输入 JSON、`team_root_issue_id`、`team_role` 和 `role_dispatch_key`。成员必须解析 JSON 里的 `request`，不能把标题当请求。

成员结果默认只走一条机器通道：`run.py` 写 `artifacts/member-result.json`，平台随 task 完成发布。团长读 artifact 获取 `ok`、`role`、`result.summary`、`facts`；平台自动生成的可见 comment 只做摘要，不当作结构化事实源。child 描述里的输入 JSON 永远不是结果。

兼容自研成员时，也允许 agent comment 携带 `member_result_key` 作为备用通道；没有该键的 comment 一律不消费。

### 运行配置

团长 Skill 读取 `config/team.json` 解析成员 Agent ID：

```json
{
  "schema": "industrial-agent/team-runtime/1",
  "squadId": "<squad-id>",
  "members": [
    {"role": "diagnosis", "agentId": "<agent-id>"}
  ]
}
```

也支持环境变量 `DEEPWORKS_AGENT_ID_<ROLE>`。两者都没有时明确失败，不猜、不回退到单专家。

### 发布面

同批发布：

- 团长 instructions
- 成员 instructions
- 每个 Skill
- squad 绑定
- runtime 绑定
- release manifest

只发布其中一层，不算完成。

## 六、test 验收

必须新建会话，记录：

- session / task / child issue id
- claim/start/execution/total 时间
- dispatch 次数
- artifact 数量
- 外部 API 调用次数
- marker 是否非空
- 最终用户可见消息
- 错误分类和 failure code

通过标准：

- 单专家首响 P95 ≤120s。
- 专家团首响 P95 ≤120s；完整链按产品 SLA，默认 ≤180s。
- 重复事件、重复确认、并发派发不产生重复副作用。
- marker 丢失可静默恢复。
- 没有空回复、裸错误、内部路径/UUID 泄漏。

## 七、常见平台坑

- 把跨轮状态藏在回复文本里，模型复述会抄坏。
- 验证器禁止 UUID，但续链 marker 本身含 UUID；必须显式豁免。
- child 完成事件重复到达，未做幂等会重复派发。
- 子进程错误写 stdout，父进程只读 stderr，最后只剩兜底文案。
- runtime 长期 QUEUED 不一定是容量问题；先检查是否被旧 sandbox run 锁死。
- Skill 依赖仓库根目录脚本，线上只挂载 Skill 目录时直接 `ModuleNotFoundError`。
- 成员把 child 标题当业务请求，真实输入 JSON 没被消费。
- 只改仓库里的 manifest，却没改 Skill 内的 `config/team.json`，线上找不到成员 Agent ID。
- test 环境通过不等于生产可用；发布后仍要用新会话复验。
