# 示例

这里给出可复现的单专家/专家团生成命令、门禁命令和 test 会话记录格式。

## 单专家

```bash
python3 tools/scaffold.py --mode single --domain energy --title 能源诊断专家 \
  --target ./examples/energy-expert
cd examples/energy-expert
python3 scripts/doctor.py
python3 -m unittest discover -s tests -v
python3 scripts/doctor.py --release   # 补 leader Agent ID 后执行
```

## 专家团

ID 可用占位符先跑本地门禁；真实发布前替换成 DeepWorks 里的真实值。

```bash
python3 tools/scaffold.py --mode team --domain energy --title 能源专家团 \
  --roles diagnosis,maintenance,monitor \
  --leader-agent-id leader_xxx \
  --agent-ids diagnosis=diag_xxx,maintenance=fix_xxx,monitor=watch_xxx \
  --squad-id squad_xxx \
  --target ./examples/energy-team
cd examples/energy-team
python3 scripts/doctor.py
python3 -m unittest discover -s tests -v
python3 scripts/doctor.py --release
```

团队运行时配置在 `skills/energy-orchestrate/config/team.json`。只改仓库根目录的 `deepworks.manifest.json` 不算完成，线上读不到它。

## test 会话记录

每个样例至少记录：session/task/child issue id、dispatch 次数、artifact 数量、外部 API 次数、最终用户消息、每轮 `timings`、P95 和失败分类。
