from __future__ import annotations

import json

import pytest
from django.test import RequestFactory

from oss_clarity.models import Hit, RecordingChunk
from oss_clarity.throttle import client_ip

from .conftest import ORIGIN, UA_CHROME
from .test_record import chunk, events
from .test_record import post as post_chunk

factory = RequestFactory()


def request(remote="10.0.0.1", **meta):
    return factory.get("/", REMOTE_ADDR=remote, **meta)


def resolve_user_ip(request):
    return request.META.get("HTTP_X_TEST_IP")


def test_by_default_the_socket_address_is_used(settings):
    assert client_ip(request(HTTP_X_FORWARDED_FOR="203.0.113.9")) == "10.0.0.1"


def test_trusted_proxies_take_the_address_counting_from_the_right(settings):
    settings.OSS_CLARITY = {"TRUSTED_PROXY_COUNT": 1}
    # The client wrote the first entry itself; the proxy appended the real one.
    forwarded = "198.51.100.66, 203.0.113.9"
    assert client_ip(request(HTTP_X_FORWARDED_FOR=forwarded)) == "203.0.113.9"
    settings.OSS_CLARITY = {"TRUSTED_PROXY_COUNT": 2}
    chain = "198.51.100.66, 203.0.113.9, 10.0.0.2"
    assert client_ip(request(HTTP_X_FORWARDED_FOR=chain)) == "203.0.113.9"


def test_a_chain_shorter_than_the_proxy_count_falls_back(settings):
    settings.OSS_CLARITY = {"TRUSTED_PROXY_COUNT": 3}
    assert client_ip(request(HTTP_X_FORWARDED_FOR="203.0.113.9")) == "10.0.0.1"


def test_a_single_trusted_header_wins_over_the_proxy_count(settings):
    settings.OSS_CLARITY = {"CLIENT_IP_HEADER": "CF-Connecting-IP", "TRUSTED_PROXY_COUNT": 1}
    meta = {"HTTP_CF_CONNECTING_IP": "2001:db8::7", "HTTP_X_FORWARDED_FOR": "203.0.113.9"}
    assert client_ip(request(**meta)) == "2001:db8::7"


def test_a_function_wins_over_everything(settings):
    settings.OSS_CLARITY = {
        "CLIENT_IP_FUNCTION": "tests.django.test_throttle.resolve_user_ip",
        "CLIENT_IP_HEADER": "CF-Connecting-IP",
    }
    assert client_ip(request(HTTP_X_TEST_IP="192.0.2.44")) == "192.0.2.44"


@pytest.mark.parametrize("value", ["not-an-ip", "", "999.1.1.1", "203.0.113.9; drop table"])
def test_an_unusable_value_falls_back_to_the_socket(settings, value):
    settings.OSS_CLARITY = {"CLIENT_IP_HEADER": "X-Real-IP"}
    assert client_ip(request(HTTP_X_REAL_IP=value)) == "10.0.0.1"


def test_ports_and_brackets_are_removed(settings):
    settings.OSS_CLARITY = {"CLIENT_IP_HEADER": "X-Real-IP"}
    assert client_ip(request(HTTP_X_REAL_IP="203.0.113.9:443")) == "203.0.113.9"
    assert client_ip(request(HTTP_X_REAL_IP="[2001:db8::7]:443")) == "2001:db8::7"


# --- the windows ------------------------------------------------------------------------


def send_hit(client, site, ip):
    payload = {"key": site.public_key, "type": "pageview", "url": f"{ORIGIN}/", "session_id": "s"}
    client.post(
        "/oc/e/",
        data=json.dumps(payload),
        content_type="text/plain",
        HTTP_ORIGIN=ORIGIN,
        HTTP_USER_AGENT=UA_CHROME,
        REMOTE_ADDR=ip,
    )


@pytest.mark.django_db
def test_one_address_is_limited_and_the_site_counts_it_once(client, site, settings):
    settings.OSS_CLARITY = {"HIT_RATE_KEY_IP": 2}
    for _ in range(5):
        send_hit(client, site, "203.0.113.9")
    send_hit(client, site, "203.0.113.10")
    assert Hit.objects.count() == 3
    site.refresh_from_db()
    assert (site.throttled_hits, site.last_throttled_scope) == (1, "key_ip")


@pytest.mark.django_db
def test_many_addresses_are_limited_per_key(client, site, settings):
    settings.OSS_CLARITY = {"HIT_RATE_KEY": 3}
    for n in range(5):
        send_hit(client, site, f"203.0.113.{n}")
    assert Hit.objects.count() == 3
    site.refresh_from_db()
    assert site.last_throttled_scope == "key"


@pytest.mark.django_db
def test_zero_disables_a_window(client, site, settings):
    settings.OSS_CLARITY = {"HIT_RATE_KEY_IP": 0, "HIT_RATE_KEY": 0, "HIT_RATE_GLOBAL": 0}
    for _ in range(5):
        send_hit(client, site, "203.0.113.9")
    assert Hit.objects.count() == 5


@pytest.mark.django_db
def test_chunks_have_their_own_windows(client, site, recording_on, settings):
    settings.OSS_CLARITY = {"CHUNK_RATE_KEY_IP": 2}
    for seq in range(4):
        post_chunk(client, chunk(site, chunk_seq=seq, body=events(2, start=1_000 + seq * 1_000)))
    assert RecordingChunk.objects.count() == 2
    site.refresh_from_db()
    assert (site.last_drop_reason, site.dropped_chunks, site.throttled_hits) == ("throttle", 1, 0)


@pytest.mark.django_db
def test_a_broken_cache_fails_open(client, site, monkeypatch):
    from oss_clarity import throttle

    def broken(*args, **kwargs):
        raise ConnectionError("cache down")

    monkeypatch.setattr(throttle.cache, "add", broken)
    send_hit(client, site, "203.0.113.9")
    assert Hit.objects.count() == 1
