# 模板目录

不要手工复制本目录。用生成器创建单专家或专家团：

```bash
python3 tools/scaffold.py --mode single --domain energy --title 能源专家 --target ./energy
python3 tools/scaffold.py --mode team --domain energy --title 能源专家团 \
  --roles diagnosis,maintenance --target ./energy-team
```

目录说明：

- `templates/common/`：两种形态共用（回复验收、marker 契约、测试、doctor）。
- `templates/single/`：单专家入口和主 Skill。
- `templates/team/base/`：团长入口和 orchestrate Skill。
- `templates/team/role/`：每个成员 Agent 的入口、soul 和角色 Skill。

生成器会把 `runtime.py` / `validate_reply.py` 复制进每个 Skill 的 `scripts/`，并给专家团生成 `config/team.json`。这样平台只挂载 Skill 目录时也能运行。

模板只提供可运行骨架。生成后必须补领域 provider、真实 Agent ID、squad 绑定，跑 `doctor.py --release`，并在 DeepWorks test 环境新建会话验收。
