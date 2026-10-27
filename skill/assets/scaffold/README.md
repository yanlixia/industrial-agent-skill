# 工业智能体模板骨架

复制本目录到新智能体仓库后：

1. 把 `DOMAIN` 全局替换为领域名（如 `device-ops`、`energy`）。
2. 补全 `{业务}`、事件表、输出契约、幂等键。
3. 按 `references/file-templates.md` 实现 `turn.py` 与 `contracts/`。
4. 落 `tests/`：路由全矩阵、幂等并发、黄金 issue 树、**状态往返契约**、子进程错误通道。
5. CI 必须含 `python3 scripts/check_marker_contract.py`（emit/exempt 一致性门）。
6. 落 `scripts/remote_probe.py`（回放探针）与 `docs/ledger-latency.md`（延迟台账）。

## 自带的已验证实现（改参数，不要从散文重写）

| 文件 | 说明 |
|---|---|
| `skills/DOMAIN-orchestrate/scripts/validate_reply.py` | 验收门。含隐形续链 marker 豁免正则——**新增 marker 命名空间必须加进 `CONTINUATION_MARKER_PATTERN`**，CI 的 marker contract 检查会强制这一点（豁免缺失 = 模型被逼删 marker = 跨轮状态链断，v3 DW-OBS-143 实证） |
| `scripts/check_marker_contract.py` | CI 一致性门。扫回层文件（turn.py/render_reply.py/*relay*/*reply*，可用 --reply-files 增补）emit 的 marker 命名空间，断言全部在 validator 豁免清单。**注意启发式局限**：marker 若在既定命名模式之外的文件里拼进回复（如 v3 的 mes_query_flow.py），必须用 --reply-files 显式声明 |

## 状态通道铁律（v3 实证，违反必断链）

1. **权威状态放 issue description / artifact**（平台原生、不过 LLM 手）；回复内 marker 只做加速通道。
2. marker 解码/校验失败 → 静默回退 issue 恢复，绝不 escalate（模型复述长 base64 会抄错字符，DW-OBS-146 实证 2 处/672 字符）。
3. 回复文本会被截断（平台单条 3000 字）、被验收器删、被模型改写——设计任何跨轮机制前先假设它会发生。
