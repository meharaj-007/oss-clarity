"""The parts of rrweb's event format the analysis reads (see `@rrweb/types`).

Only the numbers used here are listed. rrweb events are plain JSON, so a
recording can be read in one pass with nothing but the standard library.
"""

from __future__ import annotations

from enum import IntEnum


class EventType(IntEnum):
    FULL_SNAPSHOT = 2
    INCREMENTAL_SNAPSHOT = 3
    CUSTOM = 5


class IncrementalSource(IntEnum):
    MUTATION = 0
    MOUSE_MOVE = 1
    MOUSE_INTERACTION = 2
    SCROLL = 3
    INPUT = 5
    TOUCH_MOVE = 6
    SELECTION = 14


class MouseInteraction(IntEnum):
    CLICK = 2
    FOCUS = 5
    BLUR = 6
    TOUCH_START = 7


class NodeType(IntEnum):
    DOCUMENT = 0
    ELEMENT = 2
    TEXT = 3


#: Custom event tags the tracker writes into a recording.
PAGEVIEW_TAG = "oss_clarity.pageview"
ERROR_TAG = "oss_clarity.error"
