from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from django.utils import timezone

from oss_clarity.heatmaps import rollup_day, rollup_recent, selector_hash
from oss_clarity.models import HeatmapClickBucket, HeatmapPage, HeatmapScrollBucket, Hit, Site

from .conftest import make_hit

pytestmark = pytest.mark.django_db


def today():
    return timezone.now().astimezone(UTC).date()


def seed(site):
    make_hit(site, path="/pricing")
    make_hit(site, path="/pricing")
    make_hit(site, path="/pricing", device_class="mobile")
    make_hit(
        site,
        hit_type="click",
        path="/pricing",
        element_selector="button#buy",
        page_region="main",
        x=640,
        y=110,
        viewport_w=1280,
        rel_x=999,
        rel_y=0,
    )
    make_hit(site, hit_type="click", path="/pricing", x=20, y=2_000, viewport_w=1280)
    make_hit(site, hit_type="pageleave", path="/pricing", scroll_depth_pct=100)
    make_hit(site, hit_type="pageleave", path="/pricing", scroll_depth_pct=34)
    # Left out: a crawler, and a hit with no device class.
    make_hit(site, path="/pricing", is_bot=True)
    make_hit(site, path="/pricing", device_class="")


def test_the_rollup_counts_pages_clicks_and_depth(site):
    seed(site)
    result = rollup_day(site.pk, today())
    assert result == {"pages": 2, "element_rows": 1, "pixel_rows": 2, "scroll_rows": 2}

    desktop = HeatmapPage.objects.get(path="/pricing", device_class="desktop")
    assert (desktop.pageviews, desktop.clicks, desktop.pageleaves) == (2, 2, 2)
    assert HeatmapPage.objects.get(device_class="mobile").pageviews == 1

    element = HeatmapClickBucket.objects.exclude(selector_hash="").get()
    assert (element.selector, element.page_region, element.bucket_x, element.bucket_y) == (
        "button#buy",
        "main",
        9,
        0,
    )
    assert element.selector_hash == selector_hash("button#buy")
    pixels = {(b.bucket_x, b.bucket_y) for b in HeatmapClickBucket.objects.filter(selector_hash="")}
    assert pixels == {(25, 4), (0, 80)}
    depths = dict(HeatmapScrollBucket.objects.values_list("depth_decile", "views"))
    assert depths == {10: 1, 3: 1}


def test_rebuilding_a_day_gives_the_same_rows(site):
    seed(site)
    rollup_day(site.pk, today())
    first = sorted(HeatmapClickBucket.objects.values_list("bucket_x", "bucket_y", "count"))
    rollup_day(site.pk, today())
    assert sorted(HeatmapClickBucket.objects.values_list("bucket_x", "bucket_y", "count")) == first
    assert HeatmapPage.objects.count() == 2


def test_a_day_is_bounded_in_utc(site):
    yesterday = datetime.combine(today() - timedelta(days=1), datetime.min.time(), tzinfo=UTC)
    hit = make_hit(site, path="/old")
    Hit.objects.filter(pk=hit.pk).update(created_at=yesterday + timedelta(hours=23, minutes=59))
    rollup_day(site.pk, today())
    assert not HeatmapPage.objects.filter(path="/old").exists()
    rollup_day(site.pk, today() - timedelta(days=1))
    assert HeatmapPage.objects.get(path="/old").day == today() - timedelta(days=1)


def test_rollup_recent_covers_today_and_yesterday_for_active_sites(site):
    seed(site)
    idle = Site.objects.create(name="Idle", domain="idle.example")
    Site.objects.filter(pk=site.pk).update(last_hit_at=timezone.now())
    assert rollup_recent() == {"sites": 1, "days": 2}
    assert not HeatmapPage.objects.filter(site=idle).exists()
