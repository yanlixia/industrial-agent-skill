# 质量与性能门禁

## 专家契约

发布前必须完成 `contracts/expert-contract.schema.json` 对应的专家契约文件：

- 明确职责、输入、输出、边界和失败模式；
- 每个输出必须有可执行校验；
- 不确定时输出结构化 `ask_user` 或 `wait_retry`，不得编造事实；
- LLM 只做事实抽取、匹配和表达，控制流和副作用必须由脚本执行。

## 性能预算

`contracts/performance-budget.json` 是用户体验预算，不是单脚本耗时预算：

- 每个用户可见轮次默认必须小于 120 秒；
- 平台调度、沙箱、模型、工具、外部 API 和渲染全部计入；
- 每轮必须输出 `timings`，缺失即验收失败；
- 优先裁剪上下文和 payload，再考虑并行 child；
- 并行 child 必须有拓扑、收敛测试和每路 payload 上限。

## 评测与观测

`contracts/evaluation-suite.json` 至少维护黄金路径、边界路径、并发重复输入和真实历史回归：

- 黄金路径验证最终用户消息和结构化结果；
- 边界路径验证不编造、不越权、不产生副作用；
- 并发路径验证幂等键和副作用基数；
- 每次真实回归必须补入 `regressions`；
- 发布记录必须包含耗时、dispatch 数、artifact 数、外部 API 次数、token 用量和最终用户消息。

## 发布门禁

满足以下条件才可宣布发布完成：

1. 本地测试、doctor、marker 契约全部通过；
2. Skill、Agent instructions、squad 绑定和 runtime 同批版本一致；
3. DeepWorks Test 新会话使用新版本；
4. 事件时间线、最终用户消息、副作用次数和性能预算全部达标；
5. 旧会话、旧 issue 或“子任务已创建”不能替代完整业务结果验收。
