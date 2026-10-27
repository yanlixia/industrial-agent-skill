from __future__ import annotations

from typing import Any, Dict, List, Optional


def respond(
    request: str,
    contexts: List[Dict[str, Any]],
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "summary": "已完成本角色处理：%s" % (request or "空任务"),
        "text": "已完成本角色处理。",
        "facts": {},
        "artifacts": [],
    }
