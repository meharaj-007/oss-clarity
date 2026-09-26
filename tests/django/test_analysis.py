from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from oss_clarity.analysis import finalize_and_analyze, rollup_navigation, rollup_sessions
from oss_clarity.models import Recording, SessionNavigation, Site

from .conftest import make_hit, make_recording

pytestmark = pytest.mark.django_db


def widget_page(ts=1_000):
    return {
        "type": 2,
        "timestamp": ts,
        "data": {
            "node": {
                "type": 0,
                "id": 1,
                "childNodes": [
                    {
                        "type": 2,
                        "tagName": "html",
                        "id": 2,
                        "attributes": {},
                        "childNodes": [
                            {"type": 2, "tagName": "button", "id": 10, "attributes": {"id": "buy"}},
                        ],
                    }
                ],
            }
        },
    }


def click(ts, node=10, x=100, y=100):
    return {
        "type": 3,
        "timestamp": ts,
        "data": {"source": 2, "type": 2, "id": node, "x": x, "y": y},
    }


RAGE = [widget_page(), click(2_000), click(2_300, x=105), click(2_600, x=110)]


def quiet(minutes=45):
    return timezone.now() - timedelta(minutes=minutes)


def test_a_quiet_recording_is_closed_analysed_and_marked(site):
    recording = make_recording(site, pages=[("p-0", RAGE)], last_event_at=quiet())
    result = finalize_and_analyze()
    assert result == {"closed": 1, "analyzed": 1, "failed": 0}
    recording.refresh_from_db()
    assert recording.status == "complete" and recording.analyzed_at is not None
    assert (recording.rage_clicks, recording.dead_clicks) == (3, 3)
    assert {m["kind"] for m in recording.markers} == {"rage", "dead"}
    assert recording.markers[0]["selector"] == "button#buy"


def test_a_recording_still_receiving_chunks_is_left_alone(site):
    recording = make_recording(site, pages=[("p-0", RAGE)], last_event_at=quiet(5))
    assert finalize_and_analyze() == {"closed": 0, "analyzed": 0, "failed": 0}
    recording.refresh_from_db()
    assert recording.status == "recording" and recording.analyzed_at is None


def test_a_capped_recording_is_analysed_once_quiet(site):
    recording = make_recording(site, pages=[("p-0", RAGE)], status="capped", last_event_at=quiet())
    assert finalize_and_analyze()["analyzed"] == 1
    recording.refresh_from_db()
    assert recording.status == "capped" and recording.rage_clicks == 3


def test_running_twice_analyses_once(site):
    make_recording(site, pages=[("p-0", RAGE)], last_event_at=quiet())
    finalize_and_analyze()
    assert finalize_and_analyze() == {"closed": 0, "analyzed": 0, "failed": 0}


def test_thresholds_and_tags_come_from_settings(site, settings):
    settings.OSS_CLARITY = {"THRESHOLDS": {"rage_min_clicks": 4}}
    recording = make_recording(site, pages=[("p-0", RAGE)], last_event_at=quiet())
    finalize_and_analyze()
    recording.refresh_from_db()
    assert recording.rage_clicks == 0 and recording.dead_clicks == 3


def test_quick_backs_and_loops_come_from_the_page_views(site):
    recording = make_recording(site, session_id="s-q", last_event_at=quiet())
    for seq, (path, ago) in enumerate([("/", 60), ("/pricing", 58), ("/", 57), ("/about", 30)]):
        make_hit(site, session_id="s-q", path=path, sequence=seq, ago=timedelta(seconds=ago))
    finalize_and_analyze()
    recording.refresh_from_db()
    assert (recording.quick_backs, recording.loops) == (1, 0)


def test_one_broken_recording_does_not_stop_the_others(site, monkeypatch):
    from oss_clarity import analysis

    good = make_recording(site, session_id="good", pages=[("p-0", RAGE)], last_event_at=quiet())
    bad = make_recording(site, session_id="bad", last_event_at=quiet(50))
    real = analysis.all_events

    def flaky(recording):
        if recording.pk == bad.pk:
            raise OSError("storage down")
        return real(recording)

    monkeypatch.setattr(analysis, "all_events", flaky)
    assert finalize_and_analyze() == {"closed": 2, "analyzed": 1, "failed": 1}
    assert Recording.objects.get(pk=good.pk).analyzed_at is not None
    assert Recording.objects.get(pk=bad.pk).analyzed_at is None


# --- navigation rollup ---------------------------------------------------------------


def visit(site, session_id, path, *, visitor_id="v"):
    for seq, (page, ago) in enumerate(path):
        make_hit(
            site,
            session_id=session_id,
            visitor_id=visitor_id,
            path=page,
            sequence=seq,
            ago=timedelta(seconds=ago),
        )


def test_the_rollup_stores_only_visits_with_a_quick_back_or_loop(site):
    visit(site, "s-back", [("/", 60), ("/pricing", 58), ("/", 56)], visitor_id="v-back")
    visit(site, "s-loop", [("/a", 300), ("/b", 240), ("/a", 180), ("/b", 120)])
    visit(site, "s-calm", [("/", 300), ("/about", 200)])
    Site.objects.filter(pk=site.pk).update(last_hit_at=timezone.now())

    assert rollup_navigation() == {"sites": 1, "sessions": 3, "stored": 2}
    rows = {r.session_id: r for r in SessionNavigation.objects.all()}
    assert set(rows) == {"s-back", "s-loop"}
    assert (rows["s-back"].quick_backs, rows["s-back"].visitor_id) == (1, "v-back")
    assert rows["s-loop"].loops == 1
    assert rows["s-back"].occurrences == [{"kind": "quick_back", "a": "/", "b": "/pricing"}]


def test_a_row_that_no_longer_holds_is_removed_and_reruns_change_nothing(site):
    visit(site, "s-1", [("/", 60), ("/pricing", 58), ("/", 56)])
    rollup_sessions(site.pk, ["s-1"])
    assert SessionNavigation.objects.count() == 1
    # A late page view lands between: /, /pricing, /contact, / is nothing.
    make_hit(site, session_id="s-1", path="/contact", ago=timedelta(seconds=57))
    rollup_sessions(site.pk, ["s-1"])
    rollup_sessions(site.pk, ["s-1"])
    assert SessionNavigation.objects.count() == 0


def test_sites_without_recent_hits_are_skipped(site):
    visit(site, "s-back", [("/", 60), ("/pricing", 58), ("/", 56)])
    Site.objects.filter(pk=site.pk).update(last_hit_at=timezone.now() - timedelta(days=1))
    assert rollup_navigation()["sites"] == 0
