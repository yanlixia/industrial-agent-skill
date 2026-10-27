# 发布检查清单

## 一、形态

- [ ] 产品明确要单专家还是专家团。
- [ ] 没有为了实现方便擅自改变产品形态。
- [ ] 专家团每个角色都有明确职责、输入、输出和交接物。
- [ ] 每个角色标明 `runtime: member | inline`。
- [ ] 团队成员 Agent ID / squad ID 已写入 Skill 内 `config/team.json`，不是只写在仓库 manifest。

## 二、入口

- [ ] 单专家/团长第一动作是 `turn.py`。
- [ ] 成员第一动作是 `run.py`。
- [ ] 命令可从 `context/skills.json` 解析，并有固定 fallback。
- [ ] 每个 Skill 自带 `runtime.py` / `validate_reply.py`，只复制 Skill 目录也能运行。
- [ ] 没有全盘搜索、猜路径、先读无关源码。
- [ ] `continue_loop` / `reply_required` / `ok=false` 三态处理完整。

## 三、状态与副作用

- [ ] 权威状态在 issue/artifact，不在 `/tmp`。
- [ ] 回复 marker 只是加速通道。
- [ ] marker 丢失/损坏可静默回退。
- [ ] 每个副作用有幂等键。
- [ ] 重复事件、重复确认、并发派发测试通过。
- [ ] child issue 带 parent、role、幂等键、输入、验收标准。

## 四、专家团

- [ ] 团长不替成员执行专业工作。
- [ ] 成员不继续派发。
- [ ] 一次只派发 ready child，或并行拓扑有收敛测试。
- [ ] 成员结果结构化回传，不靠聊天猜测。
- [ ] 成员把结果写成 `artifacts/member-result.json`，团长从 artifact 恢复。
- [ ] 成员从 child issue 输入 JSON 取业务载荷，不把标题当用户请求。
- [ ] child 描述里的输入 JSON 不会被误当成员结果；平台自动 comment 不会被当唯一事实源。
- [ ] 团长最终回复综合成员产物。
- [ ] 平台 Squad Operating Protocol 与实现方式不冲突，或有验证过的内联决策。

## 五、回复

- [ ] `reply_intent` 分 frozen/facts/tone_context。
- [ ] `validate_reply.py` 通过。
- [ ] 回复验收使用 Skill 目录内脚本，不依赖仓库根目录。
- [ ] 失败有一次修正，之后模板兜底。
- [ ] 不裸错误、不泄漏 JSON/命令/内部路径/UUID。
- [ ] 回复长度在平台限制内。
- [ ] marker 契约 lint 通过。

## 六、测试

- [ ] 本地 smoke 通过。
- [ ] 路由全矩阵。
- [ ] 黄金 issue 树 fixture。
- [ ] 状态往返契约。
- [ ] 子进程错误通道。
- [ ] child 重复完成/并发派发。
- [ ] 发布包解压后 smoke 通过。
- [ ] `doctor.py` 的“只复制 Skill 目录”部署模拟通过。
- [ ] `doctor.py --release` 通过。

## 七、DeepWorks test

- [ ] 新建 test 会话，不用旧 session。
- [ ] 远端版本与本地 release manifest 一致。
- [ ] 完整事件时间线存在。
- [ ] dispatch 次数符合预期。
- [ ] artifact 数量符合预期。
- [ ] 外部 API 调用次数符合预期。
- [ ] 最终用户可见消息正确。
- [ ] 每轮 timings 完整。
- [ ] 单专家/专家团首响 P95 ≤120s。
- [ ] 专家团完整链按产品 SLA 达标，默认 ≤180s。

## 八、发布面

- [ ] 团长 instructions 已发布。
- [ ] 成员 instructions 已发布。
- [ ] 所有 Skill 版本已发布。
- [ ] squad 绑定已发布。
- [ ] runtime 绑定已发布。
- [ ] 团长 Skill 内的 `config/team.json` 已发布并填写真实 ID。
- [ ] manifest / release 记录已更新。
- [ ] 回滚路径已确认。

## 九、完成声明

只有以上全部满足，才可以说“可用”。

以下都不算完成：

- 旧会话跑通。
- 单测全绿。
- 子任务已创建。
- 只发布了 Skill。
- 只有本地 mock。
- marker 为空但“看起来能回复”。
