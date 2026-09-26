from __future__ import annotations

import pytest
from django.urls import reverse
from django.utils import timezone

from oss_clarity.models import Hit, JobRun, Recording, Site

pytestmark = pytest.mark.django_db


@pytest.fixture
def site():
    return Site.objects.create(name="Shop", domain="shop.example")


@pytest.fixture
def recording(site):
    now = timezone.now()
    return Recording.objects.create(
        site=site,
        session_id="s-1",
        started_at=now,
        last_event_at=now,
        duration_ms=83_000,
        rage_clicks=3,
        form_abandons=1,
    )


def test_every_page_opens(admin_client, site, recording):
    Hit.objects.create(site=site, path="/pricing", session_id="s-1")
    JobRun.objects.create(name="prune")
    for name in ("site", "recording", "hit", "jobrun"):
        response = admin_client.get(reverse(f"admin:oss_clarity_{name}_changelist"))
        assert response.status_code == 200, name
    assert (
        admin_client.get(reverse("admin:oss_clarity_site_change", args=[site.pk])).status_code
        == 200
    )
    detail = reverse("admin:oss_clarity_recording_change", args=[recording.pk])
    assert admin_client.get(detail).status_code == 200


def test_the_site_page_shows_its_snippet(admin_client, site):
    page = admin_client.get(reverse("admin:oss_clarity_site_change", args=[site.pk]))
    expected = f"http://testserver/oc/t/{site.public_key}.js"
    assert (
        f"&lt;script async src=&quot;{expected}&quot;&gt;&lt;/script&gt;" in page.content.decode()
    )


def test_the_snippet_uses_the_public_base_url_when_set(admin_client, site, settings):
    settings.OSS_CLARITY = {"PUBLIC_BASE_URL": "https://collect.example.net/"}
    page = admin_client.get(reverse("admin:oss_clarity_site_change", args=[site.pk]))
    assert f"https://collect.example.net/oc/t/{site.public_key}.js" in page.content.decode()


def test_the_snippet_waits_for_the_public_urls(admin_client, site, settings):
    settings.ROOT_URLCONF = "tests.django.urls_admin_only"
    page = admin_client.get(reverse("admin:oss_clarity_site_change", args=[site.pk]))
    assert "Include oss_clarity.urls.public" in page.content.decode()


def test_a_site_is_created_with_its_recording_settings_inline(admin_client):
    response = admin_client.post(
        reverse("admin:oss_clarity_site_add"),
        {
            "name": "Shop",
            "domain": "shop.example",
            "allowed_hosts": "[]",
            "is_active": "on",
            "recording_settings-TOTAL_FORMS": "1",
            "recording_settings-INITIAL_FORMS": "0",
            "recording_settings-MIN_NUM_FORMS": "0",
            "recording_settings-MAX_NUM_FORMS": "1",
            "recording_settings-0-enabled": "on",
            "recording_settings-0-sample_pct": "50",
            "recording_settings-0-mask_mode": "strict",
            "recording_settings-0-mask_selectors": "[]",
            "recording_settings-0-unmask_selectors": "[]",
            "recording_settings-0-block_selectors": "[]",
        },
    )
    assert response.status_code == 302, response.content.decode()[:2000]
    site = Site.objects.get()
    assert site.recording_settings.enabled and site.recording_settings.sample_pct == 50


def test_recordings_filter_by_signal_and_show_their_counts(admin_client, site, recording):
    now = timezone.now()
    Recording.objects.create(site=site, session_id="s-2", started_at=now, last_event_at=now)
    url = reverse("admin:oss_clarity_recording_changelist")

    page = admin_client.get(url, {"has": "rage"}).content.decode()
    assert "rage 3, form abandon 1" in page and "1:23" in page
    assert admin_client.get(url, {"has": "rage"}).context["cl"].result_count == 1
    assert admin_client.get(url, {"has": "loop"}).context["cl"].result_count == 0
    assert admin_client.get(url).context["cl"].result_count == 2


def test_recordings_can_be_favourited_but_not_edited(admin_client, recording):
    url = reverse("admin:oss_clarity_recording_changelist")
    admin_client.post(url, {"action": "mark_favorite", "_selected_action": [str(recording.pk)]})
    recording.refresh_from_db()
    assert recording.is_favorite
    add = admin_client.get(reverse("admin:oss_clarity_recording_add"))
    assert add.status_code == 403
