"""The viewer inside Django admin. Hits, recordings and job runs are read-only
here: they are written by the collector and the jobs, never by hand."""

from __future__ import annotations

from contextvars import ContextVar
from urllib.parse import urlencode

import django
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.urls import NoReverseMatch, path, reverse
from django.utils.html import format_html

from . import heatmaps, reading
from .choices import DeviceClass
from .conf import settings
from .models import SIGNAL_COLUMNS, Hit, JobRun, Recording, RecordingSettings, Site
from .retention import delete_recording, erase_visitor
from .tracker import snippet_for


def _days(request) -> int:
    try:
        return max(1, min(int(request.GET.get("days", 30)), 400))
    except (TypeError, ValueError):
        return 30


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request, obj=None) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False


class RecordingSettingsInline(admin.StackedInline):
    model = RecordingSettings
    can_delete = False
    extra = 0
    max_num = 1


#: The request being rendered, so a read-only field can build an absolute URL.
_current_request: ContextVar = ContextVar("oss_clarity_admin_request", default=None)


def tracker_url(site: Site, request=None) -> str | None:
    """The absolute URL the site's tracker is served from, or None before the
    public URLs are included in the host's URLconf. `PUBLIC_BASE_URL` when
    set, otherwise the host the admin is being viewed on."""
    try:
        path = reverse("oss_clarity_public:tracker", args=[site.public_key])
    except NoReverseMatch:
        return None
    base = (settings.PUBLIC_BASE_URL or "").rstrip("/")
    if base:
        return f"{base}{path}"
    return request.build_absolute_uri(path) if request is not None else path


@admin.register(Site)
class SiteAdmin(admin.ModelAdmin):
    list_display = ("name", "domain", "is_active", "last_hit_at", "rejected_hits", "dropped_chunks")
    list_filter = ("is_active",)
    search_fields = ("name", "domain")
    inlines = [RecordingSettingsInline]
    readonly_fields = (
        "install_snippet",
        "public_key",
        "last_hit_at",
        "rejected_hits",
        "last_rejected_host",
        "last_rejected_at",
        "throttled_hits",
        "last_throttled_at",
        "last_throttled_scope",
        "dropped_chunks",
        "last_drop_reason",
        "last_drop_at",
        "created_at",
        "updated_at",
    )
    fieldsets = (
        (None, {"fields": ("name", "domain", "allowed_hosts", "is_active")}),
        ("Install", {"fields": ("install_snippet", "public_key")}),
        (
            "Refused traffic",
            {
                "fields": (
                    "last_hit_at",
                    ("rejected_hits", "last_rejected_host", "last_rejected_at"),
                    ("throttled_hits", "last_throttled_scope", "last_throttled_at"),
                    ("dropped_chunks", "last_drop_reason", "last_drop_at"),
                )
            },
        ),
        (None, {"fields": ("created_at", "updated_at")}),
    )

    def get_urls(self):
        extra = [
            path(
                "<int:site_id>/heatmap/",
                self.admin_site.admin_view(self.heatmap_view),
                name="oss_clarity_site_heatmap",
            ),
            path(
                "<int:site_id>/heatmap.json",
                self.admin_site.admin_view(self.heatmap_data),
                name="oss_clarity_site_heatmap_data",
            ),
        ]
        return extra + super().get_urls()

    def _viewable(self, request, site_id) -> Site:
        site = get_object_or_404(Site, pk=site_id)
        if not self.has_view_permission(request, site):
            raise PermissionDenied
        return site

    def heatmap_view(self, request, site_id):
        site = self._viewable(request, site_id)
        days = _days(request)
        pages = heatmaps.pages_summary(site, days=days)
        path_ = request.GET.get("path") or (pages[0]["path"] if pages else "")
        device = request.GET.get("device") or ""
        if device not in DeviceClass.values:
            chosen = next((p for p in pages if p["path"] == path_), None)
            devices = chosen["devices"] if chosen else {}
            device = max(devices, key=devices.get) if devices else DeviceClass.DESKTOP
        data_url = ""
        if path_:
            query = urlencode({"path": path_, "device": device, "days": days})
            data_url = reverse("admin:oss_clarity_site_heatmap_data", args=[site.pk]) + "?" + query
        context = {
            **self.admin_site.each_context(request),
            "title": f"Heatmap: {site}",
            "opts": self.model._meta,
            "original": site,
            "site_obj": site,
            "pages": pages,
            "path": path_,
            "device": device,
            "devices": DeviceClass.choices,
            "days": days,
            "day_choices": (7, 30, 90, 400),
            "data_url": data_url,
            # Django 6 lists breadcrumbs; 5.2 writes them as one line.
            "oc_list_breadcrumbs": django.VERSION >= (6, 0),
        }
        return TemplateResponse(request, "admin/oss_clarity/site/heatmap.html", context)

    def heatmap_data(self, request, site_id):
        site = self._viewable(request, site_id)
        device = request.GET.get("device") or DeviceClass.DESKTOP
        if device not in DeviceClass.values:
            raise Http404("Unknown device class.")
        data = heatmaps.heatmap(
            site, path=request.GET.get("path") or "/", device=device, days=_days(request)
        )
        if data["backdrop"]:
            data["backdrop"]["events_url"] = reverse(
                "admin:oss_clarity_recording_events",
                args=[data["backdrop"]["recording"], data["backdrop"]["page_seq"]],
            )
        return JsonResponse(data)

    def change_view(self, request, object_id, form_url="", extra_context=None):
        extra_context = {
            **(extra_context or {}),
            "oc_heatmap_url": reverse("admin:oss_clarity_site_heatmap", args=[object_id]),
        }
        return super().change_view(request, object_id, form_url, extra_context)

    @admin.display(description="Snippet")
    def changeform_view(self, request, *args, **kwargs):
        token = _current_request.set(request)
        try:
            response = super().changeform_view(request, *args, **kwargs)
            # Rendered here, while the request is known, not lazily later.
            if hasattr(response, "render") and not getattr(response, "is_rendered", True):
                response.render()
            return response
        finally:
            _current_request.reset(token)

    def install_snippet(self, site: Site) -> str:
        if not site.pk:
            return "Save the site to get its snippet."
        url = tracker_url(site, _current_request.get())
        if url is None:
            return "Include oss_clarity.urls.public in your URLconf to get the snippet."
        return format_html("<code>{}</code>", snippet_for(url))


class SignalFilter(admin.SimpleListFilter):
    title = "signal"
    parameter_name = "has"

    def lookups(self, request, model_admin):
        return [(kind, kind.replace("_", " ")) for kind in SIGNAL_COLUMNS]

    def queryset(self, request, queryset):
        column = SIGNAL_COLUMNS.get(self.value() or "")
        return queryset.filter(**{f"{column}__gt": 0}) if column else queryset


@admin.register(Recording)
class RecordingAdmin(ReadOnlyAdmin):
    list_display = (
        "started_at",
        "site",
        "status",
        "duration",
        "page_count",
        "signals",
        "is_favorite",
    )
    list_filter = (SignalFilter, "is_favorite", "status", "site")
    date_hierarchy = "started_at"
    search_fields = ("session_id", "visitor_id")
    list_select_related = ("site",)
    actions = ["mark_favorite", "unmark_favorite", "erase_visitors", "delete_selected"]

    def has_delete_permission(self, request, obj=None) -> bool:
        return admin.ModelAdmin.has_delete_permission(self, request, obj)

    # Deleting goes through retention, which removes the stored chunks first
    # and keeps the rows if any chunk could not be removed.
    def delete_model(self, request, obj: Recording) -> None:
        if not delete_recording(obj):
            self.message_user(
                request, "Its stored chunks could not be deleted; try again.", messages.ERROR
            )

    def delete_queryset(self, request, queryset) -> None:
        kept = sum(0 if delete_recording(recording) else 1 for recording in queryset)
        if kept:
            self.message_user(
                request,
                f"{kept} recordings kept: their stored chunks could not be deleted; try again.",
                messages.ERROR,
            )

    @admin.action(description="Erase these visitors everywhere", permissions=["delete"])
    def erase_visitors(self, request, queryset) -> None:
        visitors = {v for v in queryset.values_list("visitor_id", flat=True) if v}
        totals = {"recordings": 0, "hits": 0, "navigation": 0, "failed": 0}
        for visitor_id in visitors:
            for key, value in erase_visitor(visitor_id).items():
                if key in totals:
                    totals[key] += value
        level = messages.ERROR if totals["failed"] else messages.SUCCESS
        self.message_user(
            request,
            f"Erased {len(visitors)} visitors: {totals['recordings']} recordings, "
            f"{totals['hits']} hits, {totals['navigation']} navigation rows"
            + (f"; {totals['failed']} recordings kept, try again" if totals["failed"] else "."),
            level,
        )

    def get_urls(self):
        extra = [
            path(
                "<uuid:recording_id>/replay.json",
                self.admin_site.admin_view(self.replay_data),
                name="oss_clarity_recording_replay",
            ),
            path(
                "<uuid:recording_id>/pages/<int:page_seq>/events.json",
                self.admin_site.admin_view(self.page_events_data),
                name="oss_clarity_recording_events",
            ),
        ]
        return extra + super().get_urls()

    def _viewable(self, request, recording_id) -> Recording:
        recording = get_object_or_404(Recording, pk=recording_id)
        if not self.has_view_permission(request, recording):
            raise PermissionDenied
        return recording

    def replay_data(self, request, recording_id):
        recording = self._viewable(request, recording_id)

        def events_url(page_seq: int) -> str:
            return reverse("admin:oss_clarity_recording_events", args=[recording.pk, page_seq])

        return JsonResponse(reading.recording_detail(recording, events_url))

    def page_events_data(self, request, recording_id, page_seq):
        events = reading.events_for_page(self._viewable(request, recording_id), page_seq)
        if events is None:
            raise Http404("No such page.")
        return JsonResponse({"events": events})

    def change_view(self, request, object_id, form_url="", extra_context=None):
        extra_context = {
            **(extra_context or {}),
            "oc_replay_url": reverse("admin:oss_clarity_recording_replay", args=[object_id]),
        }
        return super().change_view(request, object_id, form_url, extra_context)

    @admin.action(description="Keep as favourite")
    def mark_favorite(self, request, queryset) -> None:
        queryset.update(is_favorite=True)

    @admin.action(description="Remove from favourites")
    def unmark_favorite(self, request, queryset) -> None:
        queryset.update(is_favorite=False)

    @admin.display(description="Duration", ordering="duration_ms")
    def duration(self, recording: Recording) -> str:
        seconds = recording.duration_ms // 1000
        return f"{seconds // 60}:{seconds % 60:02d}"

    @admin.display(description="Signals")
    def signals(self, recording: Recording) -> str:
        found = [
            f"{kind.replace('_', ' ')} {getattr(recording, column)}"
            for kind, column in SIGNAL_COLUMNS.items()
            if getattr(recording, column)
        ]
        return ", ".join(found) or "-"


@admin.register(Hit)
class HitAdmin(ReadOnlyAdmin):
    list_display = ("created_at", "site", "hit_type", "path", "device_class", "browser", "is_bot")
    list_filter = ("hit_type", "device_class", "is_bot", "site")
    date_hierarchy = "created_at"
    search_fields = ("path", "session_id", "visitor_id")
    list_select_related = ("site",)


@admin.register(JobRun)
class JobRunAdmin(ReadOnlyAdmin):
    list_display = ("name", "last_started_at", "last_finished_at", "last_error")

    def has_delete_permission(self, request, obj=None) -> bool:
        return False
