"""Enumerated values stored in the database."""

from __future__ import annotations

from django.db import models


class HitType(models.TextChoices):
    """What the tracker reported.

    `pageview` is a page load or an SPA route change. `click` is one click,
    kept for heatmaps. `pageleave` is a page's last report: how far it was
    scrolled and how long it was in front of the visitor.
    """

    PAGEVIEW = "pageview", "Page view"
    CLICK = "click", "Click"
    PAGELEAVE = "pageleave", "Page leave"


class DeviceClass(models.TextChoices):
    """The layouts a heatmap is drawn for. A click at one pixel on a phone and
    on a desktop are on different parts of the page, so maps are never summed
    across classes."""

    DESKTOP = "desktop", "Desktop"
    TABLET = "tablet", "Tablet"
    MOBILE = "mobile", "Mobile"


class MaskMode(models.TextChoices):
    """How much of a recorded page the visitor's browser blanks before upload.

    Form fields are masked in every mode and cannot be unmasked: that is the
    floor, not a mode.
    """

    STRICT = "strict", "Strict: all text and images"
    BALANCED = "balanced", "Balanced: numbers and email addresses"
    RELAXED = "relaxed", "Relaxed: form fields only"


class RecordingStatus(models.TextChoices):
    """`recording` while chunks arrive; `complete` once the visit has been
    quiet for the idle window; `capped` when the recorder stopped itself at a
    limit, so the visit went on after the replay ends; `truncated` when the
    collector dropped part of it."""

    RECORDING = "recording", "Recording"
    COMPLETE = "complete", "Complete"
    CAPPED = "capped", "Capped"
    TRUNCATED = "truncated", "Truncated"


class StopReason(models.TextChoices):
    """Why the recorder stopped before the visit ended."""

    NONE = "", "Not stopped"
    BYTES = "bytes", "Byte cap"
    PAGES = "pages", "Page cap"
    TIME = "time", "Time cap"


class DropReason(models.TextChoices):
    """Why the collector refused a recording chunk. A crawler's chunk is
    refused too, but is not a drop: nobody is missing a recording."""

    DISABLED = "disabled", "Recording is switched off"
    SAMPLING = "sampling", "Session not selected"
    THROTTLE = "throttle", "Rate limit"
    BYTES = "bytes", "Session byte cap"
    OVERSIZE = "oversize", "Chunk too large"
    INVALID = "invalid", "Malformed chunk"


class ThrottleScope(models.TextChoices):
    """Which rate limit tripped: one script, one site's key, or everything."""

    KEY_IP = "key_ip", "One address on one key"
    KEY = "key", "One key"
    GLOBAL = "global", "Whole deployment"
