from django.contrib import admin
from django.shortcuts import render
from django.urls import include, path

from oss_clarity.models import Site


def page(request, name="home"):
    site = Site.objects.filter(domain="localhost").first()
    return render(request, "page.html", {"site": site, "name": name})


urlpatterns = [
    path("admin/", admin.site.urls),
    path("oc/", include("oss_clarity.urls.public")),
    path("oc-api/", include("oss_clarity.urls.api")),
    path("", page),
    path("<slug:name>/", page),
]
