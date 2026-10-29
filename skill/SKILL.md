---
name: industrial-agent-builder
description: 设计、生成并验收 DeepWorks AI 同事工作区中的单专家或专家团。用于创建工业 agent、专家团 squad、补齐 turn/run 入口、状态与幂等契约、回复验收和 test 环境发布检查。不适用于普通聊天机器人或与 Agent Teams 无关的业务系统。
---

# DeepWorks 专家/专家团构建

先确认产品要的是**单专家**还是**专家团**。产品没说清时先问，**不得默认单专家**；产品定了形态，就不要为了延迟或实现方便擅自改掉。

## 一、先判形态

| 产品形态 | 生成什么 | 运行边界 |
|---|---|---|
| 单专家 | 一个入口 Agent + 一个主 Skill | 不建团、不派发、不创建 child issue |
| 专家团 | 团长 Agent + 成员 Agent + squad | 团长只编排，成员只执行本角色 |

专家团可以有两种实现：

- **真实成员**：默认。每个成员是独立 Agent，有 instructions、soul、Skill 和角色边界。
- **确定性内联**：可选加速。只有产品允许“专家只是能力投影”时才用。生成器默认生成真实成员；内联需要明确改造并做 test 环境验证。

详细判断读 `references/decision-gates.md`。

## 二、生成骨架

先设置 Skill 根目录（从仓库运行时改为仓库路径）：

```bash
SKILL_HOME="${INDUSTRIAL_AGENT_SKILL_HOME:-$HOME/.agents/skills/industrial-agent-builder}"
```

单专家：

```bash
python3 "$SKILL_HOME/tools/scaffold.py" --mode single --domain <领域> --title <产品名> --target <目录>
```

专家团：

```bash
python3 "$SKILL_HOME/tools/scaffold.py" --mode team --domain <领域> --title <产品名> \
  --roles <角色1>,<角色2>,... \
  --agent-ids <角色1>=<id>,<角色2>=<id> --squad-id <squad-id> --target <目录>
```

ID 还不知道时可以省略；生成后在 `skills/<domain>-orchestrate/config/team.json` 补齐。线上运行时优先读这个文件，其次读环境变量 `DEEPWORKS_AGENT_ID_<ROLE>`。

生成后只改参数和领域实现，不要从散文重写整份入口文件。必读生成物里的：

- `entry/agents.md`
- `skills/.../SKILL.md`
- `deepworks.manifest.json`
- `contracts/`
- `skills/<domain>-orchestrate/config/team.json`（专家团）

## 三、实现顺序

1. 写 `contracts/`：状态、路由、交接字段、幂等键、副作用基数。
2. 写脚本：状态重建、确定性路由、providers、render。
3. 写入口：单专家固定跑 `turn.py`；团长固定跑 `turn.py`；成员固定跑 `run.py`。
4. 写回复：`reply_intent` 分 `frozen / facts / tone_context` 三层，入口必须调用同 Skill 目录下的 `validate_reply.py`。
5. 写测试：黄金输入、幂等并发、状态往返、子进程错误通道。
6. 跑 `scripts/doctor.py`，再进 test 环境新会话验收。

## 四、共同硬规则

- 第一工具调用固定且可逐字复制；禁止全盘搜索、猜路径、先读无关源码。
- 控制流只在脚本；LLM 只做意图兜底、事实抽取/匹配、措辞。
- 权威状态在 issue description/artifact；回复 marker 只是加速通道，坏了静默回退。
- 每个副作用一个幂等键；重复派发、重复确认必须收敛。
- 用户回复必须过验收；失败修正一次，仍失败用模板兜底，永不裸错误。
- 子进程错误必须能被父进程读到真实 message。
- 不依赖跨轮 `/tmp`；Skill 载荷优先控制在 15 个文件 / 200KB 内。
- Skill 目录必须自包含，不能依赖仓库根目录的 `scripts/`。

## 五、专家团额外规则

- 团长不替成员执行专业工作；成员不继续派发成员。
- 一次只派发一个 ready child；并行必须有明确拓扑和收敛测试。
- child 必须带：父 issue、角色、幂等键、验收标准、输入载荷。
- 团长必须消费 child 的结构化结果，并负责最终用户可见回复。
- 成员结果从 child issue 的 `member-result.json` artifact 读取；不靠聊天文本猜，也不把输入 JSON 当结果。
- 成员必须从 child issue 的输入 JSON 取业务载荷，不能把 child 标题当用户请求。
- 平台 Squad Operating Protocol 仍有效；不要用提示词静默对抗平台协议。确需内联时，先记录产品决策并做真实 test 会话验证。
- 发布必须同批更新：团长 instructions、成员 instructions、各 Skill、squad 绑定、runtime 绑定。

## 六、验收

本地：

```bash
python3 -m unittest discover -s tests -v
python3 scripts/doctor.py
python3 scripts/doctor.py --release   # 真实 ID 已填写
```

DeepWorks test：

- 新建会话，不复用旧 session。
- 只挂载 Skill 目录做一次 smoke，确认没有仓库根目录依赖。
- 核对完整事件时间线、最终用户消息、marker、dispatch/artifact/外部 API 次数。
- 单专家首响 P95 ≤120s；专家团首响 P95 ≤120s，完整链按产品 SLA（默认 ≤180s）。
- 缺 timings、marker 为空、重复副作用任一项出现，都不得宣布完成。

完整清单见 `references/checklist.md`。

## 七、性能反馈闭环

本 Skill 与 `session-storyboard` 同仓分发。用 storyboard 分析真实 DeepWorks 会话后，只有满足其 `references/feedback-loop.md` 门槛的通用性能经验才允许反哺到本 Skill。

- 改进本 Skill 前先搜索 `yanlixia/industrial-agent-skill` 上带 `session-storyboard-feedback-id` 的 open issue。
- 每个被采纳的反馈必须落到 `scaffold`、契约、参考文档或测试中的至少一处；只改散文提示词不算消化。
- 同一反馈对应的模板、运行时脚本、doctor / unittest 验收必须同批修改。
- 关闭反馈 issue 时说明落点文件和验证结果；重复指纹的 issue 应标记为 duplicate 并只保留一个处理线。
- 用户明确要求自动反馈时，必须先展示候选清单并取得逐项确认，再调用 `session-storyboard/scripts/feedback_issue.py`。

## 八、产物

一个可落地的仓库骨架，包含：

- `entry/`：团长或单专家 instructions 与 soul。
- `skills/`：唯一入口契约和脚本。
- `experts/`：专家团成员 Agents/Skills。
- `contracts/` + `tests/`：状态、幂等、交接和回归。
- `docs/deploy.md` + `docs/checklist.md`：创建 Agent/squad、发布和新会话验收。
- `deepworks.manifest.json`：生成模式、角色、Skill、squad 绑定。

用户只要方案时，先输出形态判断和架构，不要直接写代码。
