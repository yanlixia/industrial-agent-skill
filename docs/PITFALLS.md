# 已实证坑与规避

来源：DeepWorks Agent Teams、设备运维 v3（DW-OBS-143~147）和 2026-10 的 test 会话。原则：坑要落到**机器门禁**，不要只写在提示词里。

## 共同坑

### 1. 把回复文本当状态库

**现象**：查询轮成功，下一轮选择却稳定失败，原因只是 marker 被模型改写或删掉。

**规避**：

- 权威状态放 issue description、child issue、comment、artifact。
- 回复 marker 只做加速；损坏或缺失时静默从 issue 树恢复。
- 用 `state_roundtrip.py` 测“产出 -> 渲染 -> 验收 -> 下一轮恢复”的整条链。

### 2. 规则在组合后互相打架

**现象**：验收器禁止 UUID，但续链 marker 自身含 UUID；模型为了过验收只能删 marker。

**规避**：

- 所有进入用户回复的 marker 必须登记在 `validate_reply.py` 的续链豁免清单。
- `check_marker_contract.py` 在 CI 强制 emit/exempt 一致。
- 新增 marker 命名空间时，同时改验收器和测试。

### 3. 组件测试全绿，真实链路断

**现象**：validator 测 validator、turn 测 turn，没有任何测试跨过层与层之间的接缝。

**规避**：

- 生成物必须带 smoke、回复验收、幂等、子进程错误和团队计划测试。
- 发布前必须跑一条完整的 mock 链路，再到 test 环境新会话跑真实链路。

### 4. 父进程读不到子进程真实错误

**现象**：所有失败都只剩一句“处理失败”，无法区分 CLI 缺失、权限拒绝和输入错误。

**规避**：

- 子进程 stderr 优先；为空则解析 stdout 的 error/message JSON。
- 原始错误写入 issue/audit，用户回复只给可执行摘要。
- 测试必须断言父进程拿到的文本包含子进程真实 message。

### 5. 本地全绿，线上 Skill 却 import 失败

**现象**：仓库根目录测试全部通过，发布后入口直接 `ModuleNotFoundError: runtime`。

**原因**：本地从仓库根运行，能看到 `scripts/runtime.py`；平台线上只挂载单个 Skill 目录。

**规避**：

- 每个 Skill 的 `scripts/` 自带 `runtime.py` / `validate_reply.py`。
- `doctor.py` 把 Skill 单独复制到临时目录再执行。
- 禁止用“仓库根目录存在”当作运行前提。

## 专家团额外坑

### 6. 团长亲自干活或成员继续派发

**规避**：团长只路由、派发、汇总；成员只执行本角色，禁止创建 child。

### 7. 重复事件重复建 child / 重复回写

**规避**：child 使用 `{root}|{role}|{business_id}|{operation}` 幂等键；派发前检查 existing child 和完成状态。

### 8. 只发“子任务已创建”，没有最终综合结果

**规避**：团长必须读取 child 的 `member-result.json` artifact，按角色汇总事实后回复；不能把“已转交”当完成。

### 9. 只更新 Skill，不更新 Agent / squad / runtime

**规避**：发布清单把团长 instructions、成员 instructions、所有 Skill、squad 绑定和 runtime 绑定列为同一批发布面。

### 10. 成员把 child 标题当用户请求

**现象**：成员收到了 child issue，但只看到 `ISS-12: [diagnosis] diagnosis 任务`，真实业务输入在 description 的 JSON 里。

**规避**：成员入口解析 child 输入 JSON 的 `request`，不要取 `task.input` 第一行；测试必须断言成员消费了真实载荷。

### 11. manifest 写了 ID，线上却读不到

**现象**：仓库 `deepworks.manifest.json` 已填 ID，团长线上仍报缺少成员 Agent ID。

**原因**：manifest 是发布索引，通常不随 Skill 挂载。

**规避**：把运行时 ID 写进团长 Skill 的 `config/team.json`，或用 `DEEPWORKS_AGENT_ID_<ROLE>` 环境变量；两者都缺时必须结构化失败。

### 12. 把 child 的输入 JSON 当成成员结果

**现象**：child 描述天然包含团长写入的 `{"request": ...}`。如果结果解析器扫描整段描述，就会在没有成员产物时误判成功。

**规避**：

- 只认 `artifacts/member-result.json`，或带 `member_result_key` 的 agent comment。
- 描述里的输入 JSON、平台自动摘要 comment 都不是机器结果。
- `done` 但读不到结构化结果必须按失败处理。

### 13. 机器结果直接发成用户可见 JSON comment

**现象**：成员 `run.py` 写了一条 JSON comment，平台完成 task 又合成一条可见 comment。

**规避**：机器结果写 artifact，成员最终回复只写一句中文完成提示。这样既保留证据链，也不把裸 JSON 暴露给用户。

## 延迟经验

真实链路的主要乘数是任务扇出、冷启动、LLM 游走和无效轮询。先做结构优化：一次只派一个 ready child、脚本切片轮询、payload 瘦身、幂等收敛；不要先删产品定义的专家角色。
