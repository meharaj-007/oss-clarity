"""Turn one tracker report into one `Hit` row.

The browser reports what only it knows: the page, its title, the referrer,
its own anonymous ids, where a click landed. Everything else comes from the
request: the device from the user-agent header. Nothing that identifies the
visitor's network is kept.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from django.db.models import F
from django.utils import timezone

from .choices import DeviceClass, HitType
from .core import parse_user_agent
from .core.sampling import ID_RE
from .models import PUBLIC_KEY_PATTERN, Hit, Site

PATH_MAX = 512
URL_MAX = 2048

_KEY_RE = re.compile(rf"^{PUBLIC_KEY_PATTERN}$")

#: Query keys whose values are replaced before a URL is stored: password
#: resets and sign-in callbacks carry single-use secrets in the query string.
SENSITIVE_QUERY_KEYS = re.compile(
    r"passw|secret|token|auth|api[-_]?key|credential|signature|session|^code$|^otp$|^key$",
    re.IGNORECASE,
)
REDACTED = "redacted"

_DEVICE_CLASSES = {
    "Desktop": DeviceClass.DESKTOP,
    "Mobile": DeviceClass.MOBILE,
    "Tablet": DeviceClass.TABLET,
}


def page_path(url: str) -> str:
    """The page key for a URL: its path, no query or fragment, one trailing
    slash folded (except the root), case and percent-encoding kept, at most
    512 characters."""
    try:
        path = urlsplit(url or "").path
    except ValueError:
        path = ""
    if not path.startswith("/"):
        path = "/" + path
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    return path[:PATH_MAX]


def scrub_url(url: str) -> str:
    """The URL with credential-shaped query values replaced."""
    try:
        parts = urlsplit(url or "")
    except ValueError:
        return ""
    if not parts.query:
        return url[:URL_MAX]
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    query = urlencode(
        [(key, REDACTED if SENSITIVE_QUERY_KEYS.search(key) else value) for key, value in pairs]
    )
    return urlunsplit(parts._replace(query=query))[:URL_MAX]


def resolve_site(key: str) -> Site | None:
    """The active site behind a key, or None. Malformed and unknown keys get
    the same nothing: the caller learns nothing either way."""
    if not key or not _KEY_RE.match(key):
        return None
    return Site.objects.filter(public_key=key, is_active=True).first()


def host_of(value: str) -> str:
    """The bare host in a URL, an Origin header or a naked domain:
    lowercased, port and `www.` removed."""
    value = (value or "").strip().lower()
    if not value:
        return ""
    if "//" not in value:
        value = f"//{value}"
    try:
        host = urlsplit(value).netloc or ""
    except ValueError:
        return ""
    host = host.split("@")[-1]
    # An IPv6 literal keeps its brackets; anything else loses its port.
    host = host.split("]")[0] + "]" if host.startswith("[") else host.split(":")[0]
    return host[4:] if host.startswith("www.") else host


def reporting_host(*, origin: str, referer: str) -> str:
    """Where a report says it came from. `Origin` first: a page's script
    cannot rewrite it inside a browser. The payload's own URL is never
    evidence, since it is the thing being checked."""
    return host_of(origin) or host_of(referer)


def host_is_allowed(site: Site, host: str) -> bool:
    """The site's domain, a subdomain of it, or a host on its list."""
    if not host:
        return False
    for candidate in [site.domain, *(site.allowed_hosts or [])]:
        allowed = host_of(str(candidate))
        if allowed and (host == allowed or host.endswith(f".{allowed}")):
            return True
    return False


def record_rejected_host(site: Site, host: str) -> None:
    Site.objects.filter(pk=site.pk).update(
        rejected_hits=F("rejected_hits") + 1,
        last_rejected_host=(host or "")[:255],
        last_rejected_at=timezone.now(),
    )


def record_throttled(site: Site, scope: str) -> None:
    Site.objects.filter(pk=site.pk).update(
        throttled_hits=F("throttled_hits") + 1,
        last_throttled_at=timezone.now(),
        last_throttled_scope=scope,
    )


def build_hit(payload: dict, *, user_agent: str) -> dict | None:
    """The row for one report, or None when it is not worth keeping: an
    unknown type, a click without a position, a page leave without a depth."""
    hit_type = payload.get("type") or HitType.PAGEVIEW
    if hit_type not in HitType.values:
        return None

    url = scrub_url(str(payload.get("url") or ""))
    device = parse_user_agent(user_agent)
    row = {
        "hit_type": hit_type,
        "session_id": _id(payload.get("session_id")),
        "visitor_id": _id(payload.get("visitor_id")),
        "sequence": _clamped(payload.get("seq"), 0, 2_000_000_000),
        "url": url,
        "path": page_path(url),
        "title": str(payload.get("title") or "")[:300],
        "referrer": scrub_url(str(payload.get("referrer") or "")),
        "device_class": _DEVICE_CLASSES.get(device["device"], ""),
        "browser": device["browser"][:40],
        "os": device["os"][:40],
        "is_bot": device["device"] == "Bot" or device["browser"] == "Bot",
    }

    if hit_type == HitType.CLICK:
        row.update(
            {
                "x": _clamped(payload.get("x"), 0, 1_000_000),
                "y": _clamped(payload.get("y"), 0, 1_000_000),
                "rel_x": _clamped(payload.get("rel_x"), 0, 1000),
                "rel_y": _clamped(payload.get("rel_y"), 0, 1000),
                "viewport_w": _clamped(payload.get("viewport_w"), 1, 20_000),
                "viewport_h": _clamped(payload.get("viewport_h"), 1, 20_000),
                "page_h": _clamped(payload.get("page_h"), 1, 1_000_000),
                "element_selector": str(payload.get("element_selector") or "")[:255],
                "page_region": str(payload.get("page_region") or "")[:64],
            }
        )
        if row["x"] is None or row["y"] is None:
            return None
    elif hit_type == HitType.PAGELEAVE:
        row.update(
            {
                "scroll_depth_pct": _clamped(payload.get("scroll_depth_pct"), 0, 100),
                "active_seconds": _clamped(payload.get("active_seconds"), 0, 86_400),
                "viewport_w": _clamped(payload.get("viewport_w"), 1, 20_000),
                "viewport_h": _clamped(payload.get("viewport_h"), 1, 20_000),
                "page_h": _clamped(payload.get("page_h"), 1, 1_000_000),
            }
        )
        if row["scroll_depth_pct"] is None:
            return None
    return row


def record_hit(site: Site, row: dict) -> Hit:
    hit = Hit.objects.create(site=site, **row)
    Site.objects.filter(pk=site.pk).update(last_hit_at=hit.created_at)
    return hit


def _id(value) -> str:
    value = str(value or "")
    return value if ID_RE.match(value) else ""


def _clamped(value, low: int, high: int) -> int | None:
    """Out-of-range numbers are clamped, not rejected: a real click on a
    transformed or zoomed page can report a negative offset."""
    if isinstance(value, bool):
        return None
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError, OverflowError):
        return None
    return max(low, min(high, number))
