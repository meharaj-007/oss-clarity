"""Read-side queries and the plain-dict shapes the JSON API and the admin
viewer both return. Nothing here writes."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from math import ceil
from typing import Any

from django.db.models import Count, Max, Min, Q
from django.utils import timezone

from .choices import HitType
from .collect import page_path
from .models import SIGNAL_COLUMNS, Hit, Recording, RecordingChunk, SessionNavigation, Site
from .storage import page_events

PAGE_SIZE = 50
MAX_DAYS = 400
NAVIGATION_SIGNALS = {"quick_back": "quick_backs", "loop": "loops"}
RECORDING_SIGNALS = {k: v for k, v in SIGNAL_COLUMNS.items() if k not in NAVIGATION_SIGNALS}


def since(days: int):
    return timezone.now() - timedelta(days=max(1, min(int(days), MAX_DAYS)))


def parse_signals(value: str | None) -> list[str]:
    """`?has=rage,loop` as known signal names; unknown names are ignored."""
    return [name for name in (value or "").split(",") if name in SIGNAL_COLUMNS]


def paginate(items, page: int, size: int = PAGE_SIZE) -> dict[str, Any]:
    count = items.count() if hasattr(items, "count") and not isinstance(items, list) else len(items)
    pages = max(1, ceil(count / size))
    page = max(1, min(int(page or 1), pages))
    start = (page - 1) * size
    return {
        "count": count,
        "page": page,
        "pages": pages,
        "items": list(items[start : start + size]),
    }


def iso(value) -> str | None:
    return value.isoformat() if value else None


# --- recordings ----------------------------------------------------------------------


def recordings_for(site: Site, *, has=(), favorites: bool = False, days: int = 30):
    queryset = Recording.objects.filter(site=site, started_at__gte=since(days))
    if has:
        condition = Q()
        for name in has:
            condition |= Q(**{f"{SIGNAL_COLUMNS[name]}__gt": 0})
        queryset = queryset.filter(condition)
    if favorites:
        queryset = queryset.filter(is_favorite=True)
    return queryset.order_by("-started_at")


def recording_summary(recording: Recording) -> dict[str, Any]:
    return {
        "id": str(recording.pk),
        "site_id": recording.site_id,
        "session_id": recording.session_id,
        "visitor_id": recording.visitor_id,
        "status": recording.status,
        "stop_reason": recording.stop_reason,
        "mask_mode": recording.mask_mode,
        "started_at": iso(recording.started_at),
        "last_event_at": iso(recording.last_event_at),
        "duration_ms": recording.duration_ms,
        "page_count": recording.page_count,
        "event_count": recording.event_count,
        "byte_size": recording.byte_size,
        "signals": {kind: getattr(recording, column) for kind, column in SIGNAL_COLUMNS.items()},
        "is_favorite": recording.is_favorite,
        "analyzed_at": iso(recording.analyzed_at),
    }


def recording_detail(recording: Recording, events_url: Callable[[int], str]) -> dict[str, Any]:
    """The summary, its markers, and every page with where to fetch its events."""
    pages: dict[int, dict[str, Any]] = {}
    for chunk in RecordingChunk.objects.filter(recording=recording).order_by(
        "page_seq", "chunk_seq"
    ):
        page = pages.setdefault(
            chunk.page_seq,
            {
                "page_seq": chunk.page_seq,
                "page_id": chunk.page_id,
                "url": chunk.url,
                "path": page_path(chunk.url),
                "chunks": 0,
                "events_url": events_url(chunk.page_seq),
            },
        )
        page["chunks"] += 1
    return {
        **recording_summary(recording),
        "markers": recording.markers,
        "pages": list(pages.values()),
    }


def events_for_page(recording: Recording, page_seq: int) -> list | None:
    chunk = RecordingChunk.objects.filter(recording=recording, page_seq=page_seq).first()
    return page_events(recording, chunk.page_id) if chunk else None


# --- sessions ------------------------------------------------------------------------


def sessions_for(site: Site, *, has=(), recorded: bool = False, days: int = 30):
    """Every visit with a page view in the window, newest first, as rows of
    `session_id, visitor_id, started_at, ended_at, pageviews`."""
    views = Hit.objects.filter(
        site=site, hit_type=HitType.PAGEVIEW, created_at__gte=since(days)
    ).exclude(session_id="")
    if has:
        nav = [NAVIGATION_SIGNALS[n] for n in has if n in NAVIGATION_SIGNALS]
        rec = [RECORDING_SIGNALS[n] for n in has if n in RECORDING_SIGNALS]
        wanted = Q(pk__in=[])
        if nav:
            condition = Q()
            for column in nav:
                condition |= Q(**{f"{column}__gt": 0})
            ids = SessionNavigation.objects.filter(condition, site=site).values("session_id")
            wanted |= Q(session_id__in=ids)
        if rec:
            condition = Q()
            for column in rec:
                condition |= Q(**{f"{column}__gt": 0})
            ids = Recording.objects.filter(condition, site=site).values("session_id")
            wanted |= Q(session_id__in=ids)
        views = views.filter(wanted)
    if recorded:
        views = views.filter(
            session_id__in=Recording.objects.filter(site=site).values("session_id")
        )
    return (
        views.values("session_id")
        .annotate(
            visitor_id=Max("visitor_id"),
            started_at=Min("created_at"),
            ended_at=Max("created_at"),
            pageviews=Count("pk"),
        )
        .order_by("-started_at")
    )


def session_rows(site: Site, rows: list[dict]) -> list[dict[str, Any]]:
    """Rows with `quick_backs`, `loops`, and the recording, if there is one."""
    ids = [row["session_id"] for row in rows]
    navigation = {
        n.session_id: n for n in SessionNavigation.objects.filter(site=site, session_id__in=ids)
    }
    recordings = {r.session_id: r for r in Recording.objects.filter(site=site, session_id__in=ids)}
    out = []
    for row in rows:
        nav = navigation.get(row["session_id"])
        recording = recordings.get(row["session_id"])
        out.append(
            {
                "session_id": row["session_id"],
                "visitor_id": row["visitor_id"],
                "started_at": iso(row["started_at"]),
                "ended_at": iso(row["ended_at"]),
                "pageviews": row["pageviews"],
                "quick_backs": nav.quick_backs if nav else 0,
                "loops": nav.loops if nav else 0,
                "recording": str(recording.pk) if recording else None,
            }
        )
    return out
