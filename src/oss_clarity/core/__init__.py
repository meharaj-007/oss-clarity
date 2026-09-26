"""Rules-based analysis of rrweb recordings and page views, with no Django.

Nothing under this package imports Django or any third-party library.
"""

from . import navigation
from .measure import LoadedRecording, measure
from .mirror import Mirror
from .signals import MAX_MARKERS, SIGNAL_KINDS, analyze, looks_like_contact
from .thresholds import Thresholds
from .useragent import is_bot_user_agent, parse_user_agent

__all__ = [
    "MAX_MARKERS",
    "SIGNAL_KINDS",
    "LoadedRecording",
    "Mirror",
    "Thresholds",
    "analyze",
    "is_bot_user_agent",
    "looks_like_contact",
    "measure",
    "navigation",
    "parse_user_agent",
]
