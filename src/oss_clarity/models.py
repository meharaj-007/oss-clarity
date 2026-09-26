"""Database tables.

No IP address is stored anywhere. Visitor and session ids are random strings
the tracker keeps in the visitor's own browser; `visitor_id` is indexed on
every table that holds it so one visitor's data can be erased on request.
"""

from __future__ import annotations

import secrets
import uuid

from django.db import models

from .choices import (
    DeviceClass,
    DropReason,
    HitType,
    MaskMode,
    RecordingStatus,
    StopReason,
    ThrottleScope,
)
from .core import SIGNAL_KINDS

#: `Site.public_key`: 16 random bytes, URL-safe base64 without padding.
PUBLIC_KEY_LENGTH = 22
PUBLIC_KEY_PATTERN = r"[A-Za-z0-9_-]{22}"

#: Recording counter column for each signal `core.analyze` counts, and for the
#: two navigation signals read from page views.
SIGNAL_COLUMNS = {
    "rage": "rage_clicks",
    "dead": "dead_clicks",
    "error": "error_clicks",
    "script_error": "script_errors",
    "hesitation": "hesitations",
    "near_miss": "near_misses",
    "scroll_hunt": "scroll_hunts",
    "form_skip": "form_skips",
    "form_refill": "form_refills",
    "form_abandon": "form_abandons",
    "repeat_submit": "repeat_submits",
    "copy_out": "copy_outs",
    "idle_exit": "idle_exits",
    "quick_back": "quick_backs",
    "loop": "loops",
}
assert set(SIGNAL_KINDS) <= set(SIGNAL_COLUMNS)


def generate_public_key() -> str:
    key = secrets.token_urlsafe(16)
    assert len(key) == PUBLIC_KEY_LENGTH
    return key


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        abstract = True


class Site(TimestampedModel):
    """One website the tracker is installed on."""

    name = models.CharField(max_length=120)
    #: The site's main host, for example `www.example.com`. Hits are accepted
    #: from it and from its subdomains, and from `allowed_hosts`.
    domain = models.CharField(max_length=255)
    #: Further hosts that may report for this site (a second country domain,
    #: a staging host, `localhost` while wiring it up). The snippet is public
    #: once pasted, so without a host check anyone could send hits for it.
    allowed_hosts = models.JSONField(default=list, blank=True)
    public_key = models.CharField(
        max_length=PUBLIC_KEY_LENGTH, unique=True, default=generate_public_key, editable=False
    )
    is_active = models.BooleanField(default=True)
    last_hit_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    # What was turned away, and why. "No traffic" and "traffic refused" look
    # the same on an empty list; these tell them apart. Throttling and drops
    # are counted once per window or per session, never per request, so a
    # flood does not also become a flood of writes.
    rejected_hits = models.BigIntegerField(default=0)
    last_rejected_host = models.CharField(max_length=255, blank=True, default="")
    last_rejected_at = models.DateTimeField(null=True, blank=True)
    throttled_hits = models.BigIntegerField(default=0)
    last_throttled_at = models.DateTimeField(null=True, blank=True)
    last_throttled_scope = models.CharField(
        max_length=10, choices=ThrottleScope.choices, blank=True, default=""
    )
    dropped_chunks = models.BigIntegerField(default=0)
    last_drop_reason = models.CharField(
        max_length=10, choices=DropReason.choices, blank=True, default=""
    )
    last_drop_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name or self.domain


class RecordingSettings(TimestampedModel):
    """How one site records visits. Off until switched on."""

    site = models.OneToOneField(Site, on_delete=models.CASCADE, related_name="recording_settings")
    enabled = models.BooleanField(default=False)
    #: Percentage of visits recorded. Chosen by a hash of the session id, so
    #: every page of a visit agrees and the collector can check it.
    sample_pct = models.PositiveSmallIntegerField(default=100)
    mask_mode = models.CharField(max_length=10, choices=MaskMode.choices, default=MaskMode.BALANCED)
    #: CSS selectors applied in the visitor's browser: `mask` blanks text,
    #: `unmask` reveals it (never form fields), `block` replaces the element
    #: with a same-size placeholder.
    mask_selectors = models.JSONField(default=list, blank=True)
    unmask_selectors = models.JSONField(default=list, blank=True)
    block_selectors = models.JSONField(default=list, blank=True)
    #: Record nothing until the page calls `window.ossClarity("consent", true)`.
    consent_required = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "recording settings"

    def __str__(self) -> str:
        return f"{self.site} · recording {'on' if self.enabled else 'off'}"

    @classmethod
    def for_site(cls, site: Site) -> RecordingSettings:
        """The site's settings, created with the configured defaults on first use."""
        from .conf import settings

        row, _ = cls.objects.get_or_create(
            site=site,
            defaults={
                "sample_pct": settings.DEFAULT_SAMPLE_PCT,
                "mask_mode": settings.DEFAULT_MASK_MODE,
            },
        )
        return row


class Hit(TimestampedModel):
    """One report from a visitor's browser: a page view, a click or a page leave.

    Written once, never edited. The browser's raw user agent is not kept,
    only the coarse classes parsed from it.
    """

    site = models.ForeignKey(Site, on_delete=models.CASCADE, related_name="hits")
    hit_type = models.CharField(max_length=10, choices=HitType.choices, default=HitType.PAGEVIEW)

    # Anonymous ids from the visitor's browser. `visitor_id` lasts across
    # visits; `session_id` ends after the idle window.
    session_id = models.CharField(max_length=64, blank=True, default="")
    visitor_id = models.CharField(max_length=64, blank=True, default="", db_index=True)
    #: The tracker's own count of hits in this session. Requests can arrive
    #: out of order, so this, when every hit has one, is the true order.
    #: Not unique: two tabs can share a session.
    sequence = models.IntegerField(null=True, blank=True)

    #: The page key: the URL's path, no query or fragment, one trailing slash
    #: folded. See `collect.page_path`.
    path = models.CharField(max_length=512, blank=True, default="")
    url = models.TextField(blank=True, default="")
    title = models.CharField(max_length=300, blank=True, default="")
    #: The page the visitor came from, as the browser reported it.
    referrer = models.TextField(blank=True, default="")

    device_class = models.CharField(
        max_length=10, choices=DeviceClass.choices, blank=True, default=""
    )
    browser = models.CharField(max_length=40, blank=True, default="")
    os = models.CharField(max_length=40, blank=True, default="")
    #: Crawlers are kept, flagged, rather than silently dropped, so "how much
    #: of my traffic is bots" can be answered.
    is_bot = models.BooleanField(default=False)

    # Clicks. `x`/`y` are page pixels from the document's top-left, in a
    # window `viewport_w` wide; `rel_x`/`rel_y` are where inside the clicked
    # element, in thousandths of its box, which keeps a heatmap on the right
    # element when the page reflows.
    #: A short path to the clicked element. Its text and link are never
    #: collected: a heatmap needs where, not what.
    element_selector = models.CharField(max_length=255, blank=True, default="")
    #: The landmark around a click (`header`, `nav`, `main`, `aside`, `footer`).
    page_region = models.CharField(max_length=64, blank=True, default="")
    x = models.IntegerField(null=True, blank=True)
    y = models.IntegerField(null=True, blank=True)
    rel_x = models.PositiveSmallIntegerField(null=True, blank=True)
    rel_y = models.PositiveSmallIntegerField(null=True, blank=True)
    viewport_w = models.PositiveIntegerField(null=True, blank=True)
    viewport_h = models.PositiveIntegerField(null=True, blank=True)
    page_h = models.IntegerField(null=True, blank=True)

    # Page leaves: the deepest point the viewport's bottom reached, as a
    # percentage of the document, and the seconds the page was visible.
    scroll_depth_pct = models.PositiveSmallIntegerField(null=True, blank=True)
    active_seconds = models.IntegerField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["site", "-created_at"], name="oc_hit_site_created"),
            # Retention prunes by age across every site.
            models.Index(fields=["created_at"], name="oc_hit_created"),
            models.Index(fields=["site", "path"], name="oc_hit_site_path"),
            models.Index(fields=["site", "session_id", "created_at"], name="oc_hit_site_session"),
        ]

    def __str__(self) -> str:
        return f"{self.hit_type} {self.path or self.url}"


class Recording(TimestampedModel):
    """One visit's DOM recording: the row, not the bytes.

    The events are stored as gzip JSON chunks through Django's storage API;
    this row holds what the list and the player need without opening them.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    site = models.ForeignKey(Site, on_delete=models.CASCADE, related_name="recordings")
    session_id = models.CharField(max_length=64)
    visitor_id = models.CharField(max_length=64, blank=True, default="", db_index=True)

    status = models.CharField(
        max_length=10, choices=RecordingStatus.choices, default=RecordingStatus.RECORDING
    )
    stop_reason = models.CharField(
        max_length=10, choices=StopReason.choices, blank=True, default=""
    )
    #: The mask mode in force when recording started; the setting can change.
    mask_mode = models.CharField(max_length=10, choices=MaskMode.choices, default=MaskMode.BALANCED)

    started_at = models.DateTimeField()
    last_event_at = models.DateTimeField()
    #: First to last event on the visitor's clock, which stamps the events.
    duration_ms = models.BigIntegerField(default=0)
    page_count = models.IntegerField(default=0)
    chunk_count = models.IntegerField(default=0)
    event_count = models.IntegerField(default=0)
    #: Uncompressed bytes of event JSON: what the session cap counts.
    byte_size = models.BigIntegerField(default=0)

    # One column per signal, because the list filters on them. Definitions
    # are in `oss_clarity.core.signals` and `oss_clarity.core.navigation`.
    rage_clicks = models.IntegerField(default=0)
    dead_clicks = models.IntegerField(default=0)
    error_clicks = models.IntegerField(default=0)
    script_errors = models.IntegerField(default=0)
    hesitations = models.IntegerField(default=0)
    near_misses = models.IntegerField(default=0)
    scroll_hunts = models.IntegerField(default=0)
    form_skips = models.IntegerField(default=0)
    form_refills = models.IntegerField(default=0)
    form_abandons = models.IntegerField(default=0)
    repeat_submits = models.IntegerField(default=0)
    copy_outs = models.IntegerField(default=0)
    idle_exits = models.IntegerField(default=0)
    quick_backs = models.IntegerField(default=0)
    loops = models.IntegerField(default=0)
    #: `[{t_ms, page_id, kind, selector, label, x, y}]`, capped.
    markers = models.JSONField(default=list, blank=True)
    analyzed_at = models.DateTimeField(null=True, blank=True)

    is_favorite = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-started_at"]
        constraints = [
            models.UniqueConstraint(fields=["site", "session_id"], name="oc_recording_per_session"),
        ]
        indexes = [
            models.Index(fields=["site", "-started_at"], name="oc_rec_site_started"),
            models.Index(fields=["status", "last_event_at"], name="oc_rec_status_last"),
        ]

    def __str__(self) -> str:
        return f"{self.site} · {self.session_id[:12]} ({self.status})"


class RecordingChunk(TimestampedModel):
    """One upload from the recorder: a slice of one page's events."""

    recording = models.ForeignKey(Recording, on_delete=models.CASCADE, related_name="chunks")
    #: Minted per document load. A single-page app is one page id with many
    #: page-view events inside it.
    page_id = models.CharField(max_length=36)
    page_seq = models.IntegerField()
    chunk_seq = models.IntegerField()
    url = models.TextField(blank=True, default="")
    #: Visitor-clock milliseconds of the first and last event in the slice.
    first_ts = models.BigIntegerField()
    last_ts = models.BigIntegerField()
    event_count = models.IntegerField(default=0)
    byte_size = models.IntegerField(default=0)
    storage_key = models.CharField(max_length=512)
    is_final = models.BooleanField(default=False)

    class Meta:
        ordering = ["page_seq", "chunk_seq"]
        constraints = [
            models.UniqueConstraint(
                fields=["recording", "page_id", "chunk_seq"], name="oc_chunk_per_page"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.recording_id} · p{self.page_seq} c{self.chunk_seq}"


# Heatmaps: rolled up from hits one UTC day at a time, rebuilt from scratch for
# a (site, day) on every run. They hold counts, a selector and a path, nothing
# about any visitor, and outlive the hits.


class HeatmapPage(TimestampedModel):
    """What one page saw on one day on one device class; the map's denominator."""

    site = models.ForeignKey(Site, on_delete=models.CASCADE, related_name="heatmap_pages")
    path = models.CharField(max_length=512)
    device_class = models.CharField(max_length=10, choices=DeviceClass.choices)
    day = models.DateField()
    pageviews = models.IntegerField(default=0)
    clicks = models.IntegerField(default=0)
    #: Page views that reported how far they were scrolled.
    pageleaves = models.IntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["site", "path", "device_class", "day"], name="oc_heatmap_page_day"
            ),
        ]
        indexes = [models.Index(fields=["site", "day"], name="oc_hm_page_site_day")]


class HeatmapClickBucket(TimestampedModel):
    """Clicks on one page, one day, one device class, counted per place.

    Element rows (`selector_hash` set) count clicks on one element in a 10 × 10
    grid over its own box, so the heat stays on the element when the page
    reflows. Pixel rows (`selector_hash` blank) count by position: `bucket_x`
    a 2 % column of the window width (0 to 49), `bucket_y` a 25 px row of the
    document. Pixel rows are the fallback when the snapshot behind the map no
    longer has the element.
    """

    site = models.ForeignKey(Site, on_delete=models.CASCADE, related_name="heatmap_clicks")
    path = models.CharField(max_length=512)
    device_class = models.CharField(max_length=10, choices=DeviceClass.choices)
    day = models.DateField()
    selector_hash = models.CharField(max_length=16, blank=True, default="")
    selector = models.CharField(max_length=255, blank=True, default="")
    page_region = models.CharField(max_length=64, blank=True, default="")
    bucket_x = models.PositiveSmallIntegerField()
    bucket_y = models.PositiveSmallIntegerField()
    count = models.IntegerField(default=0)

    class Meta:
        indexes = [
            models.Index(fields=["site", "path", "device_class", "day"], name="oc_hm_click_page"),
            models.Index(fields=["site", "day"], name="oc_hm_click_site_day"),
        ]


class HeatmapScrollBucket(TimestampedModel):
    """How far page views of one page got. `depth_decile` is the deepest tenth
    reached (0 for under 10 %, 10 for the very bottom); `views` how many
    stopped there."""

    site = models.ForeignKey(Site, on_delete=models.CASCADE, related_name="heatmap_scrolls")
    path = models.CharField(max_length=512)
    device_class = models.CharField(max_length=10, choices=DeviceClass.choices)
    day = models.DateField()
    depth_decile = models.PositiveSmallIntegerField()
    views = models.IntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["site", "path", "device_class", "day", "depth_decile"],
                name="oc_heatmap_scroll_bucket",
            ),
        ]
        indexes = [models.Index(fields=["site", "day"], name="oc_hm_scroll_site_day")]


class SessionNavigation(TimestampedModel):
    """One visit's quick backs and loops, stored because lists filter on them.

    Only visits with at least one get a row. Rebuilt from page views, never
    edited, and deleted with the hits they came from.
    """

    site = models.ForeignKey(Site, on_delete=models.CASCADE, related_name="navigation")
    session_id = models.CharField(max_length=64)
    visitor_id = models.CharField(max_length=64, blank=True, default="", db_index=True)
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField()
    quick_backs = models.IntegerField(default=0)
    loops = models.IntegerField(default=0)
    #: `[{kind, a, b}]` in visit order, capped: which pages.
    occurrences = models.JSONField(default=list, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["site", "session_id"], name="oc_navigation_per_session"
            ),
        ]
        indexes = [models.Index(fields=["site", "started_at"], name="oc_nav_site_started")]

    def __str__(self) -> str:
        return f"{self.site} · {self.session_id[:12]} ({self.quick_backs}/{self.loops})"


class JobRun(TimestampedModel):
    """When each background job last ran. The source of truth for "is it
    due", so a cache flush or several servers never make a job run twice."""

    name = models.CharField(max_length=40, unique=True)
    last_started_at = models.DateTimeField(null=True, blank=True)
    last_finished_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=500, blank=True, default="")

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name
