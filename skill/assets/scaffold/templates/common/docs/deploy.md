# 发布步骤

## 1. 准备 Agent

在 DeepWorks「AI 同事工作区」创建 Agent，并绑定本仓库对应目录：

- 单专家：创建 1 个 Agent，instructions 用 `entry/agents.md`，soul 用 `entry/soul.md`，Skill 绑定 `skills/__DOMAIN__/SKILL.md`。
- 专家团：先创建所有成员 Agent，再创建团长 Agent。成员 instructions 在 `experts/<role>/AGENTS.md`，团长 instructions 在 `entry/agents.md`。

每个 Skill 目录已经自带 `runtime.py` / `validate_reply.py`。平台上只上传 Skill 目录，不要把仓库根目录当成运行依赖。

## 2. 创建 squad

专家团创建 squad：

- leader = 团长 Agent
- members = `deepworks.manifest.json` 里的全部角色
- squad instructions = 团长入口约束

把返回的 squad id、leader agent id、成员 agent id 写回：

- `deepworks.manifest.json`：发布索引。
- `skills/__DOMAIN__-orchestrate/config/team.json`：团长线上真正读取的成员 ID / squad ID。

也可以不用 `config/team.json`，改成在团长 Agent 的自定义环境变量中设置
`DEEPWORKS_AGENT_ID_<ROLE>`；两者都没有时团长会明确报缺少配置，不会猜。

## 3. 本地门禁

```bash
python3 scripts/doctor.py
python3 scripts/doctor.py --release
python3 -m unittest discover -s tests -v
python3 scripts/check_marker_contract.py .
```

`doctor.py` 会把每个 Skill 单独复制到临时目录运行，模拟线上只挂载 Skill 的布局；`--release` 还会检查真实 Agent/squad ID。全绿后再发布，不能只因为“文件上传成功”就宣布可用。

## 4. DeepWorks test

- 新建会话，不复用旧 session。
- 单专家：核对首响、最终消息、错误分类和 timings。
- 专家团：核对 leader task、每个 child issue、成员 `member-result.json` artifact、团长最终汇总。
- 只上传 Skill 目录再跑一次 smoke，确认没有 `ModuleNotFoundError` 或根目录脚本依赖。
- 核对 dispatch 次数、artifact 数量、外部 API 调用次数和重复副作用。

## 5. 发布面

同一批更新并发布：

- 团长 instructions
- 成员 instructions
- 每个 Skill 版本
- squad 绑定
- runtime 绑定
- 团长 Skill 内的 `config/team.json`
- `deepworks.manifest.json`

只发布 Skill、不更新 Agent/squad/runtime，不算完成。
