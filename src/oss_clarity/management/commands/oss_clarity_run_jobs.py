"""Run whichever background jobs are due. For cron, every five minutes:

*/5 * * * *  cd /srv/app && python manage.py oss_clarity_run_jobs
"""

from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError

from ...jobs import JOBS, interval, run_due
from ...models import JobRun


class Command(BaseCommand):
    help = "Run the oss-clarity background jobs that are due."

    def add_arguments(self, parser):
        parser.add_argument("--only", choices=sorted(JOBS), help="Run this job only.")
        parser.add_argument(
            "--force",
            action="store_true",
            help="Ignore the interval. A job still running is skipped anyway.",
        )
        parser.add_argument("--list", action="store_true", help="Show each job and its last run.")

    def handle(self, *args, only=None, force=False, list=False, **options):
        if list:
            runs = {run.name: run for run in JobRun.objects.all()}
            for name in JOBS:
                run = runs.get(name)
                minutes = int(interval(name).total_seconds() // 60)
                started = "never"
                if run and run.last_started_at:
                    started = run.last_started_at.isoformat()
                error = f"  error: {run.last_error}" if run and run.last_error else ""
                self.stdout.write(
                    f"{name:<11} every {minutes:>5} min  last started {started}{error}"
                )
            return
        results = run_due(only=only, force=force)
        for name, result in results.items():
            self.stdout.write(f"{name}: {json.dumps(result, default=str)}")
        if any(isinstance(r, str) and r.startswith("failed") for r in results.values()):
            raise CommandError("at least one job failed; see the log and `--list`")
