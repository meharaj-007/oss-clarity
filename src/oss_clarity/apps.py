from __future__ import annotations

from django.apps import AppConfig


class OssClarityConfig(AppConfig):
    name = "oss_clarity"
    label = "oss_clarity"
    verbose_name = "Session replay and heatmaps"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        from django.db.models.signals import post_delete, post_save

        from . import conf  # noqa: F401  (registers the settings checks)
        from .models import RecordingSettings
        from .record import forget_settings

        post_save.connect(forget_settings, sender=RecordingSettings, dispatch_uid="oc_rs_save")
        post_delete.connect(forget_settings, sender=RecordingSettings, dispatch_uid="oc_rs_del")
