"""Optional middleware for hosts whose other middleware gets in the way of the
public endpoints.

The views already answer preflights and set CORS and cache headers. Two
common setups override them before or after the view runs:

* django-cors-headers answers every preflight itself and, for an origin not
  on its list, leaves out `Access-Control-Allow-Origin`. The browser then
  blocks the report and the site silently stops counting.
* Middleware that stamps `Cache-Control: no-store` on every response makes
  every page load download the tracker again.

Put `oss_clarity.middleware.PublicEndpointsMiddleware` first in `MIDDLEWARE`
and it answers preflights for the public routes before anything else, and
restores their headers after everything else.
"""

from __future__ import annotations

from django.http import HttpResponse
from django.urls import Resolver404, resolve

from .views.public import cors

APP_NAME = "oss_clarity_public"


class PublicEndpointsMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not self._is_public(request):
            return self.get_response(request)
        if request.method == "OPTIONS":
            return cors(HttpResponse(status=204))

        response = self.get_response(request)
        # The view's headers, restored if anything below rewrote them.
        view_cache = getattr(response, "_oc_cache_control", None)
        cors(response)
        if view_cache:
            response["Cache-Control"] = view_cache
            response.headers.pop("Pragma", None)
            response.headers.pop("Expires", None)
        return response

    @staticmethod
    def _is_public(request) -> bool:
        try:
            match = resolve(request.path_info)
        except Resolver404:
            return False
        return match.app_name == APP_NAME
