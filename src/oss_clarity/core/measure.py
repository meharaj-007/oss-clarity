"""Measuring the signal thresholds against real recordings.

Every default in `Thresholds` is a first estimate. Before trusting one, run
this over at least a few hundred recordings from several sites: it analyses
each recording at the given thresholds, then again with each threshold moved
up and down, and reports how many signals, and how many recordings with at
least one, every setting would produce.

A sweep reads the distribution at the points that matter. "Hesitation fires in
4 % of recordings at 3 s, 11 % at 2 s and 1 % at 5 s" is the question a
threshold answers. Samples point at recordings to watch, because only a person
can say whether a hesitation really was one.

Nothing is written and nothing global is changed: each variant is a new
`Thresholds` value, so this is safe to run beside live analysis.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, fields, replace
from typing import Any

from .events import ERROR_TAG, PAGEVIEW_TAG
from .navigation import DEFAULT_QUICK_BACK_SECONDS, Moment, count
from .signals import SIGNAL_KINDS, Page, analyze
from .thresholds import Thresholds

#: The minimum evidence before a threshold is worth moving.
GATE_RECORDINGS = 200
GATE_SITES = 3

#: Threshold name → (signals it moves, values to try). The value in use is
#: always added, so it appears in its own sweep.
SWEEPS: dict[str, tuple[tuple[str, ...], tuple[float, ...]]] = {
    "rage_window_ms": (("rage", "repeat_submit"), (700, 1_000, 1_500)),
    "rage_radius_px": (("rage",), (20, 30, 50)),
    "rage_min_clicks": (("rage",), (3, 4, 5)),
    "dead_window_ms": (("dead", "near_miss"), (1_000, 1_500, 2_500)),
    "error_window_ms": (("error",), (500, 1_000, 2_000)),
    "hesitation_min_ms": (("hesitation",), (1_500, 2_000, 3_000, 4_000, 5_000)),
    "hesitation_max_ms": (("hesitation",), (15_000, 30_000, 60_000)),
    "hesitation_radius_px": (("hesitation",), (20, 40, 60)),
    "hesitation_click_grace_ms": (("hesitation",), (500, 1_500, 3_000)),
    "short_text_chars": (("hesitation",), (30, 60, 120)),
    "near_miss_window_ms": (("near_miss",), (1_000, 2_000, 3_000)),
    "near_miss_radius_px": (("near_miss",), (24, 40, 60, 80)),
    "scroll_hunt_reversals": (("scroll_hunt",), (3, 4, 5, 6)),
    "scroll_hunt_min_leg_px": (("scroll_hunt",), (200, 300, 500)),
    "repeat_submit_window_ms": (("repeat_submit",), (5_000, 10_000, 20_000)),
    "refill_min_length": (("form_refill",), (2, 3, 5)),
    "refill_drop_share": (("form_refill",), (0.1, 0.25, 0.5)),
    "idle_exit_min_ms": (("idle_exit",), (10_000, 20_000, 30_000, 60_000)),
}
#: Seconds on B for A, B, A to be a quick back.
QUICK_BACK_SWEEP = (3, 5, 10, 15)

#: Every kind the report counts.
KINDS = (*SIGNAL_KINDS, "quick_back", "loop")


@dataclass(frozen=True)
class LoadedRecording:
    """One recording, read once. `pages` is `[(page_id, events)]` in visit
    order; `page_views` is `[(path, time)]` in visit order (see
    `navigation.ordered`)."""

    id: Any
    site: str
    pages: Sequence[Page]
    page_views: Sequence[tuple[str, Moment]] = field(default_factory=tuple)


def measure(
    recordings: Iterable[LoadedRecording],
    *,
    thresholds: Thresholds | None = None,
    quick_back_seconds: float = DEFAULT_QUICK_BACK_SECONDS,
    samples: int = 5,
    sweeps: Mapping[str, tuple[tuple[str, ...], tuple[float, ...]]] | None = None,
    quick_back_sweep: Iterable[float] | None = QUICK_BACK_SWEEP,
    gate_recordings: int = GATE_RECORDINGS,
    gate_sites: int = GATE_SITES,
    failed: int = 0,
    pageview_tags: Iterable[str] = (PAGEVIEW_TAG,),
    error_tags: Iterable[str] = (ERROR_TAG,),
) -> dict[str, Any]:
    """The whole report, as plain data.

    `failed` is how many recordings the caller could not load; it is only
    reported. Pass `sweeps={}` and `quick_back_sweep=None` for a baseline
    alone.
    """
    base = thresholds or Thresholds()
    sweeps = SWEEPS if sweeps is None else sweeps
    known = {f.name for f in fields(Thresholds)}
    unknown = sorted(set(sweeps) - known)
    if unknown:
        raise ValueError(f"unknown threshold(s) to sweep: {', '.join(unknown)}")

    loaded = list(recordings)
    tags = {"pageview_tags": tuple(pageview_tags), "error_tags": tuple(error_tags)}
    sites: dict[str, int] = {}
    for item in loaded:
        sites[item.site] = sites.get(item.site, 0) + 1
    n = len(loaded)
    pages = sum(len(item.pages) for item in loaded)

    baseline, marked = _run(loaded, base, quick_back_seconds, tags)
    report: dict[str, Any] = {
        "recordings": n,
        "failed": failed,
        "pages": pages,
        "sites": dict(sorted(sites.items(), key=lambda kv: -kv[1])),
        "gate": {
            "recordings": {"have": n, "need": gate_recordings, "met": n >= gate_recordings},
            "sites": {"have": len(sites), "need": gate_sites, "met": len(sites) >= gate_sites},
        },
        "thresholds": base.as_dict(),
        "baseline": {
            kind: {
                "total": total,
                "recordings": with_it,
                "share": (with_it / n) if n else 0.0,
                "per_100_pages": (100 * total / pages) if pages else 0.0,
            }
            for kind, (total, with_it) in baseline.items()
        },
        "sweeps": {},
        "samples": _samples(marked, per_kind=samples),
    }

    for name, (kinds, values) in sweeps.items():
        current = getattr(base, name)
        rows = []
        for value in sorted({*values, current}):
            tally, _ = _run(loaded, replace(base, **{name: value}), quick_back_seconds, tags)
            rows.append(_row(value, current, tally, kinds))
        report["sweeps"][name] = {"signals": list(kinds), "rows": rows}

    if quick_back_sweep is not None:
        rows = []
        for value in sorted({*quick_back_sweep, quick_back_seconds}):
            tally, _ = _run(loaded, base, value, tags, signals=False)
            rows.append(_row(value, quick_back_seconds, tally, ("quick_back",)))
        report["sweeps"]["quick_back_seconds"] = {"signals": ["quick_back"], "rows": rows}
    return report


def _row(value, current, tally, kinds) -> dict[str, Any]:
    return {
        "value": value,
        "current": value == current,
        "signals": {
            kind: {"total": tally[kind][0], "recordings": tally[kind][1]} for kind in kinds
        },
    }


def _run(
    loaded: list[LoadedRecording],
    thresholds: Thresholds,
    quick_back_seconds: float,
    tags: dict[str, tuple[str, ...]],
    *,
    signals: bool = True,
) -> tuple[dict[str, list[int]], list[tuple[LoadedRecording, list[dict]]]]:
    """`({kind: [total, recordings with it]}, [(recording, markers)])`."""
    tally = {kind: [0, 0] for kind in KINDS}
    marked = []
    for item in loaded:
        if signals:
            counts, markers = analyze(item.pages, thresholds, **tags)
        else:
            counts, markers = {}, []
        moves = count(item.page_views, quick_back_seconds)
        counts = {**counts, "quick_back": moves["quick_backs"], "loop": moves["loops"]}
        for kind, value in counts.items():
            tally[kind][0] += value
            tally[kind][1] += 1 if value else 0
        marked.append((item, markers))
    return tally, marked


def _samples(marked, *, per_kind: int) -> dict[str, list[dict[str, Any]]]:
    """Up to `per_kind` markers of each kind to watch: one per recording
    first, then second ones. Five hesitations from one visit say less than
    one from each of five visits."""
    by_kind: dict[str, list[dict[str, Any]]] = {}
    taken: set[int] = set()
    for first_round in (True, False):
        for item, markers in marked:
            for marker in markers:
                bucket = by_kind.setdefault(marker["kind"], [])
                key = id(marker)
                if len(bucket) >= per_kind or key in taken:
                    continue
                if first_round and any(entry["recording"] == item.id for entry in bucket):
                    continue
                taken.add(key)
                bucket.append(
                    {
                        "recording": item.id,
                        "site": item.site,
                        "page_id": marker["page_id"],
                        "t_ms": marker["t_ms"],
                        "label": marker.get("label") or "",
                        "selector": marker.get("selector") or "",
                    }
                )
    return {kind: bucket for kind, bucket in by_kind.items() if bucket}
