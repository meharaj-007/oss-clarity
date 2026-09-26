"""How fast one tracker may report, and whose address is asking.

The collector is public by construction: the key ships in the site's HTML.
The host check stops a snippet pasted on the wrong site; nothing at the HTTP
layer can tell a browser from a script. So this bounds what either can do,
with three windows per minute, most specific first:

* `key_ip`: one address hammering one site. Keyed on the pair, because an
  office behind one address is many real visitors.
* `key`: one site's numbers inflated from many addresses. Set far above what
  a real site does: set too low, it deletes genuine traffic.
* `global`: the whole deployment.

A tripped window answers 204 and writes nothing, like every other refusal.
The client address is used here, in memory, and never stored or logged.
"""

from __future__ import annotations

import ipaddress
import logging
import time
from collections.abc import Callable

from django.core.cache import cache
from django.http import HttpRequest
from django.utils.module_loading import import_string

from .conf import settings

logger = logging.getLogger(__name__)

WINDOW_SECONDS = 60

_KINDS = {
    "hit": ("oc:th:", ("HIT_RATE_KEY_IP", "HIT_RATE_KEY", "HIT_RATE_GLOBAL")),
    "chunk": ("oc:tc:", ("CHUNK_RATE_KEY_IP", "CHUNK_RATE_KEY", "CHUNK_RATE_GLOBAL")),
}


def client_ip(request: HttpRequest) -> str:
    """The visitor's address, resolved as the settings say.

    `CLIENT_IP_FUNCTION` wins, then `CLIENT_IP_HEADER`, then
    `TRUSTED_PROXY_COUNT`: with `n` trusted proxies the address is the `n`-th
    entry of `X-Forwarded-For` from the right, so a client cannot choose its
    own by adding entries on the left. Anything unusable falls back to
    `REMOTE_ADDR`.
    """
    remote = _valid(request.META.get("REMOTE_ADDR", "")) or ""

    function_path = settings.CLIENT_IP_FUNCTION
    if function_path:
        function: Callable[[HttpRequest], str | None] = import_string(function_path)
        try:
            return _valid(function(request) or "") or remote
        except Exception:
            logger.warning("CLIENT_IP_FUNCTION failed; using REMOTE_ADDR", exc_info=True)
            return remote

    header = settings.CLIENT_IP_HEADER
    if header:
        meta_name = "HTTP_" + header.upper().replace("-", "_")
        return _valid(request.META.get(meta_name, "")) or remote

    count = settings.TRUSTED_PROXY_COUNT
    if count > 0:
        chain = [
            part.strip()
            for part in request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")
            if part.strip()
        ]
        if len(chain) >= count:
            return _valid(chain[-count]) or remote
    return remote


def _valid(value: str) -> str | None:
    value = (value or "").strip()
    if value.startswith("[") and "]" in value:
        value = value[1 : value.index("]")]  # "[::1]:443"
    elif value.count(":") == 1:
        value = value.split(":")[0]  # "203.0.113.9:443"
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return None


def check(key: str, ip: str, *, kind: str = "hit") -> tuple[str, bool] | None:
    """`None` to accept, or `(scope, first_trip)` to refuse.

    `first_trip` is true only for the request that crosses a limit, so the
    site's counter is written once per window rather than once per refused
    request.
    """
    prefix, names = _KINDS[kind]
    bucket = int(time.time()) // WINDOW_SECONDS
    windows = (
        ("key_ip", f"{prefix}ki:{key}:{ip}:{bucket}", getattr(settings, names[0])),
        ("key", f"{prefix}k:{key}:{bucket}", getattr(settings, names[1])),
        ("global", f"{prefix}g:{bucket}", getattr(settings, names[2])),
    )
    for scope, cache_key, limit in windows:
        if limit <= 0:
            continue
        count = _incr(cache_key)
        if count > limit:
            return scope, count == limit + 1
    return None


def _incr(cache_key: str) -> int:
    """Increment a counter, creating it with a TTL on first use. Fails open:
    a collector that refuses traffic whenever the cache hiccups loses data
    nobody can recover."""
    try:
        if cache.add(cache_key, 1, timeout=WINDOW_SECONDS + 1):
            return 1
        return cache.incr(cache_key)
    except ValueError:
        # Expired between the add and the incr.
        cache.set(cache_key, 1, timeout=WINDOW_SECONDS + 1)
        return 1
    except Exception as exc:
        logger.warning("rate limit counter failed: %s", exc.__class__.__name__)
        return 0
