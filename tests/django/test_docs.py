from __future__ import annotations

from pathlib import Path

from oss_clarity.conf import DEFAULTS

DOCS = Path(__file__).resolve().parents[2] / "docs"


def test_every_setting_is_documented():
    text = (DOCS / "settings.md").read_text()
    for key in DEFAULTS:
        assert f"| `{key}` |" in text, key
