from __future__ import annotations

from typing import Any, Dict


def respond(request: str, state: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "kind": "result",
        "text": "已收到：%s" % request,
        "facts": {},
    }
