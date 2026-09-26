"""Heatmaps: where visitors click and how far they read, per page.

The rollup folds one UTC day of one site's hits into three small tables,
rebuilding that (site, day) from scratch, so running it twice equals running
it once. Screens read only those tables: raw hits are never read at screen
time.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .choices import HitType
from .models import (
    HeatmapClickBucket,
    HeatmapPage,
    HeatmapScrollBucket,
    Hit,
    RecordingChunk,
    Site,
)

#: Element rows: a 10 × 10 grid over the clicked element's own box.
ELEMENT_GRID = 10
#: Pixel rows: 2 % columns of the window's width, 25 px rows of the document.
PIXEL_COLUMNS = 50
PIXEL_ROW_PX = 25
#: `bucket_y` is a small integer: 32 767 rows of 25 px is an 819 000 px page.
MAX_PIXEL_ROW = 32_767


def selector_hash(selector: str) -> str:
    return hashlib.sha1(selector.encode("utf-8")).hexdigest()[:16]


def day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, tzinfo=UTC)
    return start, start + timedelta(days=1)


def rollup_day(site_id, day: date) -> dict[str, int]:
    """Rebuild one site's buckets for one UTC day from its hits. Crawlers and
    hits without a device class (no layout to draw them on) are left out."""
    start, end = day_bounds(day)
    hits = Hit.objects.filter(
        site_id=site_id, created_at__gte=start, created_at__lt=end, is_bot=False
    ).exclude(device_class="")

    pages: dict[tuple, list[int]] = defaultdict(lambda: [0, 0, 0])
    elements: dict[tuple, int] = defaultdict(int)
    pixels: dict[tuple, int] = defaultdict(int)
    depths: dict[tuple, int] = defaultdict(int)

    for path, cls in (
        hits.filter(hit_type=HitType.PAGEVIEW)
        .values_list("path", "device_class")
        .iterator(chunk_size=5000)
    ):
        pages[(path, cls)][0] += 1

    for path, cls, selector, region, x, y, viewport_w, rel_x, rel_y in (
        hits.filter(hit_type=HitType.CLICK, x__isnull=False, y__isnull=False)
        .values_list(
            "path", "device_class", "element_selector", "page_region",
            "x", "y", "viewport_w", "rel_x", "rel_y",
        )
        .iterator(chunk_size=5000)
    ):  # fmt: skip
        pages[(path, cls)][1] += 1
        if selector and rel_x is not None and rel_y is not None:
            bx = min(ELEMENT_GRID - 1, rel_x * ELEMENT_GRID // 1000)
            by = min(ELEMENT_GRID - 1, rel_y * ELEMENT_GRID // 1000)
            elements[(path, cls, selector, region or "", bx, by)] += 1
        if viewport_w:
            px = min(PIXEL_COLUMNS - 1, max(0, x) * PIXEL_COLUMNS // viewport_w)
            py = min(MAX_PIXEL_ROW, max(0, y) // PIXEL_ROW_PX)
            pixels[(path, cls, px, py)] += 1

    for path, cls, depth in (
        hits.filter(hit_type=HitType.PAGELEAVE, scroll_depth_pct__isnull=False)
        .values_list("path", "device_class", "scroll_depth_pct")
        .iterator(chunk_size=5000)
    ):
        pages[(path, cls)][2] += 1
        depths[(path, cls, min(10, max(0, depth) // 10))] += 1

    common = {"site_id": site_id, "day": day}
    with transaction.atomic():
        for model in (HeatmapPage, HeatmapClickBucket, HeatmapScrollBucket):
            model.objects.filter(**common).delete()
        HeatmapPage.objects.bulk_create(
            [
                HeatmapPage(
                    **common, path=path, device_class=cls,
                    pageviews=v[0], clicks=v[1], pageleaves=v[2],
                )
                for (path, cls), v in pages.items()
            ],
            batch_size=1000,
        )  # fmt: skip
        HeatmapClickBucket.objects.bulk_create(
            [
                HeatmapClickBucket(
                    **common, path=path, device_class=cls,
                    selector_hash=selector_hash(selector), selector=selector[:255],
                    page_region=region[:64], bucket_x=bx, bucket_y=by, count=count,
                )
                for (path, cls, selector, region, bx, by), count in elements.items()
            ]
            + [
                HeatmapClickBucket(
                    **common, path=path, device_class=cls, bucket_x=px, bucket_y=py, count=count
                )
                for (path, cls, px, py), count in pixels.items()
            ],
            batch_size=2000,
        )  # fmt: skip
        HeatmapScrollBucket.objects.bulk_create(
            [
                HeatmapScrollBucket(
                    **common, path=path, device_class=cls, depth_decile=decile, views=views
                )
                for (path, cls, decile), views in depths.items()
            ],
            batch_size=1000,
        )
    return {
        "pages": len(pages),
        "element_rows": len(elements),
        "pixel_rows": len(pixels),
        "scroll_rows": len(depths),
    }


def rollup_recent(*, now=None, days: int = 2) -> dict[str, int]:
    """Today and yesterday (UTC) for every site that reported in that time.
    Yesterday is rebuilt too, so hits that arrived after the last run before
    midnight are counted. Pass a larger `days` to backfill."""
    now = now or timezone.now()
    today = now.astimezone(UTC).date()
    since = datetime.combine(today - timedelta(days=days - 1), time.min, tzinfo=UTC)
    totals = {"sites": 0, "days": 0}
    for site_id in Site.objects.filter(last_hit_at__gte=since).values_list("pk", flat=True):
        totals["sites"] += 1
        for offset in range(days):
            rollup_day(site_id, today - timedelta(days=offset))
            totals["days"] += 1
    return totals


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

#: Rows one map carries at most. The tail past these is single clicks that
#: would not change the picture.
MAX_ELEMENT_ROWS = 4000
MAX_PIXEL_ROWS = 6000
MAX_ELEMENTS_LISTED = 25
MAX_PAGES_LISTED = 200
#: How many recent page openings are searched for a backdrop snapshot.
BACKDROP_CANDIDATES = 300


def day_range(days: int) -> tuple[date, date]:
    last = timezone.now().astimezone(UTC).date()
    return last - timedelta(days=max(1, min(int(days), 400)) - 1), last


def pages_summary(site: Site, *, days: int = 30) -> list[dict]:
    """Every page with data in the window, busiest first, with its page
    views per device class."""
    first, last = day_range(days)
    rows = (
        HeatmapPage.objects.filter(site=site, day__gte=first, day__lte=last)
        .values("path", "device_class")
        .annotate(pageviews=Sum("pageviews"), clicks=Sum("clicks"), pageleaves=Sum("pageleaves"))
    )
    by_page: dict[str, dict] = {}
    for row in rows:
        page = by_page.setdefault(
            row["path"],
            {"path": row["path"], "pageviews": 0, "clicks": 0, "pageleaves": 0, "devices": {}},
        )
        for key in ("pageviews", "clicks", "pageleaves"):
            page[key] += row[key] or 0
        page["devices"][row["device_class"]] = row["pageviews"] or 0
    ordered = sorted(by_page.values(), key=lambda p: (-p["pageviews"], -p["clicks"], p["path"]))
    return ordered[:MAX_PAGES_LISTED]


def heatmap(site: Site, *, path: str, device: str, days: int = 30) -> dict:
    """Everything one map needs: coverage, clicks by element and by pixel,
    clicks per page area, scroll reach, and a snapshot to draw them on."""
    from .collect import page_path

    first, last = day_range(days)
    path = page_path(path)
    scope = {
        "site": site,
        "path": path,
        "device_class": device,
        "day__gte": first,
        "day__lte": last,
    }
    coverage = HeatmapPage.objects.filter(**scope).aggregate(
        pageviews=Sum("pageviews"), clicks=Sum("clicks"), pageleaves=Sum("pageleaves")
    )
    clicks = HeatmapClickBucket.objects.filter(**scope)
    on_elements = clicks.exclude(selector_hash="")
    element_rows = list(
        on_elements.values("selector", "page_region", "bucket_x", "bucket_y")
        .annotate(count=Sum("count"))
        .order_by("-count")[:MAX_ELEMENT_ROWS]
    )
    pixel_rows = list(
        clicks.filter(selector_hash="")
        .values("bucket_x", "bucket_y")
        .annotate(count=Sum("count"))
        .order_by("-count")[:MAX_PIXEL_ROWS]
    )
    elements = list(
        on_elements.values("selector", "page_region")
        .annotate(clicks=Sum("count"))
        .order_by("-clicks")[:MAX_ELEMENTS_LISTED]
    )
    areas = list(
        on_elements.values("page_region").annotate(clicks=Sum("count")).order_by("-clicks")
    )
    stopped = {
        row["depth_decile"]: row["views"]
        for row in HeatmapScrollBucket.objects.filter(**scope)
        .values("depth_decile")
        .annotate(views=Sum("views"))
    }
    total = sum(stopped.values())
    # Reach: the share of page views whose deepest point was at or past each
    # tenth of the page. Everyone who reported reached the top.
    reach, remaining = [], total
    for decile in range(11):
        share = round(remaining * 100 / total, 1) if total else 0.0
        reach.append({"depth_pct": decile * 10, "views": remaining, "share": share})
        remaining -= stopped.get(decile, 0)

    return {
        "path": path,
        "device_class": device,
        "range": {"start": first.isoformat(), "end": last.isoformat()},
        "coverage": {key: coverage[key] or 0 for key in ("pageviews", "clicks", "pageleaves")},
        "grid": {
            "element": ELEMENT_GRID,
            "pixel_columns": PIXEL_COLUMNS,
            "pixel_row_px": PIXEL_ROW_PX,
        },
        "element_rows": element_rows,
        "pixel_rows": pixel_rows,
        "elements": elements,
        "areas": areas,
        "scroll": reach,
        "backdrop": backdrop(site, path=path, device=device),
    }


def backdrop(site: Site, *, path: str, device: str) -> dict | None:
    """The newest recorded opening of this page on this device class, as
    `{recording, page_seq, recorded_at, mask_mode}`, or None. Only recent
    openings are searched: a page nobody has recorded lately is better drawn
    on a plain frame than on an old snapshot."""
    from .collect import page_path

    candidates = list(
        RecordingChunk.objects.filter(recording__site=site, chunk_seq=0)
        .order_by("-created_at")
        .values(
            "recording_id",
            "recording__session_id",
            "page_seq",
            "url",
            "created_at",
            "recording__mask_mode",
        )[:BACKDROP_CANDIDATES]
    )
    matching = [c for c in candidates if page_path(c["url"]) == path]
    if not matching:
        return None
    devices = dict(
        Hit.objects.filter(
            site=site,
            session_id__in={c["recording__session_id"] for c in matching},
            hit_type=HitType.PAGEVIEW,
        )
        .exclude(device_class="")
        .values_list("session_id", "device_class")
    )
    for chunk in matching:
        if devices.get(chunk["recording__session_id"]) == device:
            return {
                "recording": str(chunk["recording_id"]),
                "page_seq": chunk["page_seq"],
                "recorded_at": chunk["created_at"].isoformat(),
                "mask_mode": chunk["recording__mask_mode"],
            }
    return None
