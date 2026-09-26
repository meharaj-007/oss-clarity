"""The collector and the scripts: called by visitors' browsers on other sites.

    path("oc/", include("oss_clarity.urls.public"))

Each route kind has its own fixed segment, and the converters enforce the key
and digest formats, so no value can match another route.
"""

from __future__ import annotations

from django.urls import path, register_converter

from ..views import public
from .converters import DigestConverter, PublicKeyConverter

register_converter(PublicKeyConverter, "oc_key")
register_converter(DigestConverter, "oc_digest")

app_name = "oss_clarity_public"

urlpatterns = [
    path("t/<oc_key:key>.js", public.tracker_script, name="tracker"),
    path("rec/<oc_digest:digest>.js", public.recorder_script, name="recorder"),
    path("e/", public.collect, name="collect"),
    path("r/", public.record, name="record"),
]
