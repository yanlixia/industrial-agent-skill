---
name: __DOMAIN__-orchestrate
description: __DOMAIN_TITLE__团长唯一入口；读取 issue 树和团队计划，派发 ready child 或汇总最终回复。
version: 0.1.0
---

## 输入
- `--context context/task.json`
- `--context context/agent.json`
- `--context context/previous_tasks.json`
- `--continue` / `--event`

## 输出
`/tmp/turn.json`：`ok`、`continue_loop`、`reply_required`、`reply_intent`、`error`、`timings`。

## 状态依赖
只依赖 issue/artifact；成员结果优先读 child issue 的 `member-result.json` artifact，历史任务只做辅助。

## 运行配置
成员 Agent ID 从 `config/team.json` 读取；缺失时再读 `DEEPWORKS_AGENT_ID_<ROLE>`。两者都没有就结构化失败。

## 幂等键
- child：`{root_issue_id}|{role}|{business_id}|{operation}`

## 失败
transient / user_actionable / terminal 三分类；不吞子进程真实错误。

## 载荷
≤15 文件、≤200KB、仅标准库、无运行时安装。`runtime.py` / `validate_reply.py` 必须在本 Skill 的 `scripts/` 内。
