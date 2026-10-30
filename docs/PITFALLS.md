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

### 14. 成员 Agent 绑定离线 runtime，调度卡永远 QUEUED

**现象**：child 已正确创建，页面也出现原生专家调度卡，但成员 task 一直 queued；`task-runs` 显示 runtime offline。

**原因**：成员 Agent 绑定了离线的本地 exec-host，而 claim 只会在 online 的 managed_sandbox 上领取成员 task。编排链路没有报错，团长于是静默轮询。

**规避**：

- 发布前用 `doctor.py --agents-json ... --runtimes-json ... --squad-json ... --agent-details-json ...` 校验每个成员的 runtime 为 `managed_sandbox` + `online`；squad-only Agent 不在 `/api/agents` 列表里，必须补单条详情导出。
- 成员 Skill 必须绑定到成员 Agent，不要只绑团长。
- 成员 task 无进展超过阈值时返回 `member_runtime_stalled`，保留已创建的调度卡，不重复创建。
- 控制台修复 runtime 后回复“继续”，复用已有 child。

### 15. 用无 assignee 投影冒充成员执行

**现象**：页面上有阶段卡，但没有真实成员 task；`/task-runs` 为空，成员 token 为 0。

**原因**：为了绕过成员冷启动或排队，把成员改成团长内联 Skill + 投影 child。

**规避**：产品要求原生专家调度卡或独立成员执行明细时，内联方案直接禁用。验收必须检查 child 的 assignee 和 `/task-runs`，不能只看阶段卡标题。

### 16. 只看 runtime online 就归因容量

**现象**：runtime 显示 online，task 仍长期 queued。

**规避**：按 `QUEUED` 七类判据分流：容量、终态 run 留活绑定、候选静默跳过、无可用 managed_sandbox、退避、issue 串行化、平台调度。至少验证“同 runtime 新 task 是否秒 claim”和“成员 runtime 是否满足安全/版本策略”。

### 17. 派发脚本硬编码历史 Agent UUID

**现象**：发布门禁按当前 squad 校验通过，但成员 child 仍派给旧专家，页面调度卡挂在错误的 Agent 上；旧 Agent 往往还绑定离线 runtime，任务永远 queued。

**原因**：脚本把某次环境的 Agent ID 写成默认值，部署 env 未注入时静默回退。不同 Test/生产 workspace 的 Agent ID 不同，squad 重新创建后旧 ID 也不会自动失效。

**规避**：

- 派发脚本只从当前 squad 配置或部署 env 解析成员 ID，不保留任何环境相关 UUID 默认值。
- 发布门禁同时校验 squad 成员列表、runtime/Skill 绑定、团长 custom_env 与 squad 当前 ID 一致。
- 缺失或不一致必须 fail-closed；用测试断言“空 env 不派发”“旧 ID 不出现”。

### 18. 只发布 Skill，以为沙箱会自动用上新代码

**现象**：本地测试全绿，test 会话行为仍是旧版本；日志里跑的脚本和仓库当前内容对不上。

**原因**：沙箱注入 `context/skills/` 时优先取 Skill 的 `published_version_id` 目录，草稿改动不会进入执行面；Skill 绑定还挂在 `agent_version_id` 上。

**规避**：改完 Skill 必须发布新版本；验收前确认目标 AgentVersion 的绑定记录存在，并新开会话。不要用“文件已上传”代替“版本已发布且被注入”。

### 19. Agent 改绑 runtime 后，存量 Task 仍排在旧 runtime

**现象**：管理面显示成员 Agent 已绑到新的 managed_sandbox，task 还是 QUEUED。

**原因**：`runtime_id` 在 `enqueue_issue_task_for_agent` 创建 Task 时就写死；只有重新入队（`requeue_task_for_dispatch`）会清空它。

**规避**：排障看 Task 自己的 `runtime_id`，不看 Agent 当前配置。要让存量任务迁走，必须走重排/重建；不要重复创建同名 child 赌运气。

### 20. 把 session 复用当成默认加速

**现象**：同一会话第二轮明显变快，但偶发读到上一轮残留文件，或空闲后突然退回冷启动。

**原因**：只有 `lifecycle.mode=session` + `cleanup_policy=session_idle_destroy` 才复用；`idle_ttl_seconds` 默认 300 秒，过期即销毁；`session_volume` 会跨轮保留工作区。

**规避**：跨轮状态显式写在 issue/artifact，不依赖工作区残留；验收同时覆盖“复用命中”和“空闲超时后冷启动”两条路径。

### 21. 把“正常等待成员”误判成“派发失败”

**现象**：查询轮成功、诊断 child 也真的创建了（页面有调度卡），下一轮评审只返回 `action=wait`，团长却回复“工单选择派发未完成，请稍后重试”，业务链就此中断。

**原因**：续跑结果没有携带刚创建的 child id，评审层把“拿不到身份”当成“没派发”；编排脚本再进一步，把所有没有 `dispatch_issue_id` 的 `wait` 一律归类成“空选择失败”，用一句通用失败把真实状态覆盖掉。

**规避**：

- 派发返回值必须带 child issue id；拿不到 id 就 **fail-closed**，用独立原因（如 `dispatch_identity_missing`）失败，不许当成成功。
- 进度里持久化 `dispatch_issue_id`，并且**每个续跑结果都把它透传出去**：`wrap_result` 只挑白名单字段是最常见的丢身份写法。
- 评审层判定“空派发”必须同时满足：`action=wait` + 无任何身份 + 明确原因码；只满足前两条就是误判。
- 回归测试要覆盖：“派发成功 → 下一轮 wait 携带同一 id → 不重复派发、不返回通用失败”。

### 22. 用 `404` / `children=[]` 当作“不存在”的证据

**现象**：脚本判断 child 没建好，于是重建一次；实际平台上 child 已经存在，最终同一业务键出现两个 child、两张调度卡。

**原因**：workspace issue 可见性依赖 token 身份——非 owner 只能看到自己是 primary owner 或自己创建的 issue；daemon 创建的 root/child 可能对当前 token 不可见。REST 列表返回空数组、单条查询返回 404，都只是**当前视角不可见**。

**规避**：

- 幂等判定只信“正向证据”：description 里的幂等标记、`/task-runs` 里的真实成员 Task、自己刚写入的返回值。
- 负向证据一律升级为**未知**：可见性不足时返回结构化“无法确认”，不要进入重建分支。
- 需要跨 workspace 核对时用服务身份（daemon/服务账号）读，而不是拿用户 token 兜底。

### 23. 发布门禁自身踩坑：列表缺项 + 字段脱敏

**现象**：发布前预检脚本报“成员 Agent 缺失”“团长 custom_env 与 squad 不一致”，但页面和单条详情都正常。

**原因**：`GET /api/agents` 列表接口不返回只配置了 `summon_modes=["squad"]` 的诊断/维修 Agent；`custom_env` 在管理面被脱敏成 `***`。拿列表做存在性判断、拿脱敏值做等值比较，必然误报。

**规避**：

- 存在性判断回落到**单条详情接口**或 squad 成员接口，不用列表。
- 脱敏字段（`custom_env` 等）只校验“存在且非空”，不校验具体值；需要校验真实绑定就走 squad 成员与 agent 详情。
- 门禁误报必须当缺陷修，不能靠“人工忽略”通过；否则真正的绑定错误会和误报一起被放过。

### 24. 把外部依赖故障当成自己的业务失败

**现象**：诊断/维修任务连续三次报 `IAM request failed with status 504`，整条链停在“诊断未完成”；排查方向一度指向 Skill 鉴权参数和 squad 绑定。

**原因**：外部网关（本例是 test 环境 Foil 的 ELB，504 + `Server: elb`）整体不可用，而脚本把 5xx 和 401/403 同等对待，既没有区分归因，也没有明确的“等外部恢复”语义。

**规避**：

- 认证错误分级：401/403 → 配置/凭据问题，立即失败并提示换凭据；502/503/504、连接超时 → 外部依赖不可用，做**有界重试**（如 2~3 次退避）后返回结构化 `external_dependency_unavailable`。
- 失败信息带上上游状态码与目标域名，不要只写“诊断失败”；这样值班同学能一眼区分“我们挂了”和“上游挂了”。
- 验收记录要写明外部依赖状态；外部不可用期间不要宣布链路通过，也不要为此改业务代码。
- 同一故障要先用本机 curl 对照（sandbox 与宿主机各一次），确认是环境级还是代码级。

## 延迟经验

真实链路的主要乘数是任务扇出、冷启动、LLM 游走和无效轮询。先做结构优化：一次只派一个 ready child、脚本切片轮询、payload 瘦身、幂等收敛；不要先删产品定义的专家角色。
