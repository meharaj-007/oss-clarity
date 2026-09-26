from __future__ import annotations

import pytest
from django.core.cache import cache

from oss_clarity.models import RecordingSettings, Site

UA_CHROME = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
ORIGIN = "https://shop.example"


@pytest.fixture(autouse=True)
def fresh_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def site(db):
    return Site.objects.create(name="Shop", domain="shop.example")


@pytest.fixture
def recording_on(site):
    return RecordingSettings.objects.create(site=site, enabled=True, sample_pct=100)


def make_recording(site, *, session_id="s-1", visitor_id="v-1", pages=None, **fields):
    """A recording with its chunks written to storage, one chunk per page."""
    import json

    from django.utils import timezone

    from oss_clarity.models import Recording, RecordingChunk
    from oss_clarity.storage import chunk_key, write_chunk

    now = timezone.now()
    fields = {"started_at": now, "last_event_at": now, **fields}
    recording = Recording.objects.create(
        site=site, session_id=session_id, visitor_id=visitor_id, **fields
    )
    pages = pages if pages is not None else [("p-0", [{"type": 2, "timestamp": 1_000}])]
    for page_seq, (page_id, events) in enumerate(pages):
        key = write_chunk(chunk_key(recording, page_seq, 0), json.dumps(events).encode("utf-8"))
        stamps = [e["timestamp"] for e in events] or [0]
        RecordingChunk.objects.create(
            recording=recording,
            page_id=page_id,
            page_seq=page_seq,
            chunk_seq=0,
            first_ts=min(stamps),
            last_ts=max(stamps),
            event_count=len(events),
            storage_key=key,
        )
    Recording.objects.filter(pk=recording.pk).update(page_count=len(pages), chunk_count=len(pages))
    recording.refresh_from_db()
    return recording


def make_hit(site, *, ago=None, **fields):
    """A hit, backdated by `ago` (a timedelta)."""
    from django.utils import timezone

    from oss_clarity.models import Hit

    fields = {"hit_type": "pageview", "path": "/", "device_class": "desktop", **fields}
    hit = Hit.objects.create(site=site, **fields)
    if ago is not None:
        Hit.objects.filter(pk=hit.pk).update(created_at=timezone.now() - ago)
        hit.refresh_from_db()
    return hit
