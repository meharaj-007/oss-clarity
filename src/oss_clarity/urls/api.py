"""The read-only JSON API, for staff by default (see `API_PERMISSION`).

path("oc-api/", include("oss_clarity.urls.api"))
"""

from __future__ import annotations

from django.urls import path

from ..views import api

app_name = "oss_clarity_api"

urlpatterns = [
    path("sites/<int:site_id>/recordings/", api.recordings, name="recordings"),
    path("recordings/<uuid:recording_id>/", api.recording, name="recording"),
    path(
        "recordings/<uuid:recording_id>/pages/<int:page_seq>/events/",
        api.page_events,
        name="page-events",
    ),
    path("sites/<int:site_id>/heatmaps/", api.heatmap_pages, name="heatmap-pages"),
    path("sites/<int:site_id>/heatmap/", api.heatmap, name="heatmap"),
    path("sites/<int:site_id>/sessions/", api.sessions, name="sessions"),
]
