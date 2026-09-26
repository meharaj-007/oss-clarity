"""Frustration and behaviour signals, read in one pass over a finished recording.

This docstring is the definition of record. The counters, the markers the
player shows and the documentation all follow it, so they cannot disagree
about what a rage click is. Every number named below is a field of
`Thresholds`; each is a first estimate to be measured with `core.measure`.

Frustration

* Rage click: at least `rage_min_clicks` clicks within `rage_window_ms`, each
  within `rage_radius_px` of the one before. Every click in the burst is marked.
* Dead click: a click on something that is not a form control, followed within
  `dead_window_ms` by nothing at all: no DOM mutation, no scroll, no selection
  change, no page view. The page did not react.
* Error click: a click followed within `error_window_ms` by a script error the
  tracker wrote into the recording. An error click is not also a dead click.
* Script error: every script error event, counted whether or not a click came
  before it.

Behaviour

* Hesitation: the mouse rests on something pressable, or on a short piece of
  text such as a price (an element holding only text, at most
  `short_text_chars` long; a footer of five short links is not one), for
  between `hesitation_min_ms` and `hesitation_max_ms`, within
  `hesitation_radius_px`, with nothing else happening, and is not then pressed
  within `hesitation_click_grace_ms`. Mouse only, since a finger does not
  hover. Once per element per page.
* Near miss: a click the page did not react to before the next click, followed
  within `near_miss_window_ms` by a click at most `near_miss_radius_px` away on
  a different pressable element that the page did react to. The visitor
  missed, then corrected. It is not defined as "a dead click, then", because
  the correction's own reaction falls inside the miss's dead-click window.
  Marked on the element aimed for, at the position of the miss.
* Scroll hunt: `scroll_hunt_reversals` changes of scroll direction on one page,
  each leg at least `scroll_hunt_min_leg_px`, with no click, input or
  selection in between. Once per page.
* Form skip: a field focused and left with nothing typed, while another field
  was typed into afterwards. Once per field.
* Form refill: a field's typed length falls to at most `refill_drop_share` of
  the most it reached (having reached at least `refill_min_length`), then
  grows back to at least `refill_min_length`. Masked inputs keep their length,
  which is all this reads. Once per field.
* Form abandon: the visit ends on a page where something was typed and no
  submit was pressed after the last keystroke. Marked on the last field typed
  into. Only on the last page of the recording, because a form sent with Enter
  navigates away and cannot be told from one left for a link.
* Repeat submit: the same submit control pressed again later than
  `rage_window_ms` and within `repeat_submit_window_ms`: nothing looked like
  it happened. Once per control per page.
* Copy-out: selected text shaped like a phone number, an email address or a
  street address, as the browser masked it (masked digits arrive as `▫`).
  Once per text node.
* Idle exit: the visit ends on a page that was open for at least
  `idle_exit_min_ms` with no click, scroll, input, selection or touch at all.

Quick backs and loops are read from page views, not from the recording, so
every visit has them; see `oss_clarity.core.navigation`.

Two rules apply to all of the above:

* Typed means typed by the visitor. For the three form signals an input
  counts only if rrweb marked it `userTriggered`, or, in recordings that do
  not carry that flag, if the visitor focused or clicked that field first.
  Scripts fill hidden fields and calculators, and a script never focuses the
  field it sets.
* Consent banners are not the site. A click, rest or near miss inside a cookie
  or consent container (`mirror.CONSENT_RE` on an ancestor's id, class or
  aria-label) is never a dead click, hesitation or near miss. The banner is a
  visitor's first decision on most pages and would otherwise put the same
  finding on every site.

A marker is `{t_ms, page_id, kind, selector, label, x, y}`, where `t_ms` is
milliseconds from the first event of the recording (what the player's
timeline measures). At most `MAX_MARKERS` are kept per recording, earliest
first; the counters are never capped.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterable, Sequence

from .events import (
    ERROR_TAG,
    PAGEVIEW_TAG,
    EventType,
    IncrementalSource,
    MouseInteraction,
)
from .mirror import Mirror
from .thresholds import Thresholds

#: Every signal `analyze` counts, in display order.
SIGNAL_KINDS = (
    "rage",
    "dead",
    "error",
    "script_error",
    "hesitation",
    "near_miss",
    "scroll_hunt",
    "form_skip",
    "form_refill",
    "form_abandon",
    "repeat_submit",
    "copy_out",
    "idle_exit",
)

#: Markers kept per recording. A recording with more is already described by
#: its counters.
MAX_MARKERS = 200

Page = tuple[str, Sequence[dict]]
Marker = dict
Mark = Callable[..., None]

_PHONE_RE = re.compile(r"^[+(]?[\d▫][\d▫ ()\-.]{5,}[\d▫]$")
_EMAIL_RE = re.compile(r"\S+@\S+\.\S+")
_ADDRESS_RE = re.compile(
    r"^[\d▫]{1,5}[a-z]?\s+\S+.*\b(st|street|rd|road|ave|avenue|dr|drive|ln|lane|ct|court|"
    r"pde|parade|hwy|highway|pl|place|blvd|boulevard|way|cres|crescent|tce|terrace|cl|close)\b",
    re.IGNORECASE,
)

_REACTIONS = (IncrementalSource.MUTATION, IncrementalSource.SCROLL, IncrementalSource.SELECTION)


def analyze(
    pages: Iterable[Page],
    thresholds: Thresholds | None = None,
    *,
    pageview_tags: Iterable[str] = (PAGEVIEW_TAG,),
    error_tags: Iterable[str] = (ERROR_TAG,),
) -> tuple[dict[str, int], list[Marker]]:
    """The signals of one recording: `(counts, markers)`.

    `pages` is `[(page_id, events)]` in visit order; the last page is where
    the visit ended. `pageview_tags` and `error_tags` name the custom events
    that mean "a page view happened" and "a script error happened", for
    recordings made by a tracker that tagged them differently.

    Pure: reads nothing, writes nothing, and the same input always gives the
    same output.
    """
    t = thresholds or Thresholds()
    pages = list(pages)
    tags = _Tags(frozenset(pageview_tags), frozenset(error_tags))
    counts = dict.fromkeys(SIGNAL_KINDS, 0)
    markers: list[Marker] = []

    origin = None
    for _, events in pages:
        for event in events:
            ts = event.get("timestamp") if isinstance(event, dict) else None
            if isinstance(ts, (int, float)):
                origin = ts if origin is None else min(origin, ts)
    origin = origin or 0

    for index, (page_id, events) in enumerate(pages):
        _analyze_page(
            page_id,
            events,
            origin,
            counts,
            markers,
            t,
            tags,
            is_last_page=index == len(pages) - 1,
        )

    markers.sort(key=lambda m: m["t_ms"])
    return counts, markers[:MAX_MARKERS]


class _Tags:
    __slots__ = ("pageview", "error")

    def __init__(self, pageview: frozenset[str], error: frozenset[str]) -> None:
        self.pageview = pageview
        self.error = error


class _Page:
    """One document's events, sorted into the streams the rules read."""

    def __init__(self, short_text_chars: int) -> None:
        self.mirror = Mirror(short_text_chars=short_text_chars)
        self.clicks: list[dict] = []
        self.reactions: list[float] = []
        self.errors: list[float] = []
        self.moves: list[dict] = []
        self.scrolls: list[dict] = []
        self.inputs: list[dict] = []
        self.focuses: list[dict] = []
        self.selections: list[dict] = []
        #: Anything the visitor did other than moving the mouse.
        self.activity: list[float] = []
        self.first_ts: float | None = None
        self.last_ts: float | None = None


def _gather(events: Iterable[dict], t: Thresholds, tags: _Tags) -> _Page:
    page = _Page(t.short_text_chars)
    for event in events:
        if not isinstance(event, dict):
            continue
        ts = event.get("timestamp")
        if not isinstance(ts, (int, float)):
            continue
        kind = event.get("type")
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        page.first_ts = ts if page.first_ts is None else min(page.first_ts, ts)
        page.last_ts = ts if page.last_ts is None else max(page.last_ts, ts)

        if kind == EventType.FULL_SNAPSHOT:
            page.mirror.load_snapshot(data.get("node") or {})
        elif kind == EventType.INCREMENTAL_SNAPSHOT:
            _gather_incremental(page, data, ts)
        elif kind == EventType.CUSTOM:
            tag = data.get("tag")
            if tag in tags.pageview:
                page.reactions.append(ts)
            elif tag in tags.error:
                page.errors.append(ts)

    page.reactions.sort()
    page.errors.sort()
    page.activity.sort()
    page.moves.sort(key=lambda m: m["ts"])
    return page


def _gather_incremental(page: _Page, data: dict, ts: float) -> None:
    source = data.get("source")
    if source == IncrementalSource.MUTATION:
        page.mirror.apply_mutation(data)
    elif source == IncrementalSource.MOUSE_MOVE:
        for position in data.get("positions") or []:
            if isinstance(position, dict):
                page.moves.append(
                    {
                        "ts": ts + (position.get("timeOffset") or 0),
                        "x": position.get("x"),
                        "y": position.get("y"),
                        "id": position.get("id"),
                    }
                )
    elif source == IncrementalSource.TOUCH_MOVE:
        page.activity.append(ts)
    elif source == IncrementalSource.MOUSE_INTERACTION:
        interaction = data.get("type")
        if interaction == MouseInteraction.CLICK:
            page.clicks.append(
                {"ts": ts, "id": data.get("id"), "x": data.get("x"), "y": data.get("y")}
            )
            page.activity.append(ts)
        elif interaction in (MouseInteraction.FOCUS, MouseInteraction.BLUR):
            page.focuses.append(
                {"ts": ts, "id": data.get("id"), "focus": interaction == MouseInteraction.FOCUS}
            )
        elif interaction == MouseInteraction.TOUCH_START:
            page.activity.append(ts)
    elif source == IncrementalSource.INPUT:
        text = data.get("text")
        length = len(text) if isinstance(text, str) else (1 if data.get("isChecked") else 0)
        page.inputs.append(
            {
                "ts": ts,
                "id": data.get("id"),
                "length": length,
                # True or False when the recorder says; None when it does not.
                "user": data.get("userTriggered"),
            }
        )
        page.activity.append(ts)
    elif source == IncrementalSource.SCROLL:
        page.scrolls.append({"ts": ts, "id": data.get("id"), "y": data.get("y")})
        page.activity.append(ts)
    elif source == IncrementalSource.SELECTION:
        page.selections.append({"ts": ts, "ranges": data.get("ranges") or []})
        page.activity.append(ts)

    if source in _REACTIONS:
        page.reactions.append(ts)


def _analyze_page(
    page_id: str,
    events: Iterable[dict],
    origin: float,
    counts: dict[str, int],
    markers: list[Marker],
    t: Thresholds,
    tags: _Tags,
    *,
    is_last_page: bool,
) -> None:
    page = _gather(events, t, tags)
    mirror = page.mirror
    counts["script_error"] += len(page.errors)

    def mark(kind: str, ts: float, node_id=None, x=None, y=None) -> None:
        counts[kind] += 1
        markers.append(
            {
                "t_ms": int(ts - origin),
                "page_id": page_id,
                "kind": kind,
                "selector": mirror.describe(node_id) if node_id is not None else "",
                "label": mirror.label(node_id) if node_id is not None else "",
                "x": x,
                "y": y,
            }
        )

    rage_indexes = _rage_indexes(page.clicks, t)
    for index, click in enumerate(page.clicks):
        kinds: list[str] = []
        if index in rage_indexes:
            kinds.append("rage")
        if _within_after(page.errors, click["ts"], t.error_window_ms):
            kinds.append("error")
        elif (
            not mirror.is_control(click["id"])
            and not _within_after(page.reactions, click["ts"], t.dead_window_ms)
            and not mirror.in_consent(click["id"])
        ):
            kinds.append("dead")
        for kind in kinds:
            mark(kind, click["ts"], click["id"], click["x"], click["y"])

    _near_misses(page, mark, t)
    _hesitations(page, mark, t)
    _scroll_hunt(page, mark, t)
    _forms(page, mark, t, is_last_page=is_last_page)
    _repeat_submits(page, rage_indexes, mark, t)
    _copy_outs(page, mark)
    if (
        is_last_page
        and page.first_ts is not None
        and page.last_ts is not None
        and not page.activity
        and page.last_ts - page.first_ts >= t.idle_exit_min_ms
    ):
        mark("idle_exit", page.first_ts)


def _rage_indexes(clicks: list[dict], t: Thresholds) -> set[int]:
    """Indexes of every click in a rage burst. A click in several
    overlapping bursts is still one click."""
    found: set[int] = set()
    for i in range(len(clicks)):
        burst = [i]
        for j in range(i + 1, len(clicks)):
            if clicks[j]["ts"] - clicks[i]["ts"] > t.rage_window_ms:
                break
            if _distance(clicks[burst[-1]], clicks[j]) <= t.rage_radius_px:
                burst.append(j)
        if len(burst) >= t.rage_min_clicks:
            found.update(burst)
    return found


def _near_misses(page: _Page, mark: Mark, t: Thresholds) -> None:
    mirror = page.mirror
    for missed, follow in zip(page.clicks, page.clicks[1:], strict=False):
        if follow["ts"] - missed["ts"] > t.near_miss_window_ms:
            continue
        target = mirror.pressable_ancestor(follow["id"])
        if target is None or target == mirror.pressable_ancestor(missed["id"]):
            continue
        if _distance(missed, follow) > t.near_miss_radius_px:
            continue
        if mirror.is_control(missed["id"]) or _any_between(
            page.reactions, missed["ts"], follow["ts"] + 1
        ):
            continue  # The first click did something, so it was not a miss.
        if mirror.in_consent(missed["id"]):
            continue
        if not _within_after(page.reactions, follow["ts"], t.dead_window_ms):
            continue
        mark("near_miss", missed["ts"], target, missed["x"], missed["y"])


def _hesitations(page: _Page, mark: Mark, t: Thresholds) -> None:
    """A resting pointer sends nothing, so a rest is a gap between two moves
    close together in space with no other activity between them."""
    mirror = page.mirror
    seen: set = set()
    moves = page.moves
    for current, after in zip(moves, moves[1:], strict=False):
        gap = after["ts"] - current["ts"]
        if gap < t.hesitation_min_ms or gap > t.hesitation_max_ms:
            continue
        if _distance(current, after) > t.hesitation_radius_px:
            continue
        if _any_between(page.activity, current["ts"], after["ts"]):
            continue
        node = current["id"]
        target = mirror.pressable_ancestor(node)
        if target is None:
            element = mirror.element_of(node)
            text = mirror.text_of(element) if element is not None else ""
            if not text or len(text) > t.short_text_chars or not mirror.is_leaf(element):
                continue  # Resting over prose, or over a block of things, is reading.
            target = element
        if target in seen or mirror.in_consent(target):
            continue
        # Paused, then pressed it: that was deciding, not hesitating.
        if any(
            c["ts"] >= current["ts"]
            and c["ts"] - after["ts"] <= t.hesitation_click_grace_ms
            and target in (mirror.pressable_ancestor(c["id"]), mirror.element_of(c["id"]))
            for c in page.clicks
        ):
            continue
        seen.add(target)
        mark("hesitation", current["ts"], target, current["x"], current["y"])


def _scroll_hunt(page: _Page, mark: Mark, t: Thresholds) -> None:
    document = page.mirror.document_id
    scrolls = [s for s in page.scrolls if isinstance(s.get("y"), (int, float))]
    if document is not None:
        # The page's own scrolling, when there is any; inner scrollers otherwise.
        scrolls = [s for s in scrolls if s["id"] == document] or scrolls
    breaks = sorted(
        [c["ts"] for c in page.clicks]
        + [i["ts"] for i in page.inputs]
        + [s["ts"] for s in page.selections]
    )
    reversals = 0
    direction = 0
    extreme = None
    last_break = None
    for scroll in scrolls:
        stamp = scroll["ts"]
        pending = [b for b in breaks if (last_break is None or b > last_break) and b <= stamp]
        if pending:
            last_break = pending[-1]
            reversals, direction, extreme = 0, 0, scroll["y"]
            continue
        y = scroll["y"]
        if extreme is None:
            extreme = y
            continue
        if direction == 0:
            if abs(y - extreme) >= t.scroll_hunt_min_leg_px:
                direction = 1 if y > extreme else -1
                extreme = y
            continue
        if (y - extreme) * direction > 0:
            extreme = y  # Further the same way.
        elif abs(y - extreme) >= t.scroll_hunt_min_leg_px:
            reversals += 1
            direction = -direction
            extreme = y
            if reversals >= t.scroll_hunt_reversals:
                mark("scroll_hunt", stamp)
                return


def _forms(page: _Page, mark: Mark, t: Thresholds, *, is_last_page: bool) -> None:
    mirror = page.mirror
    # When the visitor first reached each field: focused it, or clicked it.
    reached: dict = {}
    for event in page.focuses:
        if event["focus"]:
            reached.setdefault(event["id"], event["ts"])
    for click in page.clicks:
        reached.setdefault(click["id"], click["ts"])

    def by_visitor(entry: dict) -> bool:
        if entry.get("user") is not None:
            return bool(entry["user"])
        first = reached.get(entry["id"])
        return first is not None and first <= entry["ts"]

    fields = [i for i in page.inputs if mirror.is_field(i["id"]) and by_visitor(i)]
    typed: dict = {}
    for entry in fields:
        typed.setdefault(entry["id"], []).append(entry)

    # Skip: focused and left empty, while another field was filled afterwards.
    skipped: set = set()
    focus_at: dict = {}
    for event in page.focuses:
        node = event["id"]
        if not mirror.is_field(node):
            continue
        if event["focus"]:
            focus_at[node] = event["ts"]
            continue
        opened = focus_at.pop(node, None)
        if opened is None or node in skipped or node in typed:
            continue
        if any(entry["ts"] > event["ts"] and entry["id"] != node for entry in fields):
            skipped.add(node)
            mark("form_skip", opened, node)

    # Refill: the value was cut back and typed again.
    for node, entries in typed.items():
        peak = 0
        dropped = False
        for entry in entries:
            if dropped and entry["length"] >= t.refill_min_length:
                mark("form_refill", entry["ts"], node)
                break
            if peak >= t.refill_min_length and entry["length"] <= peak * t.refill_drop_share:
                dropped = True
            peak = max(peak, entry["length"])

    # Abandon: the visit ended here, with typing and no submit after it.
    if is_last_page and fields:
        last = max(fields, key=lambda entry: entry["ts"])
        submitted = any(
            click["ts"] >= last["ts"]
            and mirror.is_submit(mirror.pressable_ancestor(click["id"]) or click["id"])
            for click in page.clicks
        )
        if not submitted:
            mark("form_abandon", last["ts"], last["id"])


def _repeat_submits(page: _Page, rage_indexes: set[int], mark: Mark, t: Thresholds) -> None:
    mirror = page.mirror
    last_at: dict = {}
    marked: set = set()
    for index, click in enumerate(page.clicks):
        target = mirror.pressable_ancestor(click["id"]) or click["id"]
        if not mirror.is_submit(target) or target in marked:
            continue
        previous = last_at.get(target)
        last_at[target] = click["ts"]
        if previous is None or index in rage_indexes:
            continue
        if t.rage_window_ms < click["ts"] - previous <= t.repeat_submit_window_ms:
            marked.add(target)
            mark("repeat_submit", click["ts"], target, click["x"], click["y"])


def _copy_outs(page: _Page, mark: Mark) -> None:
    mirror = page.mirror
    marked: set = set()
    for selection in page.selections:
        for selected in selection["ranges"]:
            if not isinstance(selected, dict):
                continue
            start, end = selected.get("start"), selected.get("end")
            if start in marked or start not in mirror.texts:
                continue
            text = mirror.texts[start]
            if start == end:
                a, b = selected.get("startOffset") or 0, selected.get("endOffset") or 0
                if a == b:
                    continue  # A caret, not a selection.
                text = text[min(a, b) : max(a, b)]
            text = " ".join(text.split())
            if looks_like_contact(text):
                marked.add(start)
                mark("copy_out", selection["ts"], start)


def looks_like_contact(text: str) -> bool:
    """Is this (possibly masked) text shaped like a phone number, an email
    address or a street address?"""
    if not text or len(text) > 120:
        return False
    digits = sum(1 for ch in text if ch.isdigit() or ch == "▫")
    if digits >= 7 and _PHONE_RE.match(text):
        return True
    return bool(_EMAIL_RE.search(text) or _ADDRESS_RE.match(text))


def _distance(a: dict, b: dict) -> float:
    try:
        return math.hypot(float(a["x"]) - float(b["x"]), float(a["y"]) - float(b["y"]))
    except (TypeError, ValueError, KeyError):
        return float("inf")


def _within_after(sorted_times: list[float], start: float, window_ms: float) -> bool:
    """Is there a time in `(start, start + window_ms]`?"""
    for ts in sorted_times:
        if ts <= start:
            continue
        return ts - start <= window_ms
    return False


def _any_between(sorted_times: list[float], start: float, end: float) -> bool:
    return any(start < ts < end for ts in sorted_times)
