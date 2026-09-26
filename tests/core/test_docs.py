"""The docs name every signal and every threshold, so they cannot drift."""

from __future__ import annotations

from pathlib import Path

from oss_clarity.core import SIGNAL_KINDS, Thresholds

DOCS = Path(__file__).resolve().parents[2] / "docs"


def test_every_signal_is_documented():
    text = (DOCS / "signals.md").read_text()
    for kind in (*SIGNAL_KINDS, "quick_back", "loop"):
        assert f"`{kind}`" in text, kind


def test_every_threshold_is_documented_with_its_default():
    text = (DOCS / "thresholds.md").read_text()
    for name, value in Thresholds().as_dict().items():
        assert f"| `{name}` | {value} |" in text, name
