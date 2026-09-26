"""Every tunable number the signal rules use, in one immutable value.

Each default is a first estimate. Measure it against your own recordings with
`oss_clarity.core.measure` before trusting it, and move it with
`dataclasses.replace(Thresholds(), hesitation_min_ms=2_000)`. Because the value
is frozen and passed in, two analyses with different thresholds can run at the
same time without affecting each other.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any


@dataclass(frozen=True, slots=True)
class Thresholds:
    # Rage click: this many clicks within the window, each near the one before.
    rage_window_ms: int = 1_000
    rage_radius_px: float = 30
    rage_min_clicks: int = 3
    # Dead click: nothing on the page reacts within the window.
    dead_window_ms: int = 1_500
    # Error click: a script error within the window after the click.
    error_window_ms: int = 1_000

    # Hesitation: the pointer rests this long, this still, and is not then pressed.
    hesitation_min_ms: int = 3_000
    hesitation_max_ms: int = 30_000
    hesitation_radius_px: float = 40
    hesitation_click_grace_ms: int = 1_500
    # Near miss: an unanswered click corrected by one this close, this soon.
    near_miss_window_ms: int = 2_000
    near_miss_radius_px: float = 40
    # Scroll hunt: this many changes of direction, each leg at least this long.
    scroll_hunt_reversals: int = 4
    scroll_hunt_min_leg_px: float = 300
    # Repeat submit: the same submit pressed again within the window.
    repeat_submit_window_ms: int = 10_000
    # Form refill: typed to at least this length, cut to under this share of it.
    refill_min_length: int = 3
    refill_drop_share: float = 0.25
    # Idle exit: the last page open at least this long with no interaction.
    idle_exit_min_ms: int = 20_000

    #: The longest text a hovered element may hold and still be something a
    #: visitor pauses on to decide (a price, a short label) rather than reads.
    #: Also the longest nearby text taken as a form field's label.
    short_text_chars: int = 60

    @classmethod
    def from_mapping(cls, values: dict[str, Any] | None) -> Thresholds:
        """Defaults with `values` applied. Unknown names raise `ValueError`,
        so a typo in a settings file is not silently ignored."""
        values = dict(values or {})
        known = {f.name for f in fields(cls)}
        unknown = sorted(set(values) - known)
        if unknown:
            raise ValueError(f"unknown threshold(s): {', '.join(unknown)}")
        return cls(**values)

    def as_dict(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}
