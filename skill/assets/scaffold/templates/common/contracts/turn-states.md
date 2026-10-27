# Turn States

填写本业务的状态机，不要只写主流程。

| 状态 | 用户看到什么 | 允许迁移 | 谁是执行者 |
|---|---|---|---|
| idle | 欢迎/能力说明 | needs_input, working | 单专家/团长 |
| needs_input | 编号问题 | working | 单专家/团长 |
| working | 处理中或等待成员 | result, error | 单专家/团长 |
| result | 结构化结果 | idle | 单专家/团长 |
| error | 错误分类与下一步 | idle | 单专家/团长 |

专家团额外字段：

- `role`：当前 ready child 的角色。
- `dispatch_key`：child 幂等键。
- `member_result`：成员结构化结果入口。
