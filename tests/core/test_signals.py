"""Each rule gets a stream that must fire it and at least one close call that
must not, because a signal that fires on ordinary browsing is noise."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from oss_clarity.core import MAX_MARKERS, SIGNAL_KINDS, Mirror, Thresholds, analyze
from oss_clarity.core.signals import looks_like_contact

from .stream import (
    ELSEWHERE,
    click,
    custom,
    el,
    focus,
    move,
    mutation,
    pages,
    scroll,
    select,
    shop_page,
    snapshot,
    text,
    typed,
)
from .stream import document as doc


def run(*event_lists, **kwargs):
    counts, markers = analyze(pages(*event_lists), **kwargs)
    return counts, markers, {m["kind"]: m for m in markers}


# --- frustration ----------------------------------------------------------------


def _widget_page(ts=1_000):
    return snapshot(
        doc(
            el(10, "button", id="buy"),
            el(11, "div", **{"class": "panel wide"}),
            el(12, "input", type="text"),
        ),
        ts,
    )


def _clicks_stream(error_tag="oss_clarity.error", pageview_tag="oss_clarity.pageview"):
    return [
        _widget_page(1_000),
        # Three clicks on one spot inside a second: rage. Nothing reacts: dead too.
        click(2_000, 10),
        click(2_300, 10, x=105),
        click(2_600, 10, x=110),
        # Answered by a mutation: fine.
        click(5_000, 11),
        mutation(5_200),
        # On an input: never dead.
        click(7_000, 12),
        # Followed by a script error: an error click, not a dead one.
        click(9_000, 11),
        custom(9_400, error_tag, {"message": "boom"}),
        # Answered by a route change.
        click(12_000, 11),
        custom(12_100, pageview_tag, {"url": "/next"}),
    ]


def test_rage_dead_and_error_clicks_are_found():
    counts, markers = analyze([("p-1", _clicks_stream())])
    assert counts["rage"] == 3
    assert counts["dead"] == 3
    assert counts["error"] == 1
    assert counts["script_error"] == 1
    assert len(markers) == 7
    kinds = {(m["kind"], m["t_ms"]) for m in markers}
    assert ("rage", 1_000) in kinds and ("dead", 1_000) in kinds
    assert ("error", 8_000) in kinds
    assert not kinds & {("dead", 4_000), ("dead", 6_000), ("dead", 11_000)}
    assert next(m for m in markers if m["kind"] == "error")["selector"] == "div.panel.wide"


def test_other_tag_names_are_read_only_when_asked_for():
    stream = _clicks_stream(error_tag="legacy.error", pageview_tag="legacy.pageview")
    counts, _ = analyze([("p-1", stream)])
    # Unrecognised: the error is not counted and the route change is not a reaction.
    assert counts["script_error"] == 0 and counts["error"] == 0 and counts["dead"] == 5

    counts, _ = analyze(
        [("p-1", stream)], pageview_tags=("legacy.pageview",), error_tags=("legacy.error",)
    )
    assert (counts["script_error"], counts["error"], counts["dead"]) == (1, 1, 3)


def test_every_kind_is_counted_and_markers_have_one_shape():
    counts, markers = analyze([("p-1", _clicks_stream())])
    assert tuple(counts) == SIGNAL_KINDS
    for marker in markers:
        assert set(marker) == {"t_ms", "page_id", "kind", "selector", "label", "x", "y"}


def test_markers_are_capped_but_counters_are_not():
    events = [shop_page()]
    # Clicks far apart in space and time: each is one dead click.
    for i in range(MAX_MARKERS + 50):
        events.append(click(2_000 + i * 5_000, 30, x=(i % 10) * 200, y=i * 10))
    counts, markers = analyze([("p-0", events)])
    assert counts["dead"] == MAX_MARKERS + 50
    assert len(markers) == MAX_MARKERS
    assert markers == sorted(markers, key=lambda m: m["t_ms"])


# --- hesitation -------------------------------------------------------------------


def test_a_rest_on_a_price_or_link_is_hesitation_but_reading_a_paragraph_is_not():
    events = [
        move(2_000, 21, 300, 200),
        move(6_000, 21, 305, 202),  # 4 s on the price
        move(7_000, 11, 50, 50),
        move(11_000, 11, 52, 50),  # 4 s on the link text
        move(12_000, 31, 400, 400),
        move(20_000, 31, 401, 400),  # 8 s on a paragraph
        move(20_500, 70, 9, 900),
        move(24_500, 70, 9, 900),  # 4 s on the footer block
        move(21_000, 62, 10, 10),
        move(25_000, 62, 10, 10),  # 4 s on Menu, then...
        click(25_500, 61, 10, 10),
        mutation(25_600),  # ...pressed it
    ]
    counts, markers, _ = run(events, ELSEWHERE)
    assert counts["hesitation"] == 2
    labels = {m["label"] for m in markers if m["kind"] == "hesitation"}
    # The paragraph is reading, the footer is a block of links rather than one
    # thing, and Menu was pressed, so it was decided.
    assert labels == {"$▫▫", "Book a ride"}


def test_a_rest_interrupted_by_scrolling_is_not_hesitation():
    events = [move(2_000, 21, 300, 200), scroll(3_000, 400), move(6_000, 21, 300, 200)]
    counts, _, _ = run(events, ELSEWHERE)
    assert counts["hesitation"] == 0


# --- near miss ----------------------------------------------------------------------


def test_an_unanswered_click_corrected_next_door_is_a_near_miss():
    events = [
        click(2_000, 60, 100, 100),  # the toolbar itself: nothing happens
        click(2_800, 62, 118, 104),
        mutation(2_900),  # Menu right beside it: it opens
    ]
    counts, _, marks = run(events, ELSEWHERE)
    # Not a dead click: Menu's reaction lands inside its window, which is why
    # a near miss is not "a dead click, then".
    assert counts["near_miss"] == 1 and counts["dead"] == 0
    assert marks["near_miss"]["label"] == "Menu"
    assert marks["near_miss"]["selector"] == "button"


def test_a_far_away_second_click_is_not_a_near_miss():
    events = [click(2_000, 60, 100, 100), click(2_800, 62, 500, 400), mutation(2_900)]
    counts, _, _ = run(events, ELSEWHERE)
    assert counts["near_miss"] == 0


# --- scroll hunt --------------------------------------------------------------------


def test_scrolling_up_and_down_without_doing_anything_is_a_hunt():
    ys = [0, 900, 100, 1_000, 200, 1_100]
    events = [scroll(2_000 + i * 700, y) for i, y in enumerate(ys)]
    counts, _, marks = run(events, ELSEWHERE)
    assert counts["scroll_hunt"] == 1
    assert marks["scroll_hunt"]["selector"] == ""


def test_reading_down_a_page_is_not_a_hunt():
    events = [scroll(2_000 + i * 700, i * 400) for i in range(10)]
    counts, _, _ = run(events, ELSEWHERE)
    assert counts["scroll_hunt"] == 0


def test_a_click_between_the_reversals_resets_the_hunt():
    events = [
        scroll(2_000, 0),
        scroll(2_700, 900),
        scroll(3_400, 100),
        click(3_500, 10),
        mutation(3_600),
        scroll(4_100, 1_000),
        scroll(4_800, 200),
        scroll(5_500, 1_100),
    ]
    counts, _, _ = run(events, ELSEWHERE)
    assert counts["scroll_hunt"] == 0


# --- forms --------------------------------------------------------------------------


def test_form_skip_refill_and_abandon():
    events = [
        focus(2_000, 51),
        typed(2_500, 51, 8),
        focus(3_000, 51, False),
        # Mobile: focused, left empty, and the email was typed afterwards.
        focus(3_500, 52),
        focus(4_000, 52, False),
        # Email: typed, cleared, typed again.
        focus(4_500, 53),
        typed(5_000, 53, 12),
        typed(6_000, 53, 0),
        typed(8_000, 53, 14),
        # And the visit ends without Order being pressed.
    ]
    counts, _, marks = run(events)
    assert counts["form_skip"] == 1 and marks["form_skip"]["selector"] == "input"
    assert marks["form_skip"]["label"] == "mobile"  # its name, having nothing else
    assert counts["form_refill"] == 1
    assert counts["form_abandon"] == 1
    assert marks["form_abandon"]["t_ms"] == 8_000 - 1_999  # from the snapshot


def test_a_sent_form_is_not_abandoned():
    events = [focus(2_000, 51), typed(2_500, 51, 8), click(3_000, 55), mutation(3_100)]
    counts, _, _ = run(events)
    assert counts["form_abandon"] == 0


def test_a_form_left_for_another_page_is_not_abandoned():
    """Enter-to-submit navigates away and looks like following a link, so
    only the page the visit ended on can abandon."""
    counts, _, _ = run([focus(2_000, 51), typed(2_500, 51, 8)], ELSEWHERE)
    assert counts["form_abandon"] == 0


def test_a_field_a_script_filled_is_not_typing():
    # No focus and no click: a script set it.
    counts, _, _ = run([typed(2_500, 51, 8)])
    assert counts["form_abandon"] == 0
    # Focused first: a person.
    counts, _, _ = run([focus(2_000, 51), typed(2_500, 51, 8)])
    assert counts["form_abandon"] == 1


def test_the_recorders_own_answer_wins_over_focus():
    counts, _, _ = run([focus(2_000, 51), typed(2_500, 51, 8, userTriggered=False)])
    assert counts["form_abandon"] == 0
    counts, _, _ = run([typed(2_500, 51, 8, userTriggered=True)])
    assert counts["form_abandon"] == 1


# --- repeat submit ------------------------------------------------------------------


def test_pressing_submit_again_after_a_pause_is_a_repeat_submit():
    counts, _, marks = run([click(2_000, 55), click(6_000, 55)], ELSEWHERE)
    assert counts["repeat_submit"] == 1
    assert marks["repeat_submit"]["selector"] == "button#submit-order"
    assert marks["repeat_submit"]["label"] == "Order"


def test_a_type_button_is_not_a_submit():
    counts, _, _ = run([click(2_000, 61), click(6_000, 61)], ELSEWHERE)
    assert counts["repeat_submit"] == 0


# --- copy-out -----------------------------------------------------------------------


def test_selecting_the_phone_number_is_a_copy_out_but_selecting_prose_is_not():
    events = [select(2_000, 31, 0, 30), select(3_000, 41, 5, 17), select(3_200, 41, 5, 17)]
    counts, _, marks = run(events, ELSEWHERE)
    assert counts["copy_out"] == 1
    assert marks["copy_out"]["label"] == "Call ▫▫▫▫ ▫▫▫ ▫▫▫"


@pytest.mark.parametrize(
    "value,expected",
    [
        ("▫▫▫▫ ▫▫▫ ▫▫▫", True),
        ("+1 555 010 0199", True),
        ("sam@example.org", True),
        ("▪▪▪▪@▪▪▪▪▪▪▪.▪▪▪", True),
        ("▫▫ Harbour Street", True),
        ("7 Elm Rd", True),
        ("$▫▫", False),
        ("we fix bikes", False),
        ("2031", False),
    ],
)
def test_contact_shapes(value, expected):
    assert looks_like_contact(value) is expected


# --- idle exit ----------------------------------------------------------------------


def test_a_visit_that_ends_on_an_untouched_page_is_an_idle_exit():
    counts, _, marks = run(
        [click(2_000, 10), mutation(2_100)], [move(40_000, 31, 1, 1), move(65_000, 31, 2, 2)]
    )
    assert counts["idle_exit"] == 1
    assert marks["idle_exit"]["page_id"] == "p-1"


def test_a_short_last_page_is_not_an_idle_exit():
    counts, _, _ = run([move(2_000, 31, 1, 1), move(10_000, 31, 1, 1)])
    assert counts["idle_exit"] == 0


# --- consent banners ----------------------------------------------------------------


def test_a_cookie_banner_is_not_the_site():
    banner = el(
        900,
        "div",
        [
            el(
                901,
                "div",
                [el(902, "p", [text(903, "We use cookies")])],
                **{"class": "rounded p-4"},
            ),
            el(904, "span", [text(905, "Necessary only")]),
        ],
        id="cookie-consent",
        role="dialog",
    )
    events = [
        shop_page([banner]),
        click(2_000, 901, 50, 700),  # dead, in the banner
        move(3_000, 905, 60, 720),
        move(7_000, 905, 61, 720),  # rests on its text
        click(9_000, 20, 300, 200),  # dead, on the page
    ]
    counts, markers = analyze([("p-0", events), ("p-1", [shop_page(ts=89_000), *ELSEWHERE])])
    assert counts["dead"] == 1 and counts["hesitation"] == 0
    assert [m["selector"] for m in markers if m["kind"] == "dead"] == ["span.cost"]


# --- field labels -------------------------------------------------------------------


def test_a_field_is_named_by_what_the_visitor_saw():
    mirror = Mirror()
    mirror.load_snapshot(
        shop_page(
            [
                el(800, "label", [text(801, "Email address")], **{"for": "contact-email"}),
                el(802, "input", id="contact-email", name="fld[7]"),
                el(810, "label", [text(811, "Postcode "), el(812, "input", name="f2")]),
                el(820, "div", [el(821, "p", [text(822, "Mobile")]), el(823, "input", name="q9")]),
                el(830, "input", name="only_a_name"),
                el(840, "input", name="x", placeholder="Your name"),
                el(850, "span", [text(851, "Delivery note")], id="note-label"),
                el(852, "textarea", **{"aria-labelledby": "note-label"}),
                el(860, "input", name="y", **{"aria-label": "  Promo   code "}),
            ]
        )["data"]["node"]
    )
    assert mirror.label(802) == "Email address"
    assert mirror.label(812) == "Postcode"
    assert mirror.label(823) == "Mobile"
    assert mirror.label(840) == "Your name"
    assert mirror.label(830) == "only_a_name"
    assert mirror.label(852) == "Delivery note"
    assert mirror.label(860) == "Promo code"


def test_text_before_a_field_is_not_taken_out_of_another_fields_block():
    mirror = Mirror()
    mirror.load_snapshot(
        doc(
            el(
                100,
                "form",
                [
                    el(
                        101,
                        "div",
                        [el(102, "p", [text(103, "First name")]), el(104, "input", name="a")],
                    ),
                    el(105, "div", [el(106, "input", name="b")]),
                ],
            )
        )
    )
    # The second field's block holds no text of its own, and the text before
    # it belongs to the first field.
    assert mirror.label(104) == "First name"
    assert mirror.label(106) == "b"


def test_children_are_kept_in_document_order():
    mirror = Mirror()
    mirror.load_snapshot(doc(el(100, "ul", [el(101, "li"), el(102, "li"), el(103, "li")])))
    assert mirror.children[100] == [101, 102, 103]
    mirror.apply_mutation(
        {"adds": [{"parentId": 3, "node": el(200, "div", [el(201, "span"), el(202, "span")])}]}
    )
    assert mirror.children[200] == [201, 202]


def test_an_element_added_after_load_can_be_named():
    events = [
        shop_page(),
        {
            "type": 3,
            "timestamp": 1_500,
            "data": {
                "source": 0,
                "adds": [
                    {"parentId": 3, "node": el(300, "button", [text(301, "Load more")], id="more")}
                ],
                "removes": [],
                "texts": [],
                "attributes": [],
            },
        },
        click(5_000, 300),
    ]
    _, markers = analyze([("p-0", events)])
    dead = next(m for m in markers if m["kind"] == "dead")
    assert (dead["selector"], dead["label"]) == ("button#more", "Load more")


# --- thresholds ---------------------------------------------------------------------


def test_moving_a_threshold_moves_the_result():
    events = [move(2_000, 21, 300, 200), move(4_500, 21, 301, 200)]  # 2.5 s rest
    assert run(events, ELSEWHERE)[0]["hesitation"] == 0
    looser = replace(Thresholds(), hesitation_min_ms=2_000)
    assert run(events, ELSEWHERE, thresholds=looser)[0]["hesitation"] == 1


def test_thresholds_are_immutable_and_reject_unknown_names():
    with pytest.raises(AttributeError):
        Thresholds().rage_min_clicks = 9  # type: ignore[misc]
    assert Thresholds.from_mapping({"rage_min_clicks": 4}).rage_min_clicks == 4
    with pytest.raises(ValueError, match="rage_min_click"):
        Thresholds.from_mapping({"rage_min_click": 4})


def test_analyses_with_different_thresholds_can_run_at_once():
    events = [move(2_000, 21, 300, 200), move(4_500, 21, 301, 200)]
    strict, loose = Thresholds(), replace(Thresholds(), hesitation_min_ms=2_000)
    jobs = [strict, loose] * 50
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(lambda t: run(events, ELSEWHERE, thresholds=t)[0]["hesitation"], jobs)
        )
    assert results == [0, 1] * 50


def test_the_same_input_gives_the_same_output_and_is_not_changed():
    stream = pages([click(2_000, 55), click(6_000, 55)], ELSEWHERE)
    before = repr(stream)
    assert analyze(stream) == analyze(stream)
    assert repr(stream) == before


def test_empty_and_malformed_input_is_harmless():
    assert analyze([]) == (dict.fromkeys(SIGNAL_KINDS, 0), [])
    counts, markers = analyze([("p-0", [{}, {"timestamp": "x"}, "junk", {"type": 3}])])
    assert sum(counts.values()) == 0 and markers == []
