"""Expiring what was collected, and erasing one visitor on request.

Windows come from settings: hits and visit navigation `HIT_RETENTION_DAYS`,
recordings `RECORDING_RETENTION_DAYS` (a favourite is kept until the hit
window instead, since a replay cannot outlive its timeline), heatmap buckets
`HEATMAP_RETENTION_DAYS`. Buckets hold counts and no visitor, so they outlive
the hits.

A recording's stored chunks are always deleted before its rows. A row whose
chunk is gone is a gap the player tolerates; a chunk whose row is gone is
storage nothing will ever find again. If a chunk cannot be deleted, the rows
stay, and the next run tries again.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .conf import settings
from .models import (
    HeatmapClickBucket,
    HeatmapPage,
    HeatmapScrollBucket,
    Hit,
    Recording,
    RecordingChunk,
    SessionNavigation,
    Site,
)
from .storage import delete_objects

logger = logging.getLogger(__name__)

#: Rows per DELETE, so a large backlog never holds a long lock on a table
#: the collector writes to on every page view.
BATCH_SIZE = 2_000
#: Most rows one run deletes per table. The job is daily and idempotent, so
#: a large backlog drains over several runs.
MAX_PER_RUN = 200_000
MAX_RECORDINGS_PER_RUN = 2_000


def delete_recording(recording: Recording) -> bool:
    """Delete a recording's stored chunks, then, if all of them went, its
    rows. Returns whether the recording is gone."""
    keys = list(
        RecordingChunk.objects.filter(recording=recording).values_list("storage_key", flat=True)
    )
    if delete_objects(keys):
        logger.warning("recording %s kept: stored chunks could not be deleted", recording.pk)
        return False
    recording.delete()
    return True


def prune(*, now=None) -> dict[str, int]:
    """Everything past its window. Safe to run any number of times."""
    now = now or timezone.now()
    hit_cutoff = now - timedelta(days=settings.HIT_RETENTION_DAYS)
    recording_cutoff = now - timedelta(days=settings.RECORDING_RETENTION_DAYS)
    bucket_cutoff = (now - timedelta(days=settings.HEATMAP_RETENTION_DAYS)).date()

    totals = {"hits": _batched_delete(Hit.objects.filter(created_at__lt=hit_cutoff))}
    totals["navigation"] = _batched_delete(
        SessionNavigation.objects.filter(ended_at__lt=hit_cutoff)
    )

    # Past the hit window, favourite or not; past the recording window unless
    # a favourite.
    stale = Recording.objects.filter(
        Q(started_at__lt=hit_cutoff) | Q(started_at__lt=recording_cutoff, is_favorite=False)
    )
    deleted = kept = 0
    for recording in stale.order_by("started_at")[:MAX_RECORDINGS_PER_RUN]:
        if delete_recording(recording):
            deleted += 1
        else:
            kept += 1
    totals["recordings"] = deleted
    totals["recordings_kept"] = kept

    buckets = 0
    for model in (HeatmapPage, HeatmapClickBucket, HeatmapScrollBucket):
        buckets += _batched_delete(model.objects.filter(day__lt=bucket_cutoff))
    totals["heatmap_rows"] = buckets
    return totals


def _batched_delete(queryset) -> int:
    model = queryset.model
    deleted = 0
    while deleted < MAX_PER_RUN:
        batch = list(queryset.values_list("pk", flat=True)[:BATCH_SIZE])
        if not batch:
            break
        model.objects.filter(pk__in=batch).delete()
        deleted += len(batch)
    return deleted


def erase_visitor(visitor_id: str, site: Site | None = None, *, dry_run: bool = False) -> dict:
    """Delete everything held about one visitor: their recordings (with the
    stored chunks, favourites included), hits and visit navigation. Optionally
    within one site.

    Returns counts per kind, and `failed`: recordings kept because a stored
    chunk could not be deleted. Running it again finishes the job. Heatmap
    buckets hold no visitor id and are left as anonymous totals.
    """
    if not visitor_id:
        raise ValueError("a visitor id is required")
    scope = {"visitor_id": visitor_id}
    if site is not None:
        scope["site"] = site
    recordings = Recording.objects.filter(**scope)
    hits = Hit.objects.filter(**scope)
    navigation = SessionNavigation.objects.filter(**scope)

    if dry_run:
        return {
            "recordings": recordings.count(),
            "chunks": RecordingChunk.objects.filter(recording__in=recordings).count(),
            "hits": hits.count(),
            "navigation": navigation.count(),
            "failed": 0,
        }

    result = {"recordings": 0, "chunks": 0, "hits": 0, "navigation": 0, "failed": 0}
    for recording in recordings:
        chunks = recording.chunks.count()
        if delete_recording(recording):
            result["recordings"] += 1
            result["chunks"] += chunks
        else:
            result["failed"] += 1
    with transaction.atomic():
        result["hits"] = hits.delete()[0]
        result["navigation"] = navigation.delete()[0]
    return result
