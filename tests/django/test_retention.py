from __future__ import annotations

from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import CommandError, call_command
from django.urls import reverse
from django.utils import timezone

from oss_clarity import storage
from oss_clarity.models import (
    HeatmapPage,
    Hit,
    Recording,
    RecordingChunk,
    SessionNavigation,
    Site,
)
from oss_clarity.retention import erase_visitor, prune

from .conftest import make_hit, make_recording

pytestmark = pytest.mark.django_db


def days_ago(n):
    return timezone.now() - timedelta(days=n)


def stored_keys():
    return list(RecordingChunk.objects.values_list("storage_key", flat=True))


def exists(key):
    return storage.get_storage().exists(key)


def navigation(site, session_id, visitor_id="v-1", ended=None):
    ended = ended or timezone.now()
    return SessionNavigation.objects.create(
        site=site,
        session_id=session_id,
        visitor_id=visitor_id,
        started_at=ended,
        ended_at=ended,
        quick_backs=1,
    )


def failing_delete(monkeypatch):
    real = storage.get_storage()

    class Broken:
        def delete(self, key):
            raise OSError("storage down")

        def __getattr__(self, name):
            return getattr(real, name)

    monkeypatch.setattr(storage, "get_storage", lambda: Broken())


# --- prune ------------------------------------------------------------------------------


def test_old_recordings_go_with_their_stored_chunks(site):
    old = make_recording(site, session_id="old", started_at=days_ago(31))
    keys = stored_keys()
    new = make_recording(site, session_id="new", started_at=days_ago(10))
    assert prune()["recordings"] == 1
    assert list(Recording.objects.values_list("pk", flat=True)) == [new.pk]
    assert not any(exists(key) for key in keys)
    assert not RecordingChunk.objects.filter(recording_id=old.pk).exists()


def test_favourites_outlive_the_recording_window_but_not_the_hit_window(site, settings):
    settings.OSS_CLARITY = {"HIT_RETENTION_DAYS": 90, "RECORDING_RETENTION_DAYS": 30}
    make_recording(site, session_id="kept", started_at=days_ago(31), is_favorite=True)
    make_recording(site, session_id="gone", started_at=days_ago(31))
    assert prune()["recordings"] == 1
    assert Recording.objects.get().session_id == "kept"
    settings.OSS_CLARITY = {"HIT_RETENTION_DAYS": 20, "RECORDING_RETENTION_DAYS": 30}
    assert prune()["recordings"] == 1


def test_a_recording_whose_chunks_cannot_be_deleted_is_kept(site, monkeypatch):
    make_recording(site, started_at=days_ago(31))
    failing_delete(monkeypatch)
    result = prune()
    assert (result["recordings"], result["recordings_kept"]) == (0, 1)
    assert Recording.objects.count() == 1 and RecordingChunk.objects.count() == 1


def test_old_hits_navigation_and_buckets_are_pruned(site, settings):
    make_hit(site, ago=timedelta(days=31))
    make_hit(site, ago=timedelta(days=1))
    navigation(site, "old", ended=days_ago(31))
    navigation(site, "new")
    for age in (401, 10):
        HeatmapPage.objects.create(site=site, path="/", device_class="desktop", day=days_ago(age))
    result = prune()
    assert (result["hits"], result["navigation"], result["heatmap_rows"]) == (1, 1, 1)
    assert Hit.objects.count() == 1 and SessionNavigation.objects.get().session_id == "new"
    assert HeatmapPage.objects.count() == 1
    assert prune() == {
        "hits": 0,
        "navigation": 0,
        "recordings": 0,
        "recordings_kept": 0,
        "heatmap_rows": 0,
    }


# --- erasing one visitor ---------------------------------------------------------------


def seed_two_visitors(site):
    other_site = Site.objects.create(name="Other", domain="other.example")
    make_recording(site, session_id="s-1", visitor_id="v-1", is_favorite=True)
    make_recording(site, session_id="s-2", visitor_id="v-1")
    make_recording(other_site, session_id="s-3", visitor_id="v-1")
    make_recording(site, session_id="s-4", visitor_id="v-2")
    for session, visitor, where in (("s-1", "v-1", site), ("s-3", "v-1", other_site)):
        make_hit(where, session_id=session, visitor_id=visitor)
        navigation(where, session, visitor)
    make_hit(site, session_id="s-4", visitor_id="v-2")
    return other_site


def test_erasing_a_visitor_removes_every_row_and_stored_chunk(site):
    seed_two_visitors(site)
    theirs = list(
        RecordingChunk.objects.filter(recording__visitor_id="v-1").values_list(
            "storage_key", flat=True
        )
    )
    result = erase_visitor("v-1")
    assert result == {"recordings": 3, "chunks": 3, "hits": 2, "navigation": 2, "failed": 0}
    assert not any(exists(key) for key in theirs)
    assert not Recording.objects.filter(visitor_id="v-1").exists()
    assert not Hit.objects.filter(visitor_id="v-1").exists()
    assert not SessionNavigation.objects.filter(visitor_id="v-1").exists()
    # The other visitor is untouched.
    assert Recording.objects.filter(visitor_id="v-2").count() == 1
    assert Hit.objects.filter(visitor_id="v-2").count() == 1
    assert all(exists(key) for key in stored_keys())


def test_erasing_within_one_site_leaves_other_sites(site):
    other_site = seed_two_visitors(site)
    result = erase_visitor("v-1", site)
    assert (result["recordings"], result["hits"], result["navigation"]) == (2, 1, 1)
    assert Recording.objects.filter(visitor_id="v-1", site=other_site).count() == 1


def test_a_dry_run_counts_and_deletes_nothing(site):
    seed_two_visitors(site)
    before = (Recording.objects.count(), Hit.objects.count(), len(stored_keys()))
    result = erase_visitor("v-1", dry_run=True)
    assert result == {"recordings": 3, "chunks": 3, "hits": 2, "navigation": 2, "failed": 0}
    assert (Recording.objects.count(), Hit.objects.count(), len(stored_keys())) == before


def test_a_failed_chunk_delete_keeps_the_recording_and_a_rerun_finishes(site, monkeypatch):
    make_recording(site, visitor_id="v-1")
    with monkeypatch.context() as patch:
        failing_delete(patch)
        assert erase_visitor("v-1")["failed"] == 1
    assert Recording.objects.count() == 1
    assert erase_visitor("v-1")["recordings"] == 1
    assert Recording.objects.count() == 0


def test_an_empty_visitor_id_is_refused():
    with pytest.raises(ValueError):
        erase_visitor("")


def test_the_command_erases_and_reports(site):
    seed_two_visitors(site)
    out = StringIO()
    call_command("oss_clarity_erase_visitor", "v-1", "--dry-run", stdout=out)
    assert "would erase: 3 recordings (3 chunks), 2 hits, 2 navigation rows" in out.getvalue()
    call_command("oss_clarity_erase_visitor", "v-1", "--site", str(site.pk), stdout=out)
    assert "erased: 2 recordings" in out.getvalue()
    with pytest.raises(CommandError):
        call_command("oss_clarity_erase_visitor", "v-1", "--site", "999999")


def test_the_admin_deletes_recordings_with_their_chunks(admin_client, site):
    recording = make_recording(site)
    keys = stored_keys()
    url = reverse("admin:oss_clarity_recording_delete", args=[recording.pk])
    assert admin_client.post(url, {"post": "yes"}).status_code == 302
    assert Recording.objects.count() == 0 and not any(exists(key) for key in keys)


def test_the_admin_erases_the_selected_visitors_everywhere(admin_client, site):
    seed_two_visitors(site)
    chosen = Recording.objects.get(session_id="s-2")
    admin_client.post(
        reverse("admin:oss_clarity_recording_changelist"),
        {"action": "erase_visitors", "_selected_action": [str(chosen.pk)]},
    )
    assert not Recording.objects.filter(visitor_id="v-1").exists()
    assert Recording.objects.filter(visitor_id="v-2").exists()
