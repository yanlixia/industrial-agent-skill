---
name: __ROLE__
description: __ROLE_TITLE__唯一执行入口；处理团长派发的 child 任务并返回结构化结果。
version: 0.1.0
---

## 输入
- `--context context/task.json`
- `--context context/agent.json`
- 团长写入 child issue 的输入载荷

## 输出
`/tmp/result.json`：`ok`、`role`、`result`、`reply_intent`、`error`、`timings`。
同一 JSON 写入任务目录的 `artifacts/member-result.json`；平台发布后由团长读取。

## 状态依赖
只依赖 child issue/artifact；不依赖团长沙箱。

## 幂等键
- 本角色副作用：`{root_issue_id}|__ROLE__|{business_id}|{operation}`

## 失败
返回结构化错误；父进程必须能读到真实 message。

## 载荷
≤15 文件、≤200KB、仅标准库、无运行时安装。`runtime.py` 必须在本 Skill 的 `scripts/` 内。
