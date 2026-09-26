"""A coarse user-agent classifier, and the one bot test the collector uses.

Deliberately coarse: the columns answer "mostly phones or mostly desktops,
which browsers matter", not "who is this". Nothing here fingerprints.
"""

from __future__ import annotations

import re

#: Crawlers by name. Most say "bot", but several that run JavaScript do not
#: (Google's newer fetchers such as `GoogleOther` and `Google-InspectionTool`,
#: link previewers, Lighthouse). Crawlers that run JavaScript reach the tracker
#: and would otherwise be recorded.
_BOT_RE = re.compile(
    r"bot|crawl|spider|slurp|headless|curl|wget|python-requests"
    r"|google-|googleother|facebookexternalhit|lighthouse",
    re.IGNORECASE,
)

#: In-app browsers of social apps, some of which put their own name first
#: instead of "Mozilla/". They are people, often arriving from an ad.
_IN_APP_RE = re.compile(
    r"FBAN|FBAV|Instagram|Snapchat|Pinterest|WhatsApp|Line/|musical_ly|BytedanceWebview|Twitter"
)

# Ordered: the first match wins, because browsers claim each other's names
# (Edge says Chrome, Chrome says Safari).
_BROWSERS = (
    ("Bot", _BOT_RE),
    ("Edge", re.compile(r"Edg(?:e|A|iOS)?/")),
    ("Opera", re.compile(r"OPR/|Opera")),
    ("Samsung Internet", re.compile(r"SamsungBrowser/")),
    ("Chrome", re.compile(r"Chrome/|CriOS/|Chromium/")),
    ("Firefox", re.compile(r"Firefox/|FxiOS/")),
    ("Safari", re.compile(r"Safari/")),
    ("IE", re.compile(r"MSIE |Trident/")),
)

_OPERATING_SYSTEMS = (
    ("Android", re.compile(r"Android")),
    ("iOS", re.compile(r"iPhone|iPad|iPod")),
    ("Windows", re.compile(r"Windows NT|Windows Phone")),
    ("Mac OS X", re.compile(r"Mac OS X|Macintosh")),
    ("Chrome OS", re.compile(r"CrOS")),
    ("Linux", re.compile(r"Linux|X11")),
)

_TABLET_RE = re.compile(r"iPad|Tablet|Android(?!.*Mobile)", re.IGNORECASE)
_MOBILE_RE = re.compile(r"Mobi|iPhone|iPod|Android|Windows Phone", re.IGNORECASE)


def parse_user_agent(user_agent: str | None) -> dict[str, str]:
    """`{device, browser, os}`. Device is Desktop, Mobile, Tablet, Bot or
    Unknown (an empty user agent)."""
    if not user_agent:
        return {"device": "Unknown", "browser": "Unknown", "os": "Unknown"}

    browser = next((name for name, pattern in _BROWSERS if pattern.search(user_agent)), "Other")
    # Every browser that runs a page's JavaScript sends "Mozilla/" first. A
    # user agent without it is a script, whatever else it claims, unless it
    # is a social app's in-app browser.
    if not user_agent.startswith("Mozilla/") and not _IN_APP_RE.search(user_agent):
        browser = "Bot"
    operating_system = next(
        (name for name, pattern in _OPERATING_SYSTEMS if pattern.search(user_agent)), "Other"
    )

    if browser == "Bot":
        device = "Bot"
    elif _TABLET_RE.search(user_agent):
        device = "Tablet"
    elif _MOBILE_RE.search(user_agent):
        device = "Mobile"
    else:
        device = "Desktop"

    return {"device": device, "browser": browser, "os": operating_system}


def is_bot_user_agent(user_agent: str | None) -> bool:
    """The collector's single bot test. Page views and recording chunks both
    ask it, so a crawler is refused by both or by neither. An empty user
    agent is not a bot here; it is "Unknown"."""
    parsed = parse_user_agent(user_agent)
    return parsed["device"] == "Bot" or parsed["browser"] == "Bot"
