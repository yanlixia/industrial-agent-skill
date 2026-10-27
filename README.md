# industrial-agent-skill

**通用工业智能体构建工具包**：一套经过真实工业链路验证（DeepWorks Agent Teams + 设备运维 v3）的单执行体方法论、脚手架和机器强制工具，让新工业智能体从第一天就满足**每轮会话 P95 ≤120s**，并且不重蹈四类已实证的断链 bug。

一条命令安装：

```bash
curl -fsSL https://raw.githubusercontent.com/yanlixia/industrial-agent-skill/main/install.sh | bash
```

或克隆后 `bash install.sh`。纯 Python 标准库 + bash，零依赖、零运行时安装。

## 这解决什么问题

工业场景的「专家团」常见形态是三四个 LLM agent 互相派发，单轮延迟动辄数分钟到一小时，且跨轮状态靠聊天文本传递——v3 设备运维链的实测把它拆成了五个延迟乘数和四类断链缺陷（详见 [docs/PITFALLS.md](docs/PITFALLS.md)）。本仓库把这些教训固化成三层：

| 层 | 内容 | 防什么 |
|---|---|---|
| **方法论**（skill/）| 决策门（大多数团该降级）、三档运行时、十二条硬规则、延迟预算表 | 小时级延迟（M1~M5 乘数） |
| **脚手架**（scaffold/）| agents.md/soul.md/SKILL.md 模板 + 已验证的 validate_reply.py | 从散文重写引入的 bug |
| **机器强制**（tools/）| marker 契约 lint、状态往返契约 harness、回放探针 | 「组件测试全绿、链路 100% 断」 |

## 仓库结构

```text
skill/                     # 安装进 agent CLI 的技能本体
  SKILL.md                 # 入口：工作流五步 + 判断红线
  references/              # architecture / decision-gates / file-templates
  assets/scaffold/         # 新智能体仓库骨架（含状态通道铁律 README）
tools/                     # 独立 CI/命令行工具（不依赖 skill 安装）
  check_marker_contract.py # CI 门：emit↔exempt 一致性（防 UUID 禁令打中自家 marker）
  validate_reply.py        # 回复验收门（含续链 marker 豁免）
  state_roundtrip.py       # 往返契约 harness：R1 正常 / R2 marker 损坏回退 / R3 禁令仍生效
  remote_probe.py          # 远端回放探针：分段计时 + 结构化 JSON + 台账行
docs/
  PITFALLS.md              # 四类实证坑的完整因果链与规避
examples/                  # （占位）首个真实生成物将放在这里
install.sh
```

## 快速开始（三步）

**1. 过决策门**（最重要的一步，大多数「专家团」在这一步被降级成 workflow 或单专家）：

读 `skill/references/decision-gates.md`，回答：流程是否完全确定？判断点是否跨 ≥2 个领域？有没有人工门禁？——能降级就降级，这是最大的一笔延迟收益。

**2. 生成骨架**：

```bash
cp -R ~/.agents/skills/industrial-agent-builder/assets/scaffold my-agent && cd my-agent
# 全局替换 DOMAIN -> 你的领域（如 energy），补全事件表/幂等键/输出契约
```

**3. 把机器强制接进 CI**：

```bash
python3 tools/check_marker_contract.py .            # emit↔exempt 一致性
python3 tools/state_roundtrip.py                    # harness 自检（接入你的管线见文件头）
cp tools/check_marker_contract.py my-agent/scripts/ # 或放仓库内
```

## 核心铁律（为什么是这四条）

1. **状态权威源 = issue description / artifact，不是回复文本。** 回复会被 LLM 抄错（实测 672 字符 marker 抄坏 2 处）、被验收器删、被平台截断（单条 3000 字）。marker 只做加速通道，损坏时静默回退。
2. **凡写进用户回复的隐形 marker，必须在验收器豁免清单。** 由 `check_marker_contract.py` 在 CI 强制——.UUID 禁令和「逐字复制 marker」两条规则天然矛盾，靠人记必然漏。
3. **测试必须含往返契约。** v3 实证：374 个组件级测试全绿，跨轮链路 100% 断。`state_roundtrip.py` 把「产出→渲染→验收→恢复」整条链当一个测试走。
4. **子进程错误必须落在父进程读的通道。** 兜底文案吞根因 = 所有故障折叠成同一句 escalate，排查期等于盲人。

完整十二条见 `skill/references/architecture.md`。

## 环境前提

- 任何支持 `skills/` 目录约定的 agent CLI（Claude Code / Codex / CodeBuddy 等）
- Python 3.8+（工具全部标准库）
- `remote_probe.py` 需要能访问 DeepWorks 桌面端调试端口（token 自动桥接，路径参数化）；不用 DeepWorks 时探针可整体不用

## 已验证与未验证（诚实声明）

- **已验证**：方法论与数字来自设备运维 v3 真实链路（2026-10 实测每轮 41~96s < 120s 目标；四类坑均有 DW-OBS 编号证据链）；工具三向测试（v3 修复后 ok / 模拟回退状态红 / 自身自检过）
- **未验证**：跨部门场景（单轮多人工门禁、超长报告生成）未经过第二个团队使用。首批使用者请把摩擦反馈回来，这是版本演进的输入

## License

MIT
