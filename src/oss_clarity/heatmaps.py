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
from django.utils import timezone

from .choices import HitType
from .models import HeatmapClickBucket, HeatmapPage, HeatmapScrollBucket, Hit, Site

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
