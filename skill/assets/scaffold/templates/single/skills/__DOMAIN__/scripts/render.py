from __future__ import annotations

from typing import Any, Dict, Optional

import runtime


def build(kind: str, root: str, text: str, facts: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return runtime.reply_intent(kind, root, text, facts)
