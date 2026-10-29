from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from storylib.model import redact


class RedactionTests(unittest.TestCase):
    def test_redacts_common_secrets(self) -> None:
        text = (
            "Authorization: Bearer abc123\n"
            "password=hunter2 token: xyz cookie=session api_key=sk-1"
        )
        result = redact(text)
        for secret in ("abc123", "hunter2", "xyz", "session", "sk-1"):
            self.assertNotIn(secret, result)
        self.assertEqual(result.count("[REDACTED]"), 5)

    def test_keeps_normal_command(self) -> None:
        self.assertEqual(redact("python3 run.py auto"), "python3 run.py auto")


if __name__ == "__main__":
    unittest.main()
