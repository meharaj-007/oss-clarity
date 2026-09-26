"""Public endpoints, called by visitors' browsers on the sites being tracked.

Unauthenticated by necessity: the site key in the request identifies the
site, not a person. Every refusal answers 204 with no body, because a status
that explained itself would print in the console of a page nobody here owns
and would tell a prober where each line is. Any origin may call them; no
credentials are ever accepted.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from functools import wraps

from django.http import HttpRequest, HttpResponse, HttpResponseNotAllowed
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt

from .. import throttle
from ..choices import DropReason
from ..collect import (
    build_hit,
    host_is_allowed,
    record_hit,
    record_rejected_host,
    record_throttled,
    reporting_host,
    resolve_site,
)
from ..conf import settings
from ..core import is_bot_user_agent
from ..record import (
    ChunkRejected,
    drop_once_per_session,
    inflate,
    ingest_chunk,
    parse_chunk,
    record_drop,
    tracker_record_config,
)
from ..tracker import recorder_source, render_tracker

logger = logging.getLogger(__name__)

#: A report is a few hundred bytes; anything far bigger is not from the tracker.
MAX_HIT_BYTES = 16 * 1024
#: The tracker is cached briefly, since recording settings are baked into it.
TRACKER_MAX_AGE = 300
#: Refusals that say something the site owner can act on.
COUNTED_DROPS = frozenset(DropReason.values) - {DropReason.INVALID}


def cors(response: HttpResponse) -> HttpResponse:
    response["Access-Control-Allow-Origin"] = "*"
    response["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response["Access-Control-Allow-Headers"] = "Content-Type, Content-Encoding"
    response["Access-Control-Max-Age"] = "86400"
    return response


def public_endpoint(*methods: str) -> Callable:
    """CSRF-exempt, CORS for any origin, preflight answered, other methods 405."""

    def decorate(view: Callable) -> Callable:
        @csrf_exempt
        @wraps(view)
        def wrapper(request: HttpRequest, *args, **kwargs) -> HttpResponse:
            if request.method == "OPTIONS":
                return cors(HttpResponse(status=204))
            if request.method not in methods:
                return cors(HttpResponseNotAllowed([*methods, "OPTIONS"]))
            return cors(view(request, *args, **kwargs))

        return wrapper

    return decorate


def no_content() -> HttpResponse:
    return HttpResponse(status=204)


def absolute(request: HttpRequest, name: str, *args) -> str:
    path = reverse(f"oss_clarity_public:{name}", args=args)
    base = (settings.PUBLIC_BASE_URL or "").rstrip("/")
    return f"{base}{path}" if base else request.build_absolute_uri(path)


def javascript(body: str, *, cache_control: str) -> HttpResponse:
    response = HttpResponse(body, content_type="application/javascript; charset=utf-8")
    response["Cache-Control"] = cache_control
    response["X-Content-Type-Options"] = "nosniff"
    # Read back by `PublicEndpointsMiddleware` if anything below rewrites it.
    response._oc_cache_control = cache_control
    return response


@public_endpoint("GET", "HEAD")
def tracker_script(request: HttpRequest, key: str) -> HttpResponse:
    """The tracker for one site. Always 200: a 404 would be an error in the
    console of the site that pasted the snippet. An unknown key gets an
    inert script."""
    site = resolve_site(key)
    if site is None:
        body = "/* oss-clarity: unknown or inactive site key */\n"
    else:
        digest, _ = recorder_source()
        record = tracker_record_config(
            site,
            endpoint=absolute(request, "record"),
            recorder_url=absolute(request, "recorder", digest),
        )
        body = render_tracker(
            site.public_key,
            absolute(request, "collect"),
            record,
            session_idle_minutes=settings.SESSION_IDLE_MINUTES,
        )
    return javascript(body, cache_control=f"public, max-age={TRACKER_MAX_AGE}")


@public_endpoint("GET", "HEAD")
def recorder_script(request: HttpRequest, digest: str) -> HttpResponse:
    """The recorder. The digest is in the URL, so it is cached for a year.
    A superseded digest, from a tracker cached before a rebuild, gets an
    inert file rather than an error."""
    current, source = recorder_source()
    if digest != current:
        return javascript(
            "/* oss-clarity: recorder version superseded */\n",
            cache_control=f"public, max-age={TRACKER_MAX_AGE}",
        )
    return javascript(source, cache_control="public, max-age=31536000, immutable")


@public_endpoint("POST")
def collect(request: HttpRequest) -> HttpResponse:
    """One page view, click or page leave."""
    body = request.read(MAX_HIT_BYTES + 1)
    if len(body) > MAX_HIT_BYTES:
        return no_content()
    try:
        payload = json.loads(body.decode("utf-8") or "{}")
    except (ValueError, UnicodeDecodeError):
        return no_content()
    if not isinstance(payload, dict):
        return no_content()

    site = resolve_site(str(payload.get("key") or ""))
    if site is None:
        return no_content()

    # The snippet is public once pasted; without this anyone could send hits
    # for the site from their own. Counted, because "nobody visited" and
    # "everything came from the wrong host" look the same otherwise.
    host = reporting_host(
        origin=request.META.get("HTTP_ORIGIN", ""), referer=request.META.get("HTTP_REFERER", "")
    )
    if not host_is_allowed(site, host):
        record_rejected_host(site, host)
        return no_content()

    throttled = throttle.check(site.public_key, throttle.client_ip(request), kind="hit")
    if throttled is not None:
        scope, first_trip = throttled
        if first_trip:
            record_throttled(site, scope)
        return no_content()

    row = build_hit(payload, user_agent=request.META.get("HTTP_USER_AGENT", ""))
    if row is not None:
        try:
            record_hit(site, row)
        except Exception:
            logger.exception("hit not written")
    return no_content()


@public_endpoint("POST")
def record(request: HttpRequest) -> HttpResponse:
    """One recording chunk: JSON, gzip-compressed when the browser could."""
    body = request.read(settings.MAX_CHUNK_BYTES + 1)
    if len(body) > settings.MAX_CHUNK_BYTES:
        return no_content()
    gzipped = "gzip" in (request.META.get("HTTP_CONTENT_ENCODING") or "").lower()
    try:
        chunk = parse_chunk(inflate(body, gzipped=gzipped))
    except ChunkRejected:
        return no_content()

    # Crawlers that run JavaScript reach the recorder too. A replay of one is
    # not a visit, and it would skew thresholds measured from recordings.
    # Refused, and not counted as a drop: nothing the site owner can fix.
    if is_bot_user_agent(request.META.get("HTTP_USER_AGENT", "")):
        return no_content()

    site = resolve_site(chunk["key"])
    if site is None:
        return no_content()
    host = reporting_host(
        origin=request.META.get("HTTP_ORIGIN", ""), referer=request.META.get("HTTP_REFERER", "")
    )
    if not host_is_allowed(site, host):
        record_rejected_host(site, host)
        return no_content()

    throttled = throttle.check(site.public_key, throttle.client_ip(request), kind="chunk")
    if throttled is not None:
        _scope, first_trip = throttled
        if first_trip:
            record_drop(site, DropReason.THROTTLE)
        return no_content()

    try:
        ingest_chunk(site, chunk)
    except ChunkRejected as refused:
        # A late chunk after its visit closed is not counted: nothing is missing.
        if refused.reason in COUNTED_DROPS:
            drop_once_per_session(site, chunk["session_id"], refused.reason)
    except Exception:
        # Storage or database unavailable. The chunk is lost; the page views
        # are a separate path and are not.
        logger.exception("recording chunk not written")
    return no_content()
