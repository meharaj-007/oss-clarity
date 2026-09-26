"""Delete everything past its retention window now. The same as
`oss_clarity_run_jobs --only prune --force`."""

from __future__ import annotations

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Delete hits, recordings and heatmap rows past their retention window."

    def handle(self, *args, **options):
        call_command("oss_clarity_run_jobs", only="prune", force=True, stdout=self.stdout)
