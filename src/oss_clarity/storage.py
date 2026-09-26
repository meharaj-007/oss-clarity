"""Recording chunks in storage: gzip JSON arrays of rrweb events, written and
read through Django's storage API (local disk, S3 via django-storages, or any
other backend in `STORAGES`)."""

from __future__ import annotations

import gzip
import json
import logging

from django.core.files.base import ContentFile
from django.core.files.storage import Storage, storages

from .conf import settings
from .models import Recording, RecordingChunk

logger = logging.getLogger(__name__)


def get_storage() -> Storage:
    return storages[settings.STORAGE]


def chunk_key(recording: Recording, page_seq: int, chunk_seq: int) -> str:
    """Where a chunk goes: by site, then the recording's own id. Never the
    session id, which the visitor's browser minted and is not a safe path."""
    prefix = settings.STORAGE_PREFIX.strip("/")
    name = f"{recording.site_id}/{recording.pk}/{page_seq:03d}-{chunk_seq:04d}.json.gz"
    return f"{prefix}/{name}" if prefix else name


def write_chunk(key: str, event_json: bytes) -> str:
    """Store one chunk's events; returns the key the storage actually used."""
    return get_storage().save(key, ContentFile(gzip.compress(event_json, compresslevel=6)))


def read_chunk(key: str) -> list:
    with get_storage().open(key, "rb") as handle:
        return json.loads(gzip.decompress(handle.read()).decode("utf-8"))


def delete_objects(keys) -> list[str]:
    """Delete stored objects. Returns the keys that could not be deleted."""
    storage = get_storage()
    failed = []
    for key in keys:
        try:
            storage.delete(key)
        except Exception:
            logger.warning("stored chunk not deleted", exc_info=True)
            failed.append(key)
    return failed


def page_events(recording: Recording, page_id: str) -> list | None:
    """Every event of one page, chunks in order. None when the page is not in
    the recording. A missing or unreadable chunk is a gap, not a failure."""
    chunks = list(
        RecordingChunk.objects.filter(recording=recording, page_id=page_id).order_by("chunk_seq")
    )
    if not chunks:
        return None
    events: list = []
    for chunk in chunks:
        try:
            events.extend(read_chunk(chunk.storage_key))
        except Exception:
            logger.warning("stored chunk unreadable: %s", chunk.storage_key, exc_info=True)
    return events


def all_events(recording: Recording) -> list[tuple[str, list]]:
    """`[(page_id, events)]` for every page in visit order, as `core.analyze`
    reads it."""
    pages: list[str] = []
    for page_id in (
        RecordingChunk.objects.filter(recording=recording)
        .order_by("page_seq", "chunk_seq")
        .values_list("page_id", flat=True)
    ):
        if page_id not in pages:
            pages.append(page_id)
    return [(page_id, page_events(recording, page_id) or []) for page_id in pages]
