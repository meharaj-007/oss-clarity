from __future__ import annotations

import gzip
import json

import pytest

from oss_clarity.models import Recording, RecordingChunk, RecordingSettings, Site
from oss_clarity.storage import all_events, get_storage, page_events

from .conftest import ORIGIN, UA_CHROME

pytestmark = pytest.mark.django_db

RECORD = "/oc/r/"
SESSION = "0b7f5c2e-4d1a-4c2b-9e8f-3a6d5c4b2a10"


def snapshot(ts=1_000):
    return {"type": 2, "timestamp": ts, "data": {"node": {"type": 0, "id": 1, "childNodes": []}}}


def events(n, start=2_000):
    return [
        {"type": 3, "timestamp": start + i * 100, "data": {"source": 1, "positions": []}}
        for i in range(n)
    ]


def chunk(site, *, session=SESSION, page="p-1", page_seq=0, chunk_seq=0, body=None, **extra):
    return {
        "key": site.public_key,
        "session_id": session,
        "visitor_id": "v-1",
        "page_id": page,
        "page_seq": page_seq,
        "chunk_seq": chunk_seq,
        "url": "https://shop.example/pricing",
        "final": False,
        "stop_reason": "",
        "events": body if body is not None else [snapshot(), *events(3)],
        **extra,
    }


def post(client, payload, *, gzipped=False, origin=ORIGIN, ua=UA_CHROME, raw=None):
    body = raw if raw is not None else json.dumps(payload).encode()
    headers = {"HTTP_USER_AGENT": ua}
    if origin is not None:
        headers["HTTP_ORIGIN"] = origin
    if gzipped:
        body = gzip.compress(body)
        headers["HTTP_CONTENT_ENCODING"] = "gzip"
    return client.post(RECORD, data=body, content_type="text/plain", **headers)


def test_a_first_chunk_opens_a_recording_and_stores_its_events(client, site, recording_on):
    assert post(client, chunk(site)).status_code == 204
    recording = Recording.objects.get()
    assert (recording.session_id, recording.visitor_id, recording.status) == (
        SESSION,
        "v-1",
        "recording",
    )
    assert (recording.page_count, recording.chunk_count, recording.event_count) == (1, 1, 4)
    assert recording.duration_ms == 1_200
    stored = RecordingChunk.objects.get()
    assert stored.storage_key.startswith(f"oss_clarity/recordings/{site.pk}/{recording.pk}/")
    assert SESSION not in stored.storage_key
    assert page_events(recording, "p-1")[0]["type"] == 2


def test_a_gzipped_body_is_accepted(client, site, recording_on):
    post(client, chunk(site), gzipped=True)
    assert Recording.objects.count() == 1


def test_later_chunks_and_pages_accumulate_in_order(client, site, recording_on):
    post(client, chunk(site))
    post(client, chunk(site, chunk_seq=1, body=events(2, start=3_000)))
    post(client, chunk(site, page="p-2", page_seq=1, body=[snapshot(5_000)]))
    recording = Recording.objects.get()
    assert (recording.page_count, recording.chunk_count, recording.duration_ms) == (2, 3, 4_000)
    pages = all_events(recording)
    assert [page for page, _ in pages] == ["p-1", "p-2"]
    assert [e["timestamp"] for e in pages[0][1]] == [1_000, 2_000, 2_100, 2_200, 3_000, 3_100]


def test_a_missing_stored_chunk_is_a_gap_not_a_failure(client, site, recording_on):
    post(client, chunk(site))
    post(client, chunk(site, chunk_seq=1, body=events(2, start=3_000)))
    first = RecordingChunk.objects.get(chunk_seq=0)
    get_storage().delete(first.storage_key)
    assert [e["timestamp"] for e in page_events(Recording.objects.get(), "p-1")] == [3_000, 3_100]


def test_only_a_first_chunk_may_open_a_recording(client, site, recording_on):
    post(client, chunk(site, chunk_seq=3))
    post(client, chunk(site, page_seq=2))
    assert Recording.objects.count() == 0


def test_a_duplicate_chunk_keeps_the_first_copy_and_no_extra_object(client, site, recording_on):
    post(client, chunk(site))
    post(client, chunk(site))
    assert RecordingChunk.objects.count() == 1
    assert Recording.objects.get().chunk_count == 1
    folder = RecordingChunk.objects.get().storage_key.rsplit("/", 1)[0]
    assert len(get_storage().listdir(folder)[1]) == 1


def test_recording_off_stores_nothing_and_counts_the_drop_once(client, site):
    RecordingSettings.objects.create(site=site, enabled=False)
    for seq in range(3):
        post(client, chunk(site, chunk_seq=seq))
    assert Recording.objects.count() == 0
    site.refresh_from_db()
    assert (site.dropped_chunks, site.last_drop_reason) == (1, "disabled")


def test_the_deployment_switch_turns_recording_off(client, site, recording_on, settings):
    settings.OSS_CLARITY = {"RECORDING_ENABLED": False}
    post(client, chunk(site))
    assert Recording.objects.count() == 0


def test_a_session_outside_the_sample_is_refused(client, site, recording_on):
    from oss_clarity.core.sampling import fnv1a32

    # Exactly its own bucket: the session is the first one left out.
    recording_on.sample_pct = fnv1a32(SESSION) % 100
    recording_on.save()
    post(client, chunk(site))
    assert Recording.objects.count() == 0
    site.refresh_from_db()
    assert site.last_drop_reason == "sampling"


@pytest.mark.parametrize(
    "ua",
    [
        "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko; "
        "Google-InspectionTool/1.0) Chrome/131.0.0.0 Safari/537.36",
        "pc",
    ],
)
def test_a_crawler_is_not_recorded_and_not_counted_as_a_drop(client, site, recording_on, ua):
    post(client, chunk(site), ua=ua)
    assert Recording.objects.count() == 0
    site.refresh_from_db()
    assert site.dropped_chunks == 0


def test_a_chunk_from_the_wrong_host_is_refused(client, site, recording_on):
    post(client, chunk(site), origin="https://other.example")
    post(client, chunk(site), origin=None)
    assert Recording.objects.count() == 0
    site.refresh_from_db()
    assert site.rejected_hits == 2


def test_the_session_byte_cap_closes_the_recording(client, site, recording_on, settings):
    settings.OSS_CLARITY = {"MAX_SESSION_BYTES": 600}
    post(client, chunk(site))
    post(client, chunk(site, chunk_seq=1, body=events(20, start=3_000)))
    recording = Recording.objects.get()
    assert (recording.status, recording.stop_reason, recording.chunk_count) == (
        "capped",
        "bytes",
        1,
    )
    site.refresh_from_db()
    assert site.last_drop_reason == "bytes"


def test_the_page_cap_closes_the_recording(client, site, recording_on, settings):
    settings.OSS_CLARITY = {"MAX_PAGES": 1}
    post(client, chunk(site))
    post(client, chunk(site, page="p-2", page_seq=1, body=[snapshot(5_000)]))
    recording = Recording.objects.get()
    assert (recording.status, recording.stop_reason, recording.page_count) == (
        "capped",
        "pages",
        1,
    )


def test_an_oversize_chunk_is_refused(client, site, recording_on, settings):
    settings.OSS_CLARITY = {"MAX_CHUNK_BYTES": 400}
    post(client, chunk(site, body=[snapshot(), *events(20)]))
    assert Recording.objects.count() == 0


def test_too_many_events_in_one_chunk_is_refused(client, site, recording_on, settings):
    settings.OSS_CLARITY = {"MAX_EVENTS_PER_CHUNK": 3}
    post(client, chunk(site))
    assert Recording.objects.count() == 0


def test_a_body_that_inflates_past_the_ceiling_is_refused_unread(
    client, site, recording_on, settings
):
    settings.OSS_CLARITY = {"MAX_INFLATED_BYTES": 500}
    post(client, chunk(site, body=[snapshot(), *events(50)]), gzipped=True)
    assert Recording.objects.count() == 0


def test_the_recorder_stopping_at_a_cap_marks_the_recording(client, site, recording_on):
    post(client, chunk(site, final=True, stop_reason="time"))
    recording = Recording.objects.get()
    assert (recording.status, recording.stop_reason) == ("capped", "time")


def test_a_chunk_after_the_recording_closed_is_refused_but_not_counted(client, site, recording_on):
    post(client, chunk(site))
    Recording.objects.update(status="complete")
    post(client, chunk(site, chunk_seq=1, body=events(2, start=3_000)))
    assert RecordingChunk.objects.count() == 1
    site.refresh_from_db()
    assert site.dropped_chunks == 0


@pytest.mark.parametrize(
    "overrides",
    [
        {"events": "nope"},
        {"events": []},
        {"events": [{"type": "x", "timestamp": 1}]},
        {"events": [{"type": 2, "timestamp": True}]},
        {"session_id": "café"},
        {"session_id": ""},
        {"page_id": "a" * 37},
        {"key": "AAAAAAAAAAAAAAAAAAAAAA"},
    ],
)
def test_malformed_chunks_answer_204_and_write_nothing(client, site, recording_on, overrides):
    assert post(client, {**chunk(site), **overrides}).status_code == 204
    assert Recording.objects.count() == 0


def test_non_json_bodies_answer_204(client, site, recording_on):
    assert post(client, None, raw=b"not json").status_code == 204
    assert post(client, None, raw=b"\x1f\x8b broken", gzipped=False).status_code == 204
    response = client.post(
        RECORD,
        data=b"not gzip",
        content_type="text/plain",
        HTTP_CONTENT_ENCODING="gzip",
        HTTP_ORIGIN=ORIGIN,
    )
    assert response.status_code == 204 and Recording.objects.count() == 0


def test_an_inactive_site_records_nothing(client, site, recording_on):
    Site.objects.filter(pk=site.pk).update(is_active=False)
    post(client, chunk(site))
    assert Recording.objects.count() == 0


def test_a_storage_outage_loses_the_chunk_quietly(client, site, recording_on, monkeypatch):
    import oss_clarity.record as record_module

    def broken(*args, **kwargs):
        raise OSError("storage down")

    monkeypatch.setattr(record_module, "write_chunk", broken)
    assert post(client, chunk(site)).status_code == 204
    assert RecordingChunk.objects.count() == 0
