---
name: __DOMAIN__
description: __DOMAIN_TITLE__唯一入口；读取平台上下文与 issue 树，执行本轮计划并产出 reply_intent。
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
只依赖 issue/artifact 和本轮 context；不依赖跨轮 `/tmp`。

## 幂等键
- 副作用：`{root_issue_id}|{business_id}|{operation}`

## 失败
transient / user_actionable / terminal 三分类；子进程错误保留真实 message。

## 载荷
≤15 文件、≤200KB、仅标准库、无运行时安装。`runtime.py` / `validate_reply.py` 必须在本 Skill 的 `scripts/` 内。
