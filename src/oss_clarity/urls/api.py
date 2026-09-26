"""The read-only JSON API, for staff by default.

path("oc-api/", include("oss_clarity.urls.api"))
"""

from __future__ import annotations

app_name = "oss_clarity_api"

urlpatterns: list = []
