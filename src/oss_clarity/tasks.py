"""Celery tasks for the four jobs, defined only when Celery is installed.

Every task goes through `jobs.run_job`, so the intervals and the one-run-at-
a-time claim apply exactly as they do for cron. A suggested beat schedule, in
the host's Celery config:

    from oss_clarity.tasks import BEAT_SCHEDULE
    app.conf.beat_schedule = {**app.conf.beat_schedule, **BEAT_SCHEDULE}

Each entry expires before its next run is due, so a backed-up queue does not
pile up copies of the same job.
"""

from __future__ import annotations

import logging

from .jobs import NotClaimed, run_job

logger = logging.getLogger(__name__)

try:
    from celery import shared_task
except ImportError:  # Celery is optional.
    shared_task = None

#: Seconds between runs, matching the default `JOB_INTERVALS`.
_EVERY = {"finalize": 300, "navigation": 900, "heatmaps": 3600, "prune": 86400}

BEAT_SCHEDULE = {
    f"oss_clarity.{name}": {
        "task": f"oss_clarity.{name}",
        "schedule": seconds,
        "options": {"expires": max(60, seconds - 60)},
    }
    for name, seconds in _EVERY.items()
}


def _run(name: str):
    try:
        return run_job(name)
    except NotClaimed:
        logger.debug("oss_clarity job %s not due", name)
        return None


if shared_task is not None:

    @shared_task(name="oss_clarity.finalize", ignore_result=True)
    def finalize():
        return _run("finalize")

    @shared_task(name="oss_clarity.navigation", ignore_result=True)
    def navigation():
        return _run("navigation")

    @shared_task(name="oss_clarity.heatmaps", ignore_result=True)
    def heatmaps():
        return _run("heatmaps")

    @shared_task(name="oss_clarity.prune", ignore_result=True)
    def prune():
        return _run("prune")
