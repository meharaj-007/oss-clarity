from __future__ import annotations

import pytest

from oss_clarity.core import is_bot_user_agent, parse_user_agent

CHROME_WINDOWS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
SAFARI_IPHONE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1"
)
EDGE_MAC = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0"
)
CHROME_ANDROID_TABLET = (
    "Mozilla/5.0 (Linux; Android 14; Tab X1) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
GOOGLEBOT = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
GOOGLE_FETCHER = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko; "
    "Google-InspectionTool/1.0) Chrome/131.0.0.0 Safari/537.36"
)
INSTAGRAM_APP = (
    "Instagram 350.0.0.0.10 (iPhone14,2; iOS 17_4; en_US; en-US; scale=3.00; 1170x2532) "
    "AppleWebKit/420+"
)
FACEBOOK_APP = "[FBAN/FB4A;FBAV/450.0.0.1;]"


@pytest.mark.parametrize(
    "user_agent, expected",
    [
        (CHROME_WINDOWS, ("Desktop", "Chrome", "Windows")),
        (SAFARI_IPHONE, ("Mobile", "Safari", "iOS")),
        (EDGE_MAC, ("Desktop", "Edge", "Mac OS X")),
        (CHROME_ANDROID_TABLET, ("Tablet", "Chrome", "Android")),
        (GOOGLEBOT, ("Bot", "Bot", "Other")),
        # Crawlers that do not say "bot" but run JavaScript.
        (GOOGLE_FETCHER, ("Bot", "Bot", "Linux")),
        ("GoogleOther", ("Bot", "Bot", "Other")),
        # No "Mozilla/" prefix: a script, whatever it says.
        ("pc", ("Bot", "Bot", "Other")),
        ("python-requests/2.32.3", ("Bot", "Bot", "Other")),
        # Social apps' in-app browsers that put their own name first are people.
        (INSTAGRAM_APP, ("Mobile", "Other", "iOS")),
        (FACEBOOK_APP, ("Desktop", "Other", "Other")),
        ("", ("Unknown", "Unknown", "Unknown")),
        (None, ("Unknown", "Unknown", "Unknown")),
    ],
)
def test_parse_user_agent(user_agent, expected):
    parsed = parse_user_agent(user_agent)
    assert (parsed["device"], parsed["browser"], parsed["os"]) == expected


@pytest.mark.parametrize(
    "user_agent, expected",
    [
        (GOOGLEBOT, True),
        (GOOGLE_FETCHER, True),
        ("curl/8.5.0", True),
        ("Mozilla/5.0 (X11; Linux x86_64) HeadlessChrome/131.0.0.0 Safari/537.36", True),
        (CHROME_WINDOWS, False),
        (INSTAGRAM_APP, False),
        (FACEBOOK_APP, False),
        ("", False),
    ],
)
def test_is_bot_user_agent(user_agent, expected):
    assert is_bot_user_agent(user_agent) is expected
