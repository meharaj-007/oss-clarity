from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from oss_clarity.heatmaps import rollup_day
from oss_clarity.models import Recording, SessionNavigation

from .conftest import make_hit, make_recording

pytestmark = pytest.mark.django_db

API = "/oc-api"


@pytest.fixture
def staff(client):
    user = get_user_model().objects.create_user("staff", password="x", is_staff=True)
    client.force_login(user)
    return client


def allow_everyone(request):
    return True


def test_only_staff_by_default(client, site):
    assert client.get(f"{API}/sites/{site.pk}/recordings/").status_code == 403
    user = get_user_model().objects.create_user("plain", password="x")
    client.force_login(user)
    assert client.get(f"{API}/sites/{site.pk}/recordings/").status_code == 403


def test_the_permission_hook_decides(client, site, settings):
    settings.OSS_CLARITY = {"API_PERMISSION": "tests.django.test_api.allow_everyone"}
    response = client.get(f"{API}/sites/{site.pk}/recordings/")
    assert response.status_code == 200 and response["Cache-Control"] == "private, no-store"


def test_writes_are_not_allowed(staff, site):
    assert staff.post(f"{API}/sites/{site.pk}/recordings/").status_code == 405


def test_recordings_filter_by_any_of_the_signals_and_favourites(staff, site):
    make_recording(site, session_id="rage", rage_clicks=2)
    make_recording(site, session_id="loop", loops=1, is_favorite=True)
    make_recording(site, session_id="calm")
    make_recording(
        site, session_id="old", rage_clicks=1, started_at=timezone.now() - timedelta(days=60)
    )

    def ids(query=""):
        body = staff.get(f"{API}/sites/{site.pk}/recordings/{query}").json()
        return sorted(r["session_id"] for r in body["data"])

    assert ids() == ["calm", "loop", "rage"]
    assert ids("?has=rage") == ["rage"]
    assert ids("?has=rage,loop") == ["loop", "rage"]
    assert ids("?has=nonsense") == ["calm", "loop", "rage"]
    assert ids("?favorites=1") == ["loop"]
    assert ids("?has=rage&days=90") == ["old", "rage"]


def test_recordings_are_paginated(staff, site):
    for n in range(55):
        make_recording(site, session_id=f"s-{n}")
    first = staff.get(f"{API}/sites/{site.pk}/recordings/").json()
    assert (first["count"], first["pages"], len(first["data"])) == (55, 2, 50)
    second = staff.get(f"{API}/sites/{site.pk}/recordings/?page=2").json()
    assert len(second["data"]) == 5
    assert staff.get(f"{API}/sites/{site.pk}/recordings/?page=99").json()["page"] == 2


def test_a_recording_lists_its_pages_and_their_events(staff, site):
    recording = make_recording(
        site,
        pages=[
            ("p-0", [{"type": 2, "timestamp": 1_000}, {"type": 3, "timestamp": 1_500}]),
            ("p-1", [{"type": 2, "timestamp": 9_000}]),
        ],
        markers=[{"t_ms": 500, "kind": "dead"}],
    )
    detail = staff.get(f"{API}/recordings/{recording.pk}/").json()
    assert detail["id"] == str(recording.pk) and detail["markers"] == [
        {"t_ms": 500, "kind": "dead"}
    ]
    assert [p["page_seq"] for p in detail["pages"]] == [0, 1]
    assert detail["signals"]["rage"] == 0
    events = staff.get(detail["pages"][0]["events_url"]).json()["events"]
    assert [e["timestamp"] for e in events] == [1_000, 1_500]
    assert staff.get(f"{API}/recordings/{recording.pk}/pages/7/events/").status_code == 404
    missing = "00000000-0000-0000-0000-000000000000"
    assert staff.get(f"{API}/recordings/{missing}/").status_code == 404


def test_sessions_carry_quick_backs_and_loops_on_every_row(staff, site):
    for session in ("s-back", "s-calm", "s-rec"):
        make_hit(site, session_id=session, visitor_id=f"v-{session}")
    SessionNavigation.objects.create(
        site=site,
        session_id="s-back",
        started_at=timezone.now(),
        ended_at=timezone.now(),
        quick_backs=1,
    )
    recording = make_recording(site, session_id="s-rec", rage_clicks=1)

    rows = {
        r["session_id"]: r for r in staff.get(f"{API}/sites/{site.pk}/sessions/").json()["data"]
    }
    assert (rows["s-back"]["quick_backs"], rows["s-back"]["loops"]) == (1, 0)
    assert (
        rows["s-calm"]["quick_backs"],
        rows["s-calm"]["loops"],
        rows["s-calm"]["recording"],
    ) == (
        0,
        0,
        None,
    )
    assert rows["s-rec"]["recording"] == str(recording.pk) and rows["s-rec"]["pageviews"] == 1

    def ids(query):
        body = staff.get(f"{API}/sites/{site.pk}/sessions/{query}").json()
        return sorted(r["session_id"] for r in body["data"])

    assert ids("?has=quick_back") == ["s-back"]
    assert ids("?has=rage") == ["s-rec"]
    assert ids("?has=quick_back,rage") == ["s-back", "s-rec"]
    assert ids("?recorded=1") == ["s-rec"]


def test_heatmap_pages_and_one_map(staff, site):
    make_hit(site, path="/pricing")
    make_hit(site, path="/pricing", device_class="mobile")
    make_hit(site, hit_type="click", path="/pricing", x=10, y=10, viewport_w=1280)
    make_hit(site, hit_type="pageleave", path="/pricing", scroll_depth_pct=55)
    rollup_day(site.pk, timezone.now().date())

    pages = staff.get(f"{API}/sites/{site.pk}/heatmaps/").json()["data"]
    assert pages == [
        {
            "path": "/pricing",
            "pageviews": 2,
            "clicks": 1,
            "pageleaves": 1,
            "devices": {"desktop": 1, "mobile": 1},
        }
    ]
    data = staff.get(f"{API}/sites/{site.pk}/heatmap/?path=/pricing/&device=desktop").json()
    assert data["path"] == "/pricing" and data["coverage"]["clicks"] == 1
    assert data["pixel_rows"] == [{"bucket_x": 0, "bucket_y": 0, "count": 1}]
    assert [s["share"] for s in data["scroll"][:7]] == [100.0] * 6 + [0.0]
    assert data["backdrop"] is None
    assert staff.get(f"{API}/sites/{site.pk}/heatmap/?device=fridge").status_code == 400


def test_the_heatmap_backdrop_is_the_newest_recording_of_that_page_and_device(staff, site):
    make_hit(site, path="/pricing")
    rollup_day(site.pk, timezone.now().date())
    phone = make_recording(site, session_id="phone")
    make_hit(site, session_id="phone", device_class="mobile")
    desktop = make_recording(site, session_id="desk")
    make_hit(site, session_id="desk", device_class="desktop")
    Recording.objects.all()  # both recorded /pricing
    from oss_clarity.models import RecordingChunk

    RecordingChunk.objects.update(url="https://shop.example/pricing?x=1")
    data = staff.get(f"{API}/sites/{site.pk}/heatmap/?path=/pricing&device=desktop").json()
    assert data["backdrop"]["recording"] == str(desktop.pk)
    assert data["backdrop"]["events_url"] == f"{API}/recordings/{desktop.pk}/pages/0/events/"
    mobile = staff.get(f"{API}/sites/{site.pk}/heatmap/?path=/pricing&device=mobile").json()
    assert mobile["backdrop"]["recording"] == str(phone.pk)
