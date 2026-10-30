# industrial-agent-skill

**DeepWorks AI 同事工作区的专家/专家团构建 Skill。**

目标不是“写一段提示词”，而是让一个专家或一支专家团在 DeepWorks 里可生成、可运行、可验收、可发布。

## 两种产品形态

| 产品形态 | 生成什么 | 运行边界 |
|---|---|---|
| 单专家 | 一个 Agent + 一个主 Skill | 不建团、不派发、不创建 child issue |
| 专家团 | 团长 Agent + 成员 Agent + squad | 团长只编排；成员只执行本角色；默认串行派发 ready child |

产品定了专家团，就生成真实团长和成员 Agent，不为了性能偷偷降级成单专家。确定性内联只作为产品明确接受的优化，不是默认架构。

## 快速开始

```bash
git clone https://github.com/yanlixia/industrial-agent-skill.git
cd industrial-agent-skill
bash install.sh
```

安装后两个 Skill 位于：

- `~/.agents/skills/industrial-agent-builder/`
- `~/.agents/skills/session-storyboard/`

`session-storyboard` 用于还原真实会话、定位耗时和副作用证据；它发现的跨领域性能经验可以在用户逐项确认后反哺本仓库。

### 单专家

```bash
python3 ~/.agents/skills/industrial-agent-builder/tools/scaffold.py \
  --mode single --domain energy --title 能源诊断专家 --target ./energy-expert
```

### 专家团

```bash
python3 ~/.agents/skills/industrial-agent-builder/tools/scaffold.py \
  --mode team --domain energy --title 能源诊断专家团 \
  --roles diagnosis,maintenance,monitor \
  --leader-agent-id <leader-id> \
  --agent-ids diagnosis=<id>,maintenance=<id>,monitor=<id> \
  --squad-id <squad-id> --target ./energy-team
```

Agent/squad 还没创建时可以先省略 ID，生成后在 `skills/<domain>-orchestrate/config/team.json` 补齐。

生成后先跑本地门禁：

```bash
cd energy-team
python3 scripts/doctor.py
python3 -m unittest discover -s tests -v
python3 scripts/doctor.py --release   # 填完真实 ID 后执行
```

然后在 DeepWorks test 环境新建会话验收。旧会话或单测全绿都不算发布完成。

## 生成物

- `entry/`：单专家或团长 instructions 与 soul。
- `skills/`：入口契约、`turn.py`，以及随 Skill 一起发布的 `runtime.py` / `validate_reply.py`。
- `experts/<role>/`：专家团成员的 Agent、soul 和 `run.py`。
- `contracts/`：状态、结果、派发、marker 和副作用基数。
- `tests/`：smoke、团队串行计划、幂等、子进程错误、回复验收。
- `scripts/`：`doctor.py`、`runtime.py`、`validate_reply.py`、`check_marker_contract.py`。
- `skills/<domain>-orchestrate/config/team.json`：线上 Skill 真正读取的成员 Agent ID / squad ID。
- `deepworks.manifest.json`：形态、领域、角色、Skill、squad 绑定和 Agent ID 的发布索引。

发布到 DeepWorks 时通常只挂载 Skill 目录，所以每个 Skill 必须自包含；生成器已经复制 runtime 与回复验收脚本。`deepworks.manifest.json` 是仓库侧发布索引，不要指望线上 worker 能读到它。

生成物是**可跑通的骨架**。领域 provider、真实 Agent ID、squad 绑定和业务事实仍需实现。

## 共同铁律

1. **先定产品形态，再定实现。**
2. **控制流在脚本，LLM 只做意图兜底、事实抽取和措辞。**
3. **权威状态在 issue/artifact，回复 marker 只是加速通道。**
4. **每个副作用有幂等键，重复事件收敛为同一结果。**
5. **用户回复必须过 `validate_reply.py`，不允许裸 JSON、命令、路径、UUID。**
6. **专家团团长只派发和汇总；成员只执行本角色，不继续派发。**
7. **Skill 目录必须自包含；不能依赖仓库根目录的脚本。**

## 质量门

```bash
python3 tools/scaffold.py --help              # 生成器可用
python3 tools/state_roundtrip.py              # 状态往返自检
python3 tools/check_marker_contract.py .      # marker emit/exempt 一致
python3 -m unittest discover -s tests -v      # 生成物验收
```

生成物门禁里的 `doctor.py` 会把入口 Skill 单独复制到临时目录再执行，专门检查线上“只挂载 Skill 目录”的布局。

专家团发布前还必须抓取 Test 控制面的 `/api/agents` 与 `/api/runtimes`，跑成员 runtime/Skill 绑定门禁：

```bash
python3 scripts/doctor.py --agents-json agents.json --runtimes-json runtimes.json --squad-json squad.json
```

要求四个成员都在当前 squad，runtime 均为 `managed_sandbox` + `online`，各成员 Skill 已绑定到对应成员 Agent，且团长派发 env 与 squad 当前 Agent ID 一致；任一不满足即失败。

正式发布还必须在 test 环境记录：事件时间线、原生专家调度卡/`task-runs`、dispatch/artifact/API 次数、最终用户消息、每轮 `timings` 和 P95。

## 性能反馈闭环

1. 用 `session-storyboard` 生成 `session-trace.json` 和 HTML 故事板。
2. 只筛选跨领域、有 trace 证据、能落成模板/契约/测试的通用优化候选。
3. 一次性向用户展示候选，用户逐项选择。
4. 对每个选中候选运行：

```bash
python3 ~/.agents/skills/session-storyboard/scripts/feedback_issue.py \
  --candidate <candidate.json> \
  --trace <session-trace.json>
```

5. 脚本生成稳定幂等指纹，查重后在本仓库创建 `[storyboard]` issue。
6. 后续改进 `industrial-agent-builder` 时优先消化这些 issue，并同批更新模板、契约或测试。

无 `gh`、未登录或用户未逐项确认时不创建 issue。完整规则见 `session-storyboard/references/feedback-loop.md`。

## 仓库结构

```text
skill/SKILL.md                  # Skill 入口：判形态 -> 生成 -> 实现 -> 验收
skill/references/               # 决策、架构、DeepWorks、沙箱运行时、文件契约、清单
skill/assets/scaffold/templates # single / team / common 模板
session-storyboard/             # 会话性能分析与反馈闭环 Skill
tools/scaffold.py               # 单专家/专家团生成器
tools/runtime.py                # 生成物共用的 DeepWorks runtime
tools/validate_reply.py         # 回复验收门
tools/check_marker_contract.py  # marker 一致性 lint
tools/state_roundtrip.py        # 状态往返契约 harness
tools/remote_probe.py           # test 环境回放探针
tools/task_timeline.py          # 用 /task-runs JSON 审计 claim/启动/执行耗时
tools/perf_sampler.py           # 批量新会话采集多轮延迟样本，产出 P95 证据
docs/PITFALLS.md                # 已实证坑与规避
tests/                          # 本 Skill 的生成物验收
```

## 适用边界

适用于 DeepWorks AI 同事工作区中的专家、专家团和 Agent Teams squad。
不适用于普通聊天机器人、与 issue/artifact 无关的独立系统。`deepsense-agent-kit` 的 `@deepsense + CLI` 发包是另一层，不与本 Skill 的 Agent Teams 形态混用。

当前交付以本地 smoke、生成物 doctor 和 v3 经验回灌为已验证范围；真实 DeepWorks 派发必须在 test 环境新建会话复核。
