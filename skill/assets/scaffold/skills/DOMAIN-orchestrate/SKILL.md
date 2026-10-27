---
name: DOMAIN-orchestrate
description: {业务}编排唯一入口；读平台上下文与 issue 树，路由并执行本轮计划，产出 reply_intent 与 timings。
version: 0.1.0
---

## 输入
- `--context context/task.json`
- `--context context/agent.json`
- `--continue`：上一 slice 的延续
- `--event answer|confirm`

## 输出契约
写 `/tmp/turn.json`，字段：`continue_loop`、`reply_required`、`reply_intent`、`error`、`progress`、`timings`。完整结构见仓库 `contracts/schemas/turn.schema.json`。

## 状态依赖
只依赖 issue 树重建状态；不依赖 /tmp 跨轮文件。

## 幂等键定义
- {副作用 A}：`{实体 id} + {操作名}`
- {副作用 B}：`{父记录 id} + {实体 id} + {操作名}`

## 载荷预算
≤15 个文件、≤200KB、仅 Python 标准库、无运行时安装。
