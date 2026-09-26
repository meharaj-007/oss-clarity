from __future__ import annotations

from dataclasses import replace

import pytest

from oss_clarity.core import LoadedRecording, Thresholds, measure

from .stream import click, move, mutation, shop_page


def rest(ms):
    """A page where the pointer rests on the price for `ms`, then a later
    page so it is not the last."""
    return [
        ("p-0", [shop_page(ts=1_000), move(2_000, 21, 300, 200), move(2_000 + ms, 21, 301, 200)]),
        ("p-1", [shop_page(ts=89_000), click(90_000, 10), mutation(90_100)]),
    ]


def recordings():
    return [
        LoadedRecording(id="short", site="shop.example", pages=rest(2_500)),
        LoadedRecording(id="long", site="shop.example", pages=rest(4_000)),
    ]


def test_a_sweep_counts_what_each_value_would_find():
    report = measure(recordings())

    assert report["recordings"] == 2 and report["pages"] == 4
    assert report["sites"] == {"shop.example": 2}
    assert report["gate"]["recordings"] == {"have": 2, "need": 200, "met": False}
    assert report["gate"]["sites"] == {"have": 1, "need": 3, "met": False}
    assert report["baseline"]["hesitation"] == {
        "total": 1,
        "recordings": 1,
        "share": 0.5,
        "per_100_pages": 25.0,
    }
    rows = {r["value"]: r for r in report["sweeps"]["hesitation_min_ms"]["rows"]}
    assert rows[2_000]["signals"]["hesitation"]["total"] == 2
    assert rows[3_000]["current"] and rows[3_000]["signals"]["hesitation"]["total"] == 1
    assert rows[5_000]["signals"]["hesitation"]["total"] == 0
    assert report["samples"]["hesitation"][0]["recording"] == "long"
    assert report["samples"]["hesitation"][0]["site"] == "shop.example"


def test_the_sweep_is_around_the_thresholds_given():
    base = replace(Thresholds(), hesitation_min_ms=2_000)
    report = measure(
        recordings(), thresholds=base, sweeps={"hesitation_min_ms": (("hesitation",), (5_000,))}
    )
    rows = {r["value"]: r for r in report["sweeps"]["hesitation_min_ms"]["rows"]}
    assert set(rows) == {2_000, 5_000}
    assert rows[2_000]["current"] and rows[2_000]["signals"]["hesitation"]["total"] == 2
    assert report["baseline"]["hesitation"]["total"] == 2
    assert report["thresholds"]["hesitation_min_ms"] == 2_000


def test_the_quick_back_window_is_swept_too():
    item = LoadedRecording(
        id="s",
        site="shop.example",
        pages=rest(0),
        page_views=[("/", 0.0), ("/pricing", 10.0), ("/", 18.0)],  # 8 s on /pricing
    )
    report = measure([item], sweeps={})
    rows = {
        r["value"]: r["signals"]["quick_back"]["total"]
        for r in report["sweeps"]["quick_back_seconds"]["rows"]
    }
    assert rows == {3: 0, 5: 0, 10: 1, 15: 1}
    assert list(report["sweeps"]) == ["quick_back_seconds"]


def test_the_gate_is_a_parameter():
    report = measure(
        recordings(), sweeps={}, quick_back_sweep=None, gate_recordings=2, gate_sites=1
    )
    assert report["gate"]["recordings"]["met"] and report["gate"]["sites"]["met"]
    assert report["sweeps"] == {}


def test_an_unknown_threshold_cannot_be_swept():
    with pytest.raises(ValueError, match="hesitation_min"):
        measure(recordings(), sweeps={"hesitation_min": (("hesitation",), (1,))})


def test_nothing_is_changed_by_measuring():
    items = recordings()
    before = repr(items)
    measure(items)
    assert repr(items) == before
    assert Thresholds().hesitation_min_ms == 3_000


def test_an_empty_set_reports_zeros():
    report = measure([], sweeps={}, quick_back_sweep=None, failed=3)
    assert report["recordings"] == 0 and report["failed"] == 3
    assert report["baseline"]["rage"] == {
        "total": 0,
        "recordings": 0,
        "share": 0.0,
        "per_100_pages": 0.0,
    }
