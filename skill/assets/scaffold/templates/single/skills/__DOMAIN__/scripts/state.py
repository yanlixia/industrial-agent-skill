from __future__ import annotations

from typing import Any, Dict, Iterable

import runtime


def rebuild(contexts: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    contexts = list(contexts)
    root = runtime.root_issue_id(contexts) or runtime.source_issue_id(contexts)
    return {"root_issue_id": root, "phase": "new"}
