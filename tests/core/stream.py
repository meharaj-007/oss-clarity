"""Builders for synthetic rrweb event streams.

Every page, id and text here is invented for the tests.
"""

from __future__ import annotations

LONG_TEXT = "Our workshop repairs and services bicycles of every size and age. " * 3


def el(node_id, tag, children=(), **attrs):
    return {
        "type": 2,
        "tagName": tag,
        "id": node_id,
        "attributes": attrs,
        "childNodes": list(children),
    }


def text(node_id, value):
    return {"type": 3, "id": node_id, "textContent": value}


def document(*body_children):
    return {"type": 0, "id": 1, "childNodes": [el(2, "html", [el(3, "body", body_children)])]}


def snapshot(node, ts=1_000):
    return {
        "type": 2,
        "timestamp": ts,
        "data": {"node": node, "initialOffset": {"top": 0, "left": 0}},
    }


def shop_page(extra=(), ts=1_000):
    """A small shop page: a link, a price, a paragraph, a phone number, an
    order form, a toolbar button and a footer of short links."""
    return snapshot(
        document(
            el(10, "a", [text(11, "Book a ride")], href="/book"),
            el(20, "span", [text(21, "$▫▫")], **{"class": "cost"}),
            el(30, "p", [text(31, LONG_TEXT)]),
            el(40, "span", [text(41, "Call ▫▫▫▫ ▫▫▫ ▫▫▫")], **{"class": "hotline"}),
            el(
                50,
                "form",
                [
                    el(51, "input", type="text", name="fullname"),
                    el(52, "input", type="tel", name="mobile"),
                    el(53, "input", type="email", name="mail"),
                    el(54, "button", [text(55, "Order")], id="submit-order"),
                ],
            ),
            el(
                60,
                "div",
                [el(61, "button", [text(62, "Menu")], type="button")],
                **{"class": "toolbar"},
            ),
            el(
                70,
                "footer",
                [el(71, "span", [text(72, "Terms")]), el(73, "span", [text(74, "Privacy")])],
            ),
            *extra,
        ),
        ts,
    )


def move(ts, node, x, y):
    return {
        "type": 3,
        "timestamp": ts,
        "data": {"source": 1, "positions": [{"x": x, "y": y, "id": node, "timeOffset": 0}]},
    }


def click(ts, node, x=100, y=100):
    return {
        "type": 3,
        "timestamp": ts,
        "data": {"source": 2, "type": 2, "id": node, "x": x, "y": y},
    }


def focus(ts, node, focused=True):
    return {
        "type": 3,
        "timestamp": ts,
        "data": {"source": 2, "type": 5 if focused else 6, "id": node},
    }


def typed(ts, node, length, **flags):
    return {
        "type": 3,
        "timestamp": ts,
        "data": {"source": 5, "id": node, "text": "*" * length, "isChecked": False, **flags},
    }


def scroll(ts, y, node=1):
    return {"type": 3, "timestamp": ts, "data": {"source": 3, "id": node, "x": 0, "y": y}}


def mutation(ts):
    return {
        "type": 3,
        "timestamp": ts,
        "data": {"source": 0, "adds": [], "removes": [], "texts": [], "attributes": []},
    }


def select(ts, node, start, end):
    return {
        "type": 3,
        "timestamp": ts,
        "data": {
            "source": 14,
            "ranges": [{"start": node, "startOffset": start, "end": node, "endOffset": end}],
        },
    }


def custom(ts, tag, payload=None):
    return {"type": 5, "timestamp": ts, "data": {"tag": tag, "payload": payload or {}}}


#: A later page, so the page under test is not the one the visit ended on.
ELSEWHERE = [click(90_000, 10), mutation(90_100)]


def pages(*event_lists):
    """`[(page_id, events)]`, each page opening with the shop snapshot one
    millisecond before its first event."""
    result = []
    for index, events in enumerate(event_lists):
        start = events[0]["timestamp"] - 1 if events else 1_000
        result.append((f"p-{index}", [shop_page(ts=start), *events]))
    return result
