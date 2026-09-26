"""The read-only JSON API. Plain Django views, no REST framework.

Every view asks `OSS_CLARITY["API_PERMISSION"]`, a dotted path to
`f(request) -> bool`; without one, only active staff users get in. Lists
take `?page=` and `?days=` (1 to 400, default 30).
"""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps

from django.http import HttpRequest, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.module_loading import import_string
from django.views.decorators.http import require_GET

from .. import heatmaps, reading
from ..choices import DeviceClass
from ..conf import settings
from ..models import Recording, Site


def staff_only(request: HttpRequest) -> bool:
    user = getattr(request, "user", None)
    return bool(user and user.is_active and user.is_staff)


def allowed(request: HttpRequest) -> bool:
    path = settings.API_PERMISSION
    check: Callable[[HttpRequest], bool] = import_string(path) if path else staff_only
    return bool(check(request))


def api_view(view: Callable) -> Callable:
    @require_GET
    @wraps(view)
    def wrapper(request: HttpRequest, *args, **kwargs):
        if not allowed(request):
            return JsonResponse({"detail": "Not allowed."}, status=403)
        response = view(request, *args, **kwargs)
        response["Cache-Control"] = "private, no-store"
        return response

    return wrapper


def number(request: HttpRequest, name: str, default: int) -> int:
    try:
        return int(request.GET.get(name, default))
    except (TypeError, ValueError):
        return default


def page_of(request: HttpRequest, items, shape: Callable) -> JsonResponse:
    page = reading.paginate(items, number(request, "page", 1))
    return JsonResponse(
        {
            "data": shape(page["items"]),
            "page": page["page"],
            "pages": page["pages"],
            "count": page["count"],
        }
    )


def events_url(recording: Recording) -> Callable[[int], str]:
    def url(page_seq: int) -> str:
        return reverse("oss_clarity_api:page-events", args=[recording.pk, page_seq])

    return url


@api_view
def recordings(request: HttpRequest, site_id: int) -> JsonResponse:
    site = get_object_or_404(Site, pk=site_id)
    queryset = reading.recordings_for(
        site,
        has=reading.parse_signals(request.GET.get("has")),
        favorites=request.GET.get("favorites") in ("1", "true"),
        days=number(request, "days", 30),
    )
    return page_of(request, queryset, lambda rows: [reading.recording_summary(r) for r in rows])


@api_view
def recording(request: HttpRequest, recording_id) -> JsonResponse:
    found = get_object_or_404(Recording, pk=recording_id)
    return JsonResponse(reading.recording_detail(found, events_url(found)))


@api_view
def page_events(request: HttpRequest, recording_id, page_seq: int) -> JsonResponse:
    found = get_object_or_404(Recording, pk=recording_id)
    events = reading.events_for_page(found, page_seq)
    if events is None:
        return JsonResponse({"detail": "No such page."}, status=404)
    return JsonResponse({"events": events})


@api_view
def heatmap_pages(request: HttpRequest, site_id: int) -> JsonResponse:
    site = get_object_or_404(Site, pk=site_id)
    return JsonResponse({"data": heatmaps.pages_summary(site, days=number(request, "days", 30))})


@api_view
def heatmap(request: HttpRequest, site_id: int) -> JsonResponse:
    site = get_object_or_404(Site, pk=site_id)
    device = request.GET.get("device") or DeviceClass.DESKTOP
    if device not in DeviceClass.values:
        return JsonResponse({"detail": "device must be desktop, tablet or mobile."}, status=400)
    data = heatmaps.heatmap(
        site, path=request.GET.get("path") or "/", device=device, days=number(request, "days", 30)
    )
    if data["backdrop"]:
        data["backdrop"]["events_url"] = reverse(
            "oss_clarity_api:page-events",
            args=[data["backdrop"]["recording"], data["backdrop"]["page_seq"]],
        )
    return JsonResponse(data)


@api_view
def sessions(request: HttpRequest, site_id: int) -> JsonResponse:
    site = get_object_or_404(Site, pk=site_id)
    rows = reading.sessions_for(
        site,
        has=reading.parse_signals(request.GET.get("has")),
        recorded=request.GET.get("recorded") in ("1", "true"),
        days=number(request, "days", 30),
    )
    return page_of(request, rows, lambda page: reading.session_rows(site, page))
