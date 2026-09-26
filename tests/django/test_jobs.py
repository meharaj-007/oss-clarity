from __future__ import annotations

import json
from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import CommandError, call_command
from django.utils import timezone

from oss_clarity import jobs
from oss_clarity.models import JobRun

from .conftest import make_hit, make_recording

pytestmark = pytest.mark.django_db


@pytest.fixture
def calls(monkeypatch):
    """Replace the jobs with ones that record that they ran."""
    ran: list[str] = []
    monkeypatch.setattr(
        jobs,
        "JOBS",
        {name: (lambda name=name: ran.append(name) or name) for name in jobs.JOBS},
    )
    return ran


def test_a_job_that_never_ran_is_due_and_then_is_not(calls):
    now = timezone.now()
    assert jobs.run_job("finalize", now=now) == "finalize"
    with pytest.raises(jobs.NotClaimed):
        jobs.run_job("finalize", now=now + timedelta(minutes=4))
    assert jobs.run_job("finalize", now=now + timedelta(minutes=5)) == "finalize"
    assert calls == ["finalize", "finalize"]
    run = JobRun.objects.get(name="finalize")
    assert run.last_finished_at >= run.last_started_at and run.last_error == ""


def test_two_claims_at_once_have_one_winner(calls):
    now = timezone.now()
    assert jobs.claim("heatmaps", now=now) is True
    # The first is still running: nobody else may start it, even forced.
    assert jobs.claim("heatmaps", now=now) is False
    assert jobs.claim("heatmaps", now=now + timedelta(hours=2), force=True) is False
    assert jobs.claim("heatmaps", now=now + timedelta(hours=2)) is False


def test_a_run_that_never_finished_is_taken_over_after_four_intervals(calls):
    now = timezone.now()
    assert jobs.claim("finalize", now=now)
    assert not jobs.claim("finalize", now=now + timedelta(minutes=19))
    assert jobs.claim("finalize", now=now + timedelta(minutes=20))


def test_force_ignores_the_interval(calls):
    now = timezone.now()
    jobs.run_job("prune", now=now)
    assert jobs.run_job("prune", now=now + timedelta(minutes=1), force=True) == "prune"


def test_a_failure_is_recorded_and_retried_next_interval(monkeypatch):
    def boom():
        raise RuntimeError("disk full")

    monkeypatch.setattr(jobs, "JOBS", {**jobs.JOBS, "prune": boom})
    now = timezone.now()
    with pytest.raises(RuntimeError):
        jobs.run_job("prune", now=now)
    assert JobRun.objects.get(name="prune").last_error == "RuntimeError: disk full"
    with pytest.raises(jobs.NotClaimed):
        jobs.run_job("prune", now=now + timedelta(hours=1))
    monkeypatch.setattr(jobs, "JOBS", {**jobs.JOBS, "prune": lambda: "ok"})
    assert jobs.run_job("prune", now=now + timedelta(days=1)) == "ok"
    assert JobRun.objects.get(name="prune").last_error == ""


def test_intervals_come_from_settings(calls, settings):
    settings.OSS_CLARITY = {"JOB_INTERVALS": {"finalize": 1}}
    now = timezone.now()
    jobs.run_job("finalize", now=now)
    assert jobs.run_job("finalize", now=now + timedelta(minutes=1)) == "finalize"


def test_run_due_runs_what_is_due_and_isolates_failures(monkeypatch):
    def boom():
        raise RuntimeError("nope")

    monkeypatch.setattr(
        jobs,
        "JOBS",
        {"finalize": lambda: 1, "navigation": boom, "heatmaps": lambda: 3, "prune": lambda: 4},
    )
    results = jobs.run_due()
    assert results == {
        "finalize": 1,
        "navigation": "failed: RuntimeError",
        "heatmaps": 3,
        "prune": 4,
    }
    assert jobs.run_due(only="heatmaps") == {"heatmaps": "not due"}


def test_the_real_jobs_run_end_to_end(site):
    make_recording(site, last_event_at=timezone.now() - timedelta(hours=1))
    make_hit(site, ago=timedelta(days=40))
    results = jobs.run_due()
    assert results["finalize"]["analyzed"] == 1
    assert results["prune"]["hits"] == 1
    assert set(results) == {"finalize", "navigation", "heatmaps", "prune"}


# --- commands --------------------------------------------------------------------------


def test_the_command_runs_due_jobs_and_lists_them(calls):
    out = StringIO()
    call_command("oss_clarity_run_jobs", stdout=out)
    assert calls == ["finalize", "navigation", "heatmaps", "prune"]
    assert json.loads(out.getvalue().splitlines()[0].split(": ", 1)[1]) == "finalize"

    out = StringIO()
    call_command("oss_clarity_run_jobs", "--only", "prune", stdout=out)
    assert out.getvalue().strip() == 'prune: "not due"'

    out = StringIO()
    call_command("oss_clarity_run_jobs", "--list", stdout=out)
    listing = out.getvalue()
    assert "finalize    every     5 min" in listing and "prune       every  1440 min" in listing


def test_the_command_fails_loudly_when_a_job_fails(monkeypatch):
    def boom():
        raise RuntimeError("nope")

    monkeypatch.setattr(jobs, "JOBS", {**jobs.JOBS, "prune": boom})
    with pytest.raises(CommandError):
        call_command("oss_clarity_run_jobs", "--only", "prune", stdout=StringIO())


def test_the_prune_command_forces_a_prune(calls):
    call_command("oss_clarity_prune", stdout=StringIO())
    call_command("oss_clarity_prune", stdout=StringIO())
    assert calls == ["prune", "prune"]


# --- Celery -----------------------------------------------------------------------------


def test_the_beat_schedule_expires_every_entry_before_its_next_run():
    from oss_clarity.tasks import BEAT_SCHEDULE

    assert set(BEAT_SCHEDULE) == {f"oss_clarity.{name}" for name in jobs.JOBS}
    for entry in BEAT_SCHEDULE.values():
        assert 0 < entry["options"]["expires"] < entry["schedule"]


def test_the_celery_tasks_go_through_the_claim(calls):
    pytest.importorskip("celery")
    from oss_clarity import tasks

    assert tasks.finalize() == "finalize"
    assert tasks.finalize() is None  # not due again yet
