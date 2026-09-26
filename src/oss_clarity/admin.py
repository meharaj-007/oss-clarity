"""The viewer inside Django admin. Hits, recordings and job runs are read-only
here: they are written by the collector and the jobs, never by hand."""

from __future__ import annotations

from contextvars import ContextVar

from django.contrib import admin
from django.urls import NoReverseMatch, reverse
from django.utils.html import format_html

from .conf import settings
from .models import SIGNAL_COLUMNS, Hit, JobRun, Recording, RecordingSettings, Site
from .tracker import snippet_for


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
    actions = ["mark_favorite", "unmark_favorite"]

    def has_delete_permission(self, request, obj=None) -> bool:
        # Deleting must also delete the stored chunks; that arrives with the
        # storage layer. Until then rows are removed only by retention.
        return False

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
