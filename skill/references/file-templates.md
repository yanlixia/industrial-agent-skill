# 文件级模板：每个东西写什么

## 一、模板仓库结构

```text
industrial-agent-template/
├── entry/
│   ├── agents.md          # ≤120 行，五段式骨架
│   └── soul.md            # ≤30 行，人格与价值观
├── skills/
│   ├── <域>-orchestrate/
│   │   ├── SKILL.md       # 入口契约
│   │   └── scripts/
│   │       ├── turn.py        # 每轮唯一入口
│   │       ├── state.py       # issue 树重建，纯函数
│   │       ├── providers/     # MES/IOT 等外部 API 客户端，可 mock
│   │       ├── render.py      # reply_intent 组装
│   │       └── validate_reply.py
│   └── <域>-diagnose/         # 领域图遍历 skill
├── contracts/
│   ├── turn-states.md     # 轮次状态机全表
│   ├── invariants.json    # 基数约束
│   └── schemas/           # 交接 JSON Schema
└── tests/                 # 路由全矩阵/幂等并发/黄金 issue 树
```

## 二、团长 agents.md——≤120 行，五段式

```markdown
# {业务}专家团长

## 角色（1 句）
你是团长，只做调度、衔接和用户可见回复。不诊断、不改根因、不改方案、不编造结果。

## 路由 0：唯一第一动作
每个用户轮次，第一个工具调用必须是执行 turn.py（附固定命令，含路径解析 fallback）。
- enabled:true → 本轮禁止自行建 issue/派发/mention；只按 JSON 行动
- continue_loop=true 且 reply_required=false → 原样重复上一条命令，禁止输出"正在处理"
- reply_required=true → JSON 的 reply 原样作为唯一用户可见回复，禁止追加任何内容

## 事件表（全部业务分支一张表，每行一个固定命令）
| 触发 | 命令 | 终态语义 |
|---|---|---|
| 初始查询轮 | turn.py --context ... | 渲染工单表+marker |
| 选择/continuation | turn.py --continue --slice-seconds 25 | continue/reply 二态 |
| 用户答排查问题 | turn.py --event answer ... | 只回 JSON.reply |
| 用户确认 | turn.py --event confirm | 轮询到 done 再 relay_final |

## 输出规则
非空中文；禁 JSON/命令/日志/内部路径/UUID；中间文件输出 /tmp；
wait 到达立即回"处理中"结束本轮（选择轮除外）。

## 绝对禁止
空回复 / 转发执行流 / 未呈现预审就接受确认 / 确认后重新诊断 /
丢 confidence·证据链 / 最终回复只给工单不给结论。
```

写法纪律：

- 过程写进 SKILL.md（按需加载），不写进 agents.md（每轮全量进上下文）。
- 命令必须逐字可粘贴，占位符只允许 `{当前issue_id}` 这类运行期值。
- 凡是"由模型决定下一步"的措辞都是漏洞；每条"禁止"尽量转成脚本里的硬约束。

## 三、soul.md——≤30 行

只放人格与价值观，照四段结构换内容：我是谁 / 我信什么 / 我怎么说话 / 我绝不做的事。不要往 soul 里塞流程。

## 四、专家 agents.md——≤100 行，六段式

以诊断专家为模板：

```markdown
# {领域}专家

## 续跑路由（最高优先级）
description 含「用户答案：」→ 只执行 run.py resume --issue-file，
禁止 start、禁止重复提问、禁止重建 data_query。

## 最重要的规则
脚本 stdout 的 JSON 是中间数据，绝不直接展示；必须解析后用中文回复。

## 角色（1 句，写清判断边界）
沿本体图做确定性遍历，LLM 只做入口事实抽取和答案匹配。

## 唯一 Skill 与路径解析
固定解析片段 + 禁止全盘搜索。

## 执行流程（每个分支一条固定命令）
start → needs_input → needs_iot_data → resume/consume → confirmed

## 输出契约
必须字段与 contracts/schemas 一字不差。
```

## 五、SKILL.md 头部固定格式

每个 Skill 就是一个命令行入口契约，不是能力描述文章：

```markdown
---
name: <域>-orchestrate
description: 一句话：唯一入口 + 方法 + 产出
version: 0.1.0
---
## 输入：--context 文件清单与语义
## 输出契约：/tmp/turn.json 字段表（与 contracts/schemas 对齐）
## 状态依赖：只依赖 issue 树，不依赖 /tmp 跨轮文件
## 幂等键定义：逐个列出
## 载荷预算：≤15 个文件、≤200KB、仅标准库、无运行时安装
```

`scripts/` 固定五类结构：

1. `run.py`/`turn.py`：唯一入口，子命令分派。
2. 领域核心（如 `graph_traversal.py`）：纯函数，可单测。
3. 外部客户端（`foil_client.py`/`mes_client.py`）：全部可 mock。
4. `report_renderer.py`：HTML 产物。
5. `daemon_artifacts.py`：平台附件登记。

必带（scaffold 已含，从模板复制后改域参数，不要从散文重写）：

- `validate_reply.py`：验收门，含隐形 marker 豁免正则（emit/exempt 一致性由 CI 检查）。
- `scripts/check_marker_contract.py`（仓库根 scripts/）：CI 步骤，扫回层文件 emit 的 marker 命名空间断言全部豁免；新增 marker 命名空间忘记豁免时红。
- 状态往返测试模板（tests/）：

```python
def test_roundtrip_state_survives_reply_pipeline():
    """往返契约：查询轮产出 → 渲染 → 验收 → 下一轮恢复同一状态。

    组件各自正确、组合必死的实证防线（v3 DW-OBS-143：374 个组件测试
    全绿，链路 100% 断）。任何一层改动都必须重跑本测试。
    """
    turn_out = turn.run(ctx, root="", slice_seconds=25)        # 查询轮
    draft = render_draft(turn_out.reply_intent)                 # LLM 起草位（mock）
    verdict = validate(turn_out.reply_intent, draft)
    assert verdict["ok"], verdict["violations"]                 # marker 存活
    assert draft 不含损坏 marker（若走 mock LLM，直接用 intent.frozen）
    restored = state.rebuild_from_reply(draft, issues=fixture)  # 下一轮恢复
    assert restored.root_issue_id == turn_out.root_issue_id
    # marker 被删/损坏的降级路径：必须从 issue description 恢复，不 escalate
    corrupted = draft.replace(marker_payload, "xx损坏xx")
    restored2 = state.rebuild_from_reply(corrupted, issues=fixture)
    assert restored2.root_issue_id == turn_out.root_issue_id

def test_child_process_error_reaches_parent():
    """子进程错误通道：父进程拿到的错误文本必须含子进程真实 message。"""
```

## 六 bis、探针与延迟台账（模板仓库结构追加）

```text
scripts/remote_probe.py     # 发布后回放探针：参数化发消息+分段计时+结构化 JSON
docs/ledger-latency.md      # 追加式台账：日期/agent/轮次/claim/start/执行/总/marker/终态，一行一轮
```

探针约束：纯标准库；token 桥接路径做成参数/环境变量，缺失时报可操作错误；测试 mock HTTP 层，绝不真实调平台。回放不是可选项——**没有探针和台账的仓库，P95 ≤120s 目标无法证伪**。

## 六、turn.py CLI 契约

```bash
python3 "$SKILL_PATH/scripts/turn.py" \
  --context context/task.json --context context/agent.json \
  [--continue] [--slice-seconds 25] > /tmp/turn.json
```

```json
{
  "continue_loop": false,
  "reply_required": true,
  "reply_intent": {
    "kind": "needs_input | orders_table | preview | final_card | wait_retry | error | escalate",
    "frozen": {"marker": "...", "options": []},
    "facts": {"confidence": 0.62, "fdr": "FDR..."},
    "tone_context": {"user_last_said": "...", "already_asked": []}
  },
  "error": {"class": "transient|user_actionable|terminal", "summary": "..."},
  "progress": {"messages_posted": 3},
  "timings": {"state_rebuild_ms": 210, "providers_ms": 18340, "total_ms": 19550}
}
```

`timings` 必须有：每轮自动产出台账格式的延迟数据，基准回放直接消费。

## 七、契约层文件

- 每条业务链一个 `architecture-invariants.json`（基数约束，如一个诊断一个预览、一个预览一次执行、回写 API 恰好一次）。新增状态/派发路径必须先改契约和测试。
- 每个副作用一个幂等键，键定义写在 Skill 契约里；重试/并发/重复确认必须等价收敛，有并发测试而不是只测顺序主线。

## 八、载荷纪律

- 仅 Python 标准库（urllib/json/sqlite/hashlib），禁止 requirements.txt、禁止运行时 pip。
- skill 文件数和体积预算进 CI 检查；本体/大资源走 provider API + /tmp 任务内缓存，不随包。
- 依赖平台 skill 物化机制时，保留声明路径→仓库相对路径的 fallback 链。
