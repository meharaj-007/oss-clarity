from __future__ import annotations

from datetime import UTC, datetime, timedelta

from oss_clarity.core import navigation

START = datetime(2030, 1, 1, tzinfo=UTC)


def at(*paths_and_seconds):
    return [(path, START + timedelta(seconds=s)) for path, s in paths_and_seconds]


def test_a_quick_back_is_a_b_a_with_little_time_on_b():
    result = navigation.count(at(("/", 0), ("/pricing", 10), ("/", 12), ("/about", 60)))
    assert result["quick_backs"] == 1
    assert result["occurrences"] == [{"kind": "quick_back", "a": "/", "b": "/pricing"}]


def test_a_long_read_before_going_back_is_not_a_quick_back():
    assert navigation.count(at(("/", 0), ("/pricing", 10), ("/", 40)))["quick_backs"] == 0


def test_the_quick_back_window_is_a_parameter():
    views = at(("/", 0), ("/pricing", 10), ("/", 18))  # 8 s on /pricing
    assert navigation.count(views)["quick_backs"] == 0
    assert navigation.count(views, quick_back_seconds=10)["quick_backs"] == 1


def test_a_loop_is_counted_without_overlap():
    result = navigation.count(
        at(("/a", 0), ("/b", 30), ("/a", 60), ("/b", 90), ("/a", 120), ("/b", 150))
    )
    assert result["loops"] == 1
    assert {"kind": "loop", "a": "/a", "b": "/b"} in result["occurrences"]


def test_back_and_forth_between_two_pages_mid_visit_is_a_loop():
    result = navigation.count(
        at(
            ("/", 0),
            ("/prices", 10),
            ("/repairs", 20),
            ("/prices", 30),
            ("/repairs", 40),
            ("/contact", 50),
        )
    )
    assert result["loops"] == 1 and result["quick_backs"] == 0


def test_times_may_be_plain_seconds():
    assert navigation.count([("/", 0.0), ("/x", 10.0), ("/", 12.0)])["quick_backs"] == 1


def test_occurrences_are_capped():
    views = []
    for i in range(100):
        views += [("/a", i * 10.0), ("/b", i * 10.0 + 1)]
    result = navigation.count(views)
    assert result["quick_backs"] > navigation.MAX_OCCURRENCES
    assert len(result["occurrences"]) == navigation.MAX_OCCURRENCES


def test_ordered_uses_the_sequence_when_every_view_has_one():
    rows = [("/b", 1, 10.0), ("/a", 0, 11.0), ("/c", 2, 12.0)]  # /a arrived late
    result = navigation.ordered(rows, key=lambda r: (r[1], r[2]))
    assert [r[0] for r in result] == ["/a", "/b", "/c"]


def test_ordered_falls_back_to_time_when_any_view_lacks_one():
    rows = [("/b", 1, 10.0), ("/a", None, 11.0), ("/c", 0, 12.0)]
    result = navigation.ordered(rows, key=lambda r: (r[1], r[2]))
    assert [r[0] for r in result] == ["/b", "/a", "/c"]


def test_ordered_keeps_arrival_order_between_equal_sequences():
    rows = [("/second", 3, 20.0), ("/first", 3, 10.0)]
    result = navigation.ordered(rows, key=lambda r: (r[1], r[2]))
    assert [r[0] for r in result] == ["/first", "/second"]
