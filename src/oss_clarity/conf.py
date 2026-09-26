"""Settings: one `OSS_CLARITY` dict in the host's settings, every key defaulted.

    OSS_CLARITY = {
        "RECORDING_RETENTION_DAYS": 14,
        "THRESHOLDS": {"hesitation_min_ms": 2_000},
    }

Read through `conf.settings.NAME`, which looks the value up on every access
so `override_settings` in tests works. Unknown keys and wrong types are
reported by `manage.py check`.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings as django_settings
from django.core.checks import Error, Tags, register

from .core import Thresholds

MIB = 1024 * 1024

DEFAULTS: dict[str, Any] = {
    # Recording, for the whole deployment. Each site is also off until enabled.
    "RECORDING_ENABLED": True,
    "DEFAULT_SAMPLE_PCT": 100,
    "DEFAULT_MASK_MODE": "balanced",
    # Caps, enforced by the recorder and again by the collector.
    "MAX_CHUNK_BYTES": 3 * MIB,
    "MAX_INFLATED_BYTES": 4 * MIB,
    "MAX_SESSION_BYTES": 10 * MIB,
    "MAX_MINUTES": 120,
    "MAX_PAGES": 128,
    "MAX_EVENTS_PER_CHUNK": 5_000,
    # Rate limits per minute. 0 disables that window.
    "HIT_RATE_KEY_IP": 120,
    "HIT_RATE_KEY": 6_000,
    "HIT_RATE_GLOBAL": 60_000,
    "CHUNK_RATE_KEY_IP": 60,
    "CHUNK_RATE_KEY": 3_000,
    "CHUNK_RATE_GLOBAL": 30_000,
    # Retention in days. Navigation rows go with the hits they came from.
    "HIT_RETENTION_DAYS": 30,
    "RECORDING_RETENTION_DAYS": 30,
    "HEATMAP_RETENTION_DAYS": 400,
    # Analysis.
    "QUICK_BACK_SECONDS": 5,
    "SESSION_IDLE_MINUTES": 30,
    "THRESHOLDS": {},
    "PAGEVIEW_TAGS": ("oss_clarity.pageview",),
    "ERROR_TAGS": ("oss_clarity.error",),
    # Storage: a key of Django's STORAGES, and the folder chunks go under.
    "STORAGE": "default",
    "STORAGE_PREFIX": "oss_clarity/recordings",
    # Absolute origin the snippet points at, when the collector is served from
    # another host than the admin (for example "https://collect.example.com").
    "PUBLIC_BASE_URL": None,
    # Client IP, used in memory only, for rate limits. See the settings docs.
    "TRUSTED_PROXY_COUNT": 0,
    "CLIENT_IP_HEADER": None,
    "CLIENT_IP_FUNCTION": None,
    # Minutes between runs of each job.
    "JOB_INTERVALS": {"finalize": 5, "navigation": 15, "heatmaps": 60, "prune": 1_440},
    # Dotted path to `f(request) -> bool` guarding the JSON API. None: staff only.
    "API_PERMISSION": None,
}

_INT_KEYS = frozenset(
    key for key, value in DEFAULTS.items() if isinstance(value, int) and not isinstance(value, bool)
)
_OPTIONAL_STR_KEYS = frozenset(
    {"PUBLIC_BASE_URL", "CLIENT_IP_HEADER", "CLIENT_IP_FUNCTION", "API_PERMISSION"}
)


def _user() -> dict[str, Any]:
    value = getattr(django_settings, "OSS_CLARITY", None) or {}
    return value if isinstance(value, dict) else {}


class _Settings:
    def __getattr__(self, name: str) -> Any:
        if name not in DEFAULTS:
            raise AttributeError(f"OSS_CLARITY has no setting {name!r}")
        user = _user()
        if name == "JOB_INTERVALS":
            return {**DEFAULTS[name], **(user.get(name) or {})}
        return user.get(name, DEFAULTS[name])

    @property
    def thresholds(self) -> Thresholds:
        return Thresholds.from_mapping(self.THRESHOLDS)


settings = _Settings()


@register(Tags.compatibility)
def check_settings(app_configs=None, **kwargs) -> list[Error]:
    errors: list[Error] = []
    raw = getattr(django_settings, "OSS_CLARITY", None)
    if raw is None:
        return errors
    if not isinstance(raw, dict):
        return [Error("OSS_CLARITY must be a dict.", id="oss_clarity.E001")]

    for key in sorted(set(raw) - set(DEFAULTS)):
        errors.append(Error(f"OSS_CLARITY has an unknown key {key!r}.", id="oss_clarity.E002"))
    for key in sorted(_INT_KEYS & set(raw)):
        value = raw[key]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            errors.append(
                Error(
                    f"OSS_CLARITY[{key!r}] must be a non-negative integer.",
                    id="oss_clarity.E003",
                )
            )
    for key in sorted(_OPTIONAL_STR_KEYS & set(raw)):
        if raw[key] is not None and not isinstance(raw[key], str):
            errors.append(
                Error(f"OSS_CLARITY[{key!r}] must be a string or None.", id="oss_clarity.E004")
            )
    sample_pct = raw.get("DEFAULT_SAMPLE_PCT", 0)
    if isinstance(sample_pct, int) and not 0 <= sample_pct <= 100:
        errors.append(
            Error("OSS_CLARITY['DEFAULT_SAMPLE_PCT'] must be 0 to 100.", id="oss_clarity.E005")
        )
    if "DEFAULT_MASK_MODE" in raw and raw["DEFAULT_MASK_MODE"] not in (
        "strict",
        "balanced",
        "relaxed",
    ):
        errors.append(
            Error(
                "OSS_CLARITY['DEFAULT_MASK_MODE'] must be strict, balanced or relaxed.",
                id="oss_clarity.E006",
            )
        )
    if "THRESHOLDS" in raw:
        try:
            Thresholds.from_mapping(raw["THRESHOLDS"])
        except (TypeError, ValueError) as exc:
            errors.append(Error(f"OSS_CLARITY['THRESHOLDS']: {exc}", id="oss_clarity.E007"))
    if "JOB_INTERVALS" in raw:
        intervals = raw["JOB_INTERVALS"]
        known = set(DEFAULTS["JOB_INTERVALS"])
        if not isinstance(intervals, dict) or set(intervals) - known:
            errors.append(
                Error(
                    f"OSS_CLARITY['JOB_INTERVALS'] takes minutes for: {', '.join(sorted(known))}.",
                    id="oss_clarity.E008",
                )
            )
        elif any(
            not isinstance(v, int) or isinstance(v, bool) or v < 1 for v in intervals.values()
        ):
            errors.append(
                Error(
                    "OSS_CLARITY['JOB_INTERVALS'] values must be whole minutes, at least 1.",
                    id="oss_clarity.E008",
                )
            )
    if "STORAGE" in raw:
        storages = getattr(django_settings, "STORAGES", {}) or {}
        if raw["STORAGE"] not in storages:
            errors.append(
                Error(
                    f"OSS_CLARITY['STORAGE'] is {raw['STORAGE']!r}, which is not in STORAGES.",
                    id="oss_clarity.E009",
                )
            )
    return errors
