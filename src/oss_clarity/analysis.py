"""Closing finished recordings, running the signal rules on them, and storing
each visit's quick backs and loops.

The rules themselves live in `oss_clarity.core`; this module only reads the
inputs from the database and storage and writes the results back.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .choices import HitType, RecordingStatus
from .conf import settings
from .core import analyze, navigation
from .models import SIGNAL_COLUMNS, Hit, Recording, SessionNavigation, Site
from .storage import all_events

logger = logging.getLogger(__name__)

#: Recordings closed or analysed per run; the rest wait for the next one.
BATCH = 500
#: Visits reread per query when rolling up navigation.
SESSION_CHUNK = 500


def page_views(site_id, session_id: str) -> list[tuple[str, object]]:
    """One visit's page views as `[(path, time)]`, in the order they happened."""
    rows = list(
        Hit.objects.filter(
            site_id=site_id, session_id=session_id, hit_type=HitType.PAGEVIEW
        ).values("path", "created_at", "sequence")
    )
    rows = navigation.ordered(rows, key=lambda r: (r["sequence"], r["created_at"]))
    return [(r["path"], r["created_at"]) for r in rows]


def analyze_recording(recording: Recording) -> dict[str, int]:
    """Run every rule over one recording and store the counters and markers."""
    counts, markers = analyze(
        all_events(recording),
        settings.thresholds,
        pageview_tags=settings.PAGEVIEW_TAGS,
        error_tags=settings.ERROR_TAGS,
    )
    moves = navigation.count(
        page_views(recording.site_id, recording.session_id), settings.QUICK_BACK_SECONDS
    )
    counts = {**counts, "quick_back": moves["quick_backs"], "loop": moves["loops"]}
    now = timezone.now()
    Recording.objects.filter(pk=recording.pk).update(
        **{SIGNAL_COLUMNS[kind]: value for kind, value in counts.items()},
        markers=markers,
        analyzed_at=now,
        updated_at=now,
    )
    return counts


def finalize_and_analyze(*, now=None) -> dict[str, int]:
    """Close recordings quiet for `SESSION_IDLE_MINUTES`, then analyse every
    closed recording not analysed yet (including ones the recorder capped)."""
    now = now or timezone.now()
    cutoff = now - timedelta(minutes=settings.SESSION_IDLE_MINUTES)
    quiet = list(
        Recording.objects.filter(
            status=RecordingStatus.RECORDING, last_event_at__lt=cutoff
        ).values_list("pk", flat=True)[:BATCH]
    )
    if quiet:
        Recording.objects.filter(pk__in=quiet).update(
            status=RecordingStatus.COMPLETE, updated_at=now
        )

    analyzed = failed = 0
    pending = Recording.objects.filter(analyzed_at__isnull=True, last_event_at__lt=cutoff).exclude(
        status=RecordingStatus.RECORDING
    )
    for recording in pending.order_by("last_event_at")[:BATCH]:
        try:
            analyze_recording(recording)
            analyzed += 1
        except Exception:
            failed += 1
            logger.exception("recording %s not analysed", recording.pk)
    return {"closed": len(quiet), "analyzed": analyzed, "failed": failed}


def rollup_sessions(site_id, session_ids: list[str]) -> dict[str, int]:
    """Recount these visits from all their page views and replace their rows.
    Only visits with a quick back or a loop keep a row."""
    session_ids = [s for s in dict.fromkeys(session_ids) if s]
    if not session_ids:
        return {"sessions": 0, "stored": 0}

    by_session: dict[str, list[dict]] = defaultdict(list)
    for row in Hit.objects.filter(
        site_id=site_id, session_id__in=session_ids, hit_type=HitType.PAGEVIEW
    ).values("session_id", "visitor_id", "path", "created_at", "sequence"):
        by_session[row["session_id"]].append(row)

    keep = []
    for session_id, views in by_session.items():
        ordered = navigation.ordered(views, key=lambda r: (r["sequence"], r["created_at"]))
        result = navigation.count(
            [(r["path"], r["created_at"]) for r in ordered], settings.QUICK_BACK_SECONDS
        )
        if not (result["quick_backs"] or result["loops"]):
            continue
        keep.append(
            SessionNavigation(
                site_id=site_id,
                session_id=session_id,
                visitor_id=last_visitor_id(ordered),
                started_at=min(r["created_at"] for r in views),
                ended_at=max(r["created_at"] for r in views),
                **result,
            )
        )

    # Replaced as a set, so a visit reread without a quick back loses its row.
    with transaction.atomic():
        SessionNavigation.objects.filter(site_id=site_id, session_id__in=session_ids).delete()
        SessionNavigation.objects.bulk_create(keep)
    return {"sessions": len(session_ids), "stored": len(keep)}


def last_visitor_id(rows: list[dict]) -> str:
    return next((row["visitor_id"] for row in reversed(rows) if row["visitor_id"]), "")


def rollup_navigation(*, now=None, hours: int = 2) -> dict[str, int]:
    """Every visit with a page view in the last `hours`. Two hours covers a
    visit's end (the idle window) and a missed run or two."""
    now = now or timezone.now()
    since = now - timedelta(hours=hours)
    totals = {"sites": 0, "sessions": 0, "stored": 0}
    for site_id in Site.objects.filter(last_hit_at__gte=since).values_list("pk", flat=True):
        totals["sites"] += 1
        session_ids = list(
            Hit.objects.filter(site_id=site_id, created_at__gte=since, hit_type=HitType.PAGEVIEW)
            .exclude(session_id="")
            .values_list("session_id", flat=True)
            .distinct()
        )
        for start in range(0, len(session_ids), SESSION_CHUNK):
            result = rollup_sessions(site_id, session_ids[start : start + SESSION_CHUNK])
            totals["sessions"] += result["sessions"]
            totals["stored"] += result["stored"]
    return totals
