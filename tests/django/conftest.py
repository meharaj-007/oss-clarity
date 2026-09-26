from __future__ import annotations

import pytest
from django.core.cache import cache

from oss_clarity.models import RecordingSettings, Site

UA_CHROME = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
ORIGIN = "https://shop.example"


@pytest.fixture(autouse=True)
def fresh_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def site(db):
    return Site.objects.create(name="Shop", domain="shop.example")


@pytest.fixture
def recording_on(site):
    return RecordingSettings.objects.create(site=site, enabled=True, sample_pct=100)
