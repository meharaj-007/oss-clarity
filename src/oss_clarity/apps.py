from __future__ import annotations

from django.apps import AppConfig


class OssClarityConfig(AppConfig):
    name = "oss_clarity"
    label = "oss_clarity"
    verbose_name = "Session replay and heatmaps"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        from . import conf  # noqa: F401  (registers the settings checks)
