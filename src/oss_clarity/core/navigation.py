"""Quick backs and loops: how a visit moved between pages.

Both are read from a visit's page views rather than from a recording, so every
visit has them, recorded or not.

* Quick back: page views A, B, A with less than `quick_back_seconds` spent on
  B. The visitor opened B and went straight back: B was not what they wanted.
* Loop: page views A, B, A, B in a row: back and forth, finding it in neither
  place. Counted without overlap, so A B A B A B is one loop and the start of
  another, not three.

Page views must be in the order they happened; `ordered` puts them there.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from typing import Any, TypeVar

DEFAULT_QUICK_BACK_SECONDS = 5
#: Occurrences kept per visit. A visit that bounces between two pages fifty
#: times says so in its counts; the list stops telling anything new long before.
MAX_OCCURRENCES = 20

Row = TypeVar("Row")
#: A moment: a `datetime`, or seconds as a number (for example a Unix time).
Moment = datetime | float | int


def ordered(rows: Sequence[Row], key: Callable[[Row], tuple[int | None, Any]]) -> list[Row]:
    """One visit's page views in the order they happened.

    `key(row)` returns `(sequence, time)`. The tracker numbers its own hits,
    and that number is the true order: separate requests can arrive in a
    different order than they were sent. It is only trustworthy when every
    row has one, because a visit can span a tracker update and mix numbered
    hits with unnumbered ones. So rows are sorted by sequence when all have
    one, by time otherwise. Ties keep arrival (time) order.
    """
    rows = sorted(rows, key=lambda row: key(row)[1])
    if all(key(row)[0] is not None for row in rows):
        return sorted(rows, key=lambda row: key(row)[0])
    return rows


def count(
    page_views: Sequence[tuple[str, Moment]],
    quick_back_seconds: float = DEFAULT_QUICK_BACK_SECONDS,
) -> dict[str, Any]:
    """`{quick_backs, loops, occurrences}` for one visit.

    `page_views` is `[(path, time)]`, already in visit order.
    """
    occurrences: list[dict[str, str]] = []
    quick_backs = 0
    for i in range(1, len(page_views) - 1):
        (before, _), (here, here_at), (after, after_at) = page_views[i - 1 : i + 2]
        if (
            before == after
            and here != before
            and _seconds_between(here_at, after_at) < quick_back_seconds
        ):
            quick_backs += 1
            occurrences.append({"kind": "quick_back", "a": before or "", "b": here or ""})

    paths = [path for path, _ in page_views]
    loops = 0
    i = 0
    while i + 3 < len(paths):
        a, b, c, d = paths[i : i + 4]
        if a != b and a == c and b == d:
            loops += 1
            occurrences.append({"kind": "loop", "a": a or "", "b": b or ""})
            i += 3
        else:
            i += 1

    return {
        "quick_backs": quick_backs,
        "loops": loops,
        "occurrences": occurrences[:MAX_OCCURRENCES],
    }


def _seconds_between(start: Moment, end: Moment) -> float:
    delta = end - start  # type: ignore[operator]
    if isinstance(delta, timedelta):
        return delta.total_seconds()
    return float(delta)
