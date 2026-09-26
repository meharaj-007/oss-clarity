from __future__ import annotations

import json
from pathlib import Path

import pytest

from oss_clarity.core.sampling import ID_RE, fnv1a32, session_is_sampled

VECTORS = json.loads(
    (Path(__file__).resolve().parents[1] / "vectors" / "sampling.json").read_text()
)["vectors"]


@pytest.mark.parametrize("vector", VECTORS, ids=lambda v: v["session_id"][:20] or "empty")
def test_the_hash_matches_every_vector(vector):
    assert fnv1a32(vector["session_id"]) == vector["hash"]
    assert fnv1a32(vector["session_id"]) % 100 == vector["bucket"]


def test_the_vectors_cover_both_ends_of_the_range():
    buckets = {v["bucket"] for v in VECTORS}
    assert {0, 99} <= buckets


def test_known_reference_values():
    assert fnv1a32("") == 0x811C9DC5
    assert fnv1a32("a") == 0xE40C292C


def test_zero_records_nothing_and_a_hundred_records_everything():
    for vector in VECTORS:
        assert session_is_sampled(vector["session_id"], 0) is False
        assert session_is_sampled(vector["session_id"], 100) is True
        assert session_is_sampled(vector["session_id"], 250) is True
        assert session_is_sampled(vector["session_id"], -5) is False


def test_a_session_is_in_the_sample_when_its_bucket_is_below_the_percentage():
    for vector in VECTORS:
        bucket = vector["bucket"]
        assert session_is_sampled(vector["session_id"], bucket + 1) is (bucket + 1 <= 100)
        if bucket:
            assert session_is_sampled(vector["session_id"], bucket) is False


def test_sampling_takes_roughly_the_asked_share():
    chosen = sum(session_is_sampled(f"session-{i}", 25) for i in range(4000))
    assert 850 < chosen < 1150


@pytest.mark.parametrize(
    "value, valid",
    [
        ("0b7f5c2e-4d1a-4c2b-9e8f-3a6d5c4b2a10", True),
        ("1767225600000-k3j9x2m1qz", True),
        ("", False),
        ("x" * 65, False),
        ("café", False),
        ("🙂", False),
        ("a b", False),
        ("../etc", False),
    ],
)
def test_only_ascii_ids_are_accepted(value, valid):
    assert bool(ID_RE.match(value)) is valid
