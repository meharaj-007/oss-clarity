from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("oc/", include("oss_clarity.urls.public")),
    path("oc-api/", include("oss_clarity.urls.api")),
]
