from __future__ import annotations

import json

import pytest
from django.urls import reverse

from oss_clarity.collect import page_path, scrub_url
from oss_clarity.models import Hit, Site

from .conftest import ORIGIN, UA_CHROME

pytestmark = pytest.mark.django_db

COLLECT = "/oc/e/"


def hit(site, **overrides):
    payload = {
        "key": site.public_key,
        "type": "pageview",
        "seq": 1,
        "url": "https://shop.example/pricing/?utm_source=mail",
        "title": "Pricing",
        "referrer": "https://search.example/?q=bikes",
        "visitor_id": "v-1",
        "session_id": "s-1",
    }
    payload.update(overrides)
    return payload


def post(client, payload, *, origin=ORIGIN, ua=UA_CHROME, raw=None):
    headers = {"HTTP_USER_AGENT": ua}
    if origin is not None:
        headers["HTTP_ORIGIN"] = origin
    body = raw if raw is not None else json.dumps(payload)
    return client.post(COLLECT, data=body, content_type="text/plain", **headers)


def test_a_page_view_is_stored_with_its_page_and_device(client, site):
    assert post(client, hit(site)).status_code == 204
    row = Hit.objects.get()
    assert (row.hit_type, row.path, row.title) == ("pageview", "/pricing", "Pricing")
    assert row.url == "https://shop.example/pricing/?utm_source=mail"
    assert (row.session_id, row.visitor_id, row.sequence) == ("s-1", "v-1", 1)
    assert (row.device_class, row.browser, row.os, row.is_bot) == (
        "desktop",
        "Chrome",
        "Windows",
        False,
    )
    site.refresh_from_db()
    assert site.last_hit_at is not None


def test_secrets_in_the_query_string_are_never_stored(client, site):
    post(client, hit(site, url="https://shop.example/reset?token=abc123&page=2"))
    url = Hit.objects.get().url
    assert "abc123" not in url and "page=2" in url and "token=redacted" in url


def test_a_crawler_is_kept_and_flagged(client, site):
    post(client, hit(site), ua="Mozilla/5.0 (compatible; Googlebot/2.1)")
    assert Hit.objects.get().is_bot is True


def test_a_click_keeps_its_position_and_path_but_never_its_text(client, site):
    post(
        client,
        hit(
            site,
            type="click",
            x=120.6,
            y=-4,
            rel_x=5000,
            rel_y=500,
            viewport_w=1280,
            element_selector="button#buy",
            element_text="Buy now",
            element_href="/checkout",
            page_region="main",
        ),
    )
    row = Hit.objects.get()
    assert (row.x, row.y, row.rel_x, row.rel_y) == (121, 0, 1000, 500)
    assert (row.element_selector, row.page_region) == ("button#buy", "main")
    assert not hasattr(row, "element_text") and not hasattr(row, "element_href")


def test_a_click_without_a_position_is_dropped(client, site):
    post(client, hit(site, type="click"))
    assert Hit.objects.count() == 0


def test_a_page_leave_records_depth_and_time(client, site):
    post(client, hit(site, type="pageleave", scroll_depth_pct=140, active_seconds=37))
    row = Hit.objects.get()
    assert (row.scroll_depth_pct, row.active_seconds) == (100, 37)


def test_a_page_leave_without_a_depth_is_dropped(client, site):
    post(client, hit(site, type="pageleave"))
    assert Hit.objects.count() == 0


@pytest.mark.parametrize(
    "overrides",
    [{"type": "heartbeat"}, {"type": "made-up"}],
)
def test_an_unknown_type_is_dropped_not_counted_as_a_page_view(client, site, overrides):
    post(client, hit(site, **overrides))
    assert Hit.objects.count() == 0


def test_a_missing_type_is_a_page_view(client, site):
    payload = hit(site)
    del payload["type"]
    post(client, payload)
    assert Hit.objects.get().hit_type == "pageview"


def test_junk_ids_and_sequences_are_blanked_not_trusted(client, site):
    post(client, hit(site, session_id="../../etc", visitor_id="café", seq="soon"))
    row = Hit.objects.get()
    assert (row.session_id, row.visitor_id, row.sequence) == ("", "", None)


@pytest.mark.parametrize(
    "raw",
    [
        b"not json",
        b"[1,2]",
        b'{"key": "nope"}',
        b'{"key": "AAAAAAAAAAAAAAAAAAAAAA"}',
        b"x" * 20_000,
    ],
)
def test_junk_and_unknown_keys_answer_204_and_write_nothing(client, site, raw):
    assert post(client, None, raw=raw).status_code == 204
    assert Hit.objects.count() == 0


def test_an_inactive_site_collects_nothing(client, site):
    Site.objects.filter(pk=site.pk).update(is_active=False)
    post(client, hit(site))
    assert Hit.objects.count() == 0


@pytest.mark.parametrize(
    "origin",
    [
        "https://shop.example",
        "https://www.shop.example",
        "https://eu.shop.example",
        "https://shop.example:8443",
    ],
)
def test_the_sites_domain_and_subdomains_are_accepted(client, site, origin):
    post(client, hit(site), origin=origin)
    assert Hit.objects.count() == 1


@pytest.mark.parametrize(
    "origin",
    [
        "https://other.example",
        "https://shop.example.other.example",
        "http://localhost:3000",
        None,
    ],
)
def test_reports_from_anywhere_else_are_refused_and_counted(client, site, origin):
    post(client, hit(site), origin=origin)
    assert Hit.objects.count() == 0
    site.refresh_from_db()
    assert site.rejected_hits == 1 and site.last_hit_at is None


def test_the_payload_cannot_vouch_for_its_own_host(client, site):
    post(client, hit(site, url="https://shop.example/"), origin="https://other.example")
    site.refresh_from_db()
    assert Hit.objects.count() == 0 and site.last_rejected_host == "other.example"


def test_the_referer_is_used_when_there_is_no_origin(client, site):
    client.post(
        COLLECT,
        data=json.dumps(hit(site)),
        content_type="text/plain",
        HTTP_REFERER="https://shop.example/pricing",
        HTTP_USER_AGENT=UA_CHROME,
    )
    assert Hit.objects.count() == 1


def test_an_extra_host_can_be_allowed(client, site):
    Site.objects.filter(pk=site.pk).update(allowed_hosts=["localhost"])
    post(client, hit(site), origin="http://localhost:3000")
    assert Hit.objects.count() == 1


def test_any_origin_gets_cors_and_a_preflight_answer(client, site):
    response = post(client, hit(site), origin="https://other.example")
    assert response["Access-Control-Allow-Origin"] == "*"
    assert "Access-Control-Allow-Credentials" not in response
    preflight = client.options(COLLECT, HTTP_ORIGIN="https://anything.example")
    assert preflight.status_code == 204
    assert "Content-Encoding" in preflight["Access-Control-Allow-Headers"]


def test_get_is_not_allowed(client):
    assert client.get(COLLECT).status_code == 405


# --- the tracker and recorder scripts ------------------------------------------------


def test_the_tracker_is_served_cacheable_with_the_collector_url(client, site):
    response = client.get(f"/oc/t/{site.public_key}.js")
    body = response.content.decode()
    assert response.status_code == 200
    assert response["Content-Type"].startswith("application/javascript")
    assert response["Cache-Control"] == "public, max-age=300"
    assert response["Access-Control-Allow-Origin"] == "*"
    assert f'"{site.public_key}"' in body and '"http://testserver/oc/e/"' in body
    assert "var RECORD=null;" in body


def test_the_tracker_carries_recording_settings_when_on(client, site, recording_on):
    body = client.get(f"/oc/t/{site.public_key}.js").content.decode()
    assert '"endpoint":"http://testserver/oc/r/"' in body
    assert '"recorder_url":"http://testserver/oc/rec/' in body
    assert '"mask_mode":"balanced"' in body and '"sample_pct":100' in body


def test_a_settings_change_reaches_the_next_tracker(client, site, recording_on):
    client.get(f"/oc/t/{site.public_key}.js")  # cached now
    recording_on.enabled = False
    recording_on.save()
    assert "var RECORD=null;" in client.get(f"/oc/t/{site.public_key}.js").content.decode()


def test_the_public_base_url_is_used_when_set(client, site, settings):
    settings.OSS_CLARITY = {"PUBLIC_BASE_URL": "https://collect.example.net"}
    body = client.get(f"/oc/t/{site.public_key}.js").content.decode()
    assert '"https://collect.example.net/oc/e/"' in body


def test_an_unknown_key_gets_an_inert_script_not_a_404(client):
    response = client.get("/oc/t/AAAAAAAAAAAAAAAAAAAAAA.js")
    assert response.status_code == 200 and b"unknown or inactive" in response.content


def test_malformed_keys_and_digests_do_not_route():
    from django.urls import Resolver404, resolve

    for path in ("/oc/t/short.js", "/oc/t/AAAAAAAAAAAAAAAAAAAAAA!.js", "/oc/rec/XYZ.js"):
        with pytest.raises(Resolver404):
            resolve(path)
    assert resolve("/oc/t/AAAAAAAAAAAAAAAAAAAAAA.js").url_name == "tracker"
    assert resolve("/oc/rec/0123456789abcdef.js").url_name == "recorder"


def test_the_recorder_is_served_immutable_and_a_stale_digest_is_inert(client):
    from oss_clarity.tracker import recorder_source

    digest, source = recorder_source()
    assert "__ossClarityRecorder" in source
    response = client.get(reverse("oss_clarity_public:recorder", args=[digest]))
    assert response.status_code == 200 and "immutable" in response["Cache-Control"]
    stale = client.get(reverse("oss_clarity_public:recorder", args=["0" * 16]))
    assert stale.status_code == 200 and b"superseded" in stale.content


def test_the_middleware_restores_headers_something_else_overwrote(client, site, settings):
    settings.MIDDLEWARE = [
        "oss_clarity.middleware.PublicEndpointsMiddleware",
        "tests.django.middleware.NoStoreEverything",
        *settings.MIDDLEWARE[1:],
    ]
    response = client.get(f"/oc/t/{site.public_key}.js")
    assert response["Cache-Control"] == "public, max-age=300"
    assert "Pragma" not in response
    assert client.get("/admin/login/")["Cache-Control"] == "no-store"


# --- page_path and scrub_url --------------------------------------------------------


@pytest.mark.parametrize(
    "url, path",
    [
        ("https://shop.example/pricing?x=1#faq", "/pricing"),
        ("https://shop.example/pricing/", "/pricing"),
        ("https://shop.example/", "/"),
        ("https://shop.example", "/"),
        ("", "/"),
        ("pricing", "/pricing"),
        ("https://shop.example/About", "/About"),
        ("https://shop.example/caf%C3%A9", "/caf%C3%A9"),
        ("https://shop.example/" + "a" * 600, "/" + "a" * 511),
    ],
)
def test_page_path(url, path):
    assert page_path(url) == path


def test_scrub_url_keeps_harmless_parameters():
    scrubbed = scrub_url("https://shop.example/?page=2&access_token=abc&utm_source=x&code=9")
    assert "page=2" in scrubbed and "utm_source=x" in scrubbed
    assert "abc" not in scrubbed and "code=9" not in scrubbed
