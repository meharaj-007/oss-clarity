"""Accept one recording chunk from the recorder.

The recorder uploads slices of one page's rrweb events every few seconds,
gzip where the browser can. Everything here answers "no" silently: the caller
is a browser on someone else's page, and an explanation would print in its
console and tell a prober where each line is. The site's drop counter is
where refusals show up instead.

Checks run cheapest first. Only the chunk that opens a session can create a
recording; every later chunk is judged against that row.
"""

from __future__ import annotations

import json
import logging
import zlib

from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import F, Max, Min
from django.utils import timezone

from .choices import DropReason, RecordingStatus, StopReason
from .conf import settings
from .core.sampling import ID_RE, session_is_sampled
from .models import Recording, RecordingChunk, RecordingSettings, Site
from .storage import chunk_key, delete_objects, write_chunk

logger = logging.getLogger(__name__)

SETTINGS_CACHE_SECONDS = 60
#: Refused for being late, not dropped: a tab woke up after its visit closed.
CLOSED = "closed"


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def _cache_key(site_id) -> str:
    return f"oc:rs:{site_id}"


def settings_for(site: Site) -> dict:
    """One site's recording settings as plain values, cached briefly: they
    are read on every chunk and every tracker serve."""
    key = _cache_key(site.pk)
    try:
        cached = cache.get(key)
    except Exception:
        cached = None
    if cached is not None:
        return cached
    row = RecordingSettings.objects.filter(site=site).first()
    data = {
        "enabled": bool(row and row.enabled),
        "sample_pct": int(row.sample_pct) if row else settings.DEFAULT_SAMPLE_PCT,
        "mask_mode": row.mask_mode if row else settings.DEFAULT_MASK_MODE,
        "mask_selectors": list(row.mask_selectors or []) if row else [],
        "unmask_selectors": list(row.unmask_selectors or []) if row else [],
        "block_selectors": list(row.block_selectors or []) if row else [],
        "consent_required": bool(row and row.consent_required),
    }
    try:
        cache.set(key, data, timeout=SETTINGS_CACHE_SECONDS)
    except Exception:
        logger.debug("recording settings not cached", exc_info=True)
    return data


def forget_settings(sender=None, instance=None, **kwargs) -> None:
    """Drop the cached copy when settings are saved, so the change applies
    to the next chunk and the next tracker serve."""
    if instance is None:
        return
    try:
        cache.delete(_cache_key(instance.site_id))
    except Exception:
        logger.debug("recording settings cache not cleared", exc_info=True)


def tracker_record_config(site: Site, *, endpoint: str, recorder_url: str) -> dict | None:
    """The recording block baked into a served tracker, or None when the
    site does not record."""
    config = settings_for(site)
    if not settings.RECORDING_ENABLED or not config["enabled"]:
        return None
    return {
        "endpoint": endpoint,
        "recorder_url": recorder_url,
        "sample_pct": config["sample_pct"],
        "mask_mode": config["mask_mode"],
        "mask": config["mask_selectors"],
        "unmask": config["unmask_selectors"],
        "block": config["block_selectors"],
        "consent_required": config["consent_required"],
        "max_bytes": settings.MAX_SESSION_BYTES,
        "max_pages": settings.MAX_PAGES,
        "max_minutes": settings.MAX_MINUTES,
    }


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


class ChunkRejected(Exception):
    """A chunk that will not be stored, and why. Never reaches the browser."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def inflate(body: bytes, *, gzipped: bool) -> bytes:
    """The body as JSON bytes. Decompressed in one bounded step, so a small
    body cannot expand into gigabytes before anything measures it."""
    if not gzipped:
        return body
    ceiling = settings.MAX_INFLATED_BYTES
    try:
        inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
        data = inflater.decompress(body, ceiling + 1)
    except zlib.error as exc:
        raise ChunkRejected(DropReason.INVALID) from exc
    if len(data) > ceiling or inflater.unconsumed_tail:
        raise ChunkRejected(DropReason.OVERSIZE)
    return data


def parse_chunk(data: bytes) -> dict:
    """The chunk's fields, checked for shape. Timestamps and counts are
    recomputed here, not trusted."""
    try:
        payload = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ChunkRejected(DropReason.INVALID) from exc
    if not isinstance(payload, dict):
        raise ChunkRejected(DropReason.INVALID)

    events = payload.get("events")
    if not isinstance(events, list) or not events:
        raise ChunkRejected(DropReason.INVALID)
    if len(events) > settings.MAX_EVENTS_PER_CHUNK:
        raise ChunkRejected(DropReason.OVERSIZE)
    timestamps = []
    for event in events:
        if not isinstance(event, dict):
            raise ChunkRejected(DropReason.INVALID)
        ts = event.get("timestamp")
        kind = event.get("type")
        if isinstance(ts, bool) or not isinstance(ts, (int, float)):
            raise ChunkRejected(DropReason.INVALID)
        if isinstance(kind, bool) or not isinstance(kind, int):
            raise ChunkRejected(DropReason.INVALID)
        timestamps.append(int(ts))

    session_id = str(payload.get("session_id") or "")
    page_id = str(payload.get("page_id") or "")
    if not ID_RE.match(session_id) or not ID_RE.match(page_id) or len(page_id) > 36:
        raise ChunkRejected(DropReason.INVALID)
    visitor_id = str(payload.get("visitor_id") or "")

    stop_reason = str(payload.get("stop_reason") or "")
    return {
        "key": str(payload.get("key") or ""),
        "session_id": session_id,
        "visitor_id": visitor_id if ID_RE.match(visitor_id) else "",
        "page_id": page_id,
        "page_seq": _count(payload.get("page_seq")),
        "chunk_seq": _count(payload.get("chunk_seq")),
        "url": str(payload.get("url") or "")[:2048],
        "final": payload.get("final") is True,
        "stop_reason": stop_reason if stop_reason in StopReason.values else "",
        "events": events,
        "first_ts": min(timestamps),
        "last_ts": max(timestamps),
    }


def _count(value) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return max(0, min(int(value), 1_000_000))
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------------------
# Drop accounting
# ---------------------------------------------------------------------------


def record_drop(site: Site, reason: str) -> None:
    Site.objects.filter(pk=site.pk).update(
        dropped_chunks=F("dropped_chunks") + 1,
        last_drop_at=timezone.now(),
        last_drop_reason=reason,
    )


def drop_once_per_session(site: Site, session_id: str, reason: str) -> None:
    """Count a refusal once per session and reason: chunks keep arriving
    every few seconds for as long as the visitor stays."""
    key = f"oc:rd:{site.pk}:{session_id}:{reason}"
    try:
        first = cache.add(key, 1, timeout=settings.SESSION_IDLE_MINUTES * 60)
    except Exception:
        first = True  # No cache: count it rather than lose it.
    if first:
        record_drop(site, reason)


# ---------------------------------------------------------------------------
# The write
# ---------------------------------------------------------------------------


def ingest_chunk(site: Site, chunk: dict, *, now=None) -> RecordingChunk:
    """Store one parsed chunk, or raise `ChunkRejected`."""
    now = now or timezone.now()
    config = settings_for(site)
    if not settings.RECORDING_ENABLED or not config["enabled"]:
        raise ChunkRejected(DropReason.DISABLED)
    if not session_is_sampled(chunk["session_id"], config["sample_pct"]):
        raise ChunkRejected(DropReason.SAMPLING)

    event_json = json.dumps(chunk["events"], separators=(",", ":")).encode("utf-8")
    if len(event_json) > settings.MAX_CHUNK_BYTES:
        raise ChunkRejected(DropReason.OVERSIZE)

    recording = Recording.objects.filter(site=site, session_id=chunk["session_id"]).first()
    if recording is None:
        # Only a first chunk opens a session, so a visit whose opening chunk
        # was refused cannot appear from its middle.
        if chunk["chunk_seq"] != 0 or chunk["page_seq"] != 0:
            raise ChunkRejected(DropReason.SAMPLING)
        try:
            with transaction.atomic():
                recording = Recording.objects.create(
                    site=site,
                    session_id=chunk["session_id"],
                    visitor_id=chunk["visitor_id"],
                    mask_mode=config["mask_mode"],
                    started_at=now,
                    last_event_at=now,
                )
        except IntegrityError:
            # Two opening chunks racing; continue on the one that won.
            recording = Recording.objects.get(site=site, session_id=chunk["session_id"])

    if recording.status != RecordingStatus.RECORDING:
        raise ChunkRejected(CLOSED)
    if recording.byte_size + len(event_json) > settings.MAX_SESSION_BYTES:
        Recording.objects.filter(pk=recording.pk).update(
            status=RecordingStatus.CAPPED, stop_reason=StopReason.BYTES
        )
        raise ChunkRejected(DropReason.BYTES)
    is_new_page = not RecordingChunk.objects.filter(
        recording=recording, page_id=chunk["page_id"]
    ).exists()
    if is_new_page and recording.page_count >= settings.MAX_PAGES:
        Recording.objects.filter(pk=recording.pk).update(
            status=RecordingStatus.CAPPED, stop_reason=StopReason.PAGES
        )
        raise ChunkRejected(DropReason.BYTES)

    saved_key = write_chunk(chunk_key(recording, chunk["page_seq"], chunk["chunk_seq"]), event_json)
    try:
        with transaction.atomic():
            row = RecordingChunk.objects.create(
                recording=recording,
                page_id=chunk["page_id"],
                page_seq=chunk["page_seq"],
                chunk_seq=chunk["chunk_seq"],
                url=chunk["url"],
                first_ts=chunk["first_ts"],
                last_ts=chunk["last_ts"],
                event_count=len(chunk["events"]),
                byte_size=len(event_json),
                storage_key=saved_key,
                is_final=chunk["final"],
            )
            updates = {
                "last_event_at": now,
                "chunk_count": F("chunk_count") + 1,
                "event_count": F("event_count") + len(chunk["events"]),
                "byte_size": F("byte_size") + len(event_json),
                "updated_at": now,
            }
            if is_new_page:
                updates["page_count"] = F("page_count") + 1
            if chunk["final"] and chunk["stop_reason"]:
                updates["status"] = RecordingStatus.CAPPED
                updates["stop_reason"] = chunk["stop_reason"]
            Recording.objects.filter(pk=recording.pk).update(**updates)
    except IntegrityError:
        # The same chunk twice, a retried upload. The first copy stands.
        delete_objects([saved_key])
        raise ChunkRejected(DropReason.INVALID) from None

    _refresh_duration(recording.pk)
    return row


def _refresh_duration(recording_id) -> None:
    """Recomputed from every chunk, so one arriving late cannot shrink it."""
    span = RecordingChunk.objects.filter(recording_id=recording_id).aggregate(
        first=Min("first_ts"), last=Max("last_ts")
    )
    if span["first"] is not None:
        Recording.objects.filter(pk=recording_id).update(
            duration_ms=max(0, int(span["last"]) - int(span["first"]))
        )
