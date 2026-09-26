"""The background work, as plain functions, and when each is due.

Four jobs, each safe to run any number of times:

    finalize    close quiet recordings and analyse them       every 5 min
    navigation  store quick backs and loops for recent visits every 15 min
    heatmaps    rebuild today's and yesterday's buckets      every hour
    prune       delete what is past its retention window     every day

Intervals come from `OSS_CLARITY["JOB_INTERVALS"]`. Run them from cron with
`manage.py oss_clarity_run_jobs` every five minutes, or from Celery beat with
the tasks in `oss_clarity.tasks`. Both go through `run_job`, which records
each run in `JobRun` and claims it first with one conditional UPDATE: only
the process whose update touched the row runs the job, so overlapping cron
runs, several servers, or cron and Celery together never run a job twice.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import suppress
from datetime import timedelta
from typing import Any

from django.db import IntegrityError
from django.db.models import F, Q
from django.utils import timezone

from . import analysis, heatmaps, retention
from .conf import settings
from .models import JobRun

logger = logging.getLogger(__name__)

JOBS: dict[str, Callable[[], Any]] = {
    "finalize": lambda: analysis.finalize_and_analyze(),
    "navigation": lambda: analysis.rollup_navigation(),
    "heatmaps": lambda: heatmaps.rollup_recent(),
    "prune": lambda: retention.prune(),
}
#: A run that started this many intervals ago and never finished is assumed
#: to have crashed, and may be claimed again.
STALE_INTERVALS = 4
#: The part of an error kept on `JobRun`: the class and message only.
ERROR_CHARS = 500


class NotClaimed(Exception):
    """The job is not due, or another process is running it."""


def interval(name: str) -> timedelta:
    return timedelta(minutes=settings.JOB_INTERVALS[name])


def claim(name: str, *, now=None, force: bool = False) -> bool:
    """Take the job for this process, or return False.

    Due: never run, or started at least one interval ago and finished since.
    `force` skips the interval but never runs a job that is still running.
    A run older than `STALE_INTERVALS` intervals that never finished counts
    as crashed and is taken over.
    """
    if name not in JOBS:
        raise KeyError(name)
    now = now or timezone.now()
    # A process racing this one may create the row first.
    with suppress(IntegrityError):
        JobRun.objects.get_or_create(name=name)

    period = interval(name)
    finished = Q(last_finished_at__gte=F("last_started_at"))
    crashed = Q(last_started_at__lte=now - period * STALE_INTERVALS)
    if force:
        ready = Q(last_started_at__isnull=True) | finished | crashed
    else:
        ready = (
            Q(last_started_at__isnull=True)
            | (Q(last_started_at__lte=now - period) & finished)
            | crashed
        )
    return JobRun.objects.filter(ready, name=name).update(last_started_at=now) == 1


def run_job(name: str, *, now=None, force: bool = False) -> Any:
    """Claim the job and run it, recording how it ended. Raises `NotClaimed`
    when it was not this process's to run."""
    if not claim(name, now=now, force=force):
        raise NotClaimed(name)
    try:
        result = JOBS[name]()
    except Exception as exc:
        message = f"{exc.__class__.__name__}: {exc}"[:ERROR_CHARS]
        JobRun.objects.filter(name=name).update(last_finished_at=_finished(now), last_error=message)
        logger.exception("oss_clarity job %s failed", name)
        raise
    JobRun.objects.filter(name=name).update(last_finished_at=_finished(now), last_error="")
    return result


def _finished(started) -> object:
    """Never before the start, even when the caller passed its own clock."""
    now = timezone.now()
    return max(now, started) if started else now


def run_due(*, only: str | None = None, force: bool = False, now=None) -> dict[str, Any]:
    """Run every job that is due (or just `only`). One failing job does not
    stop the others. Returns what each job did, or why it did not run."""
    results: dict[str, Any] = {}
    for name in [only] if only else JOBS:
        try:
            results[name] = run_job(name, now=now, force=force)
        except NotClaimed:
            results[name] = "not due"
        except Exception as exc:
            results[name] = f"failed: {exc.__class__.__name__}"
    return results
