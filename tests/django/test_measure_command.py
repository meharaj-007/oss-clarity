from __future__ import annotations

import json
from io import StringIO

import pytest
from django.core.management import CommandError, call_command

from oss_clarity.models import Recording

from .conftest import make_hit, make_recording
from .test_analysis import RAGE

pytestmark = pytest.mark.django_db


def test_the_command_reports_the_gate_baseline_sweeps_and_samples(site, tmp_path):
    person = make_recording(site, session_id="person", pages=[("p-0", RAGE)], status="complete")
    make_recording(site, session_id="crawler", pages=[("p-0", RAGE)], status="complete")
    make_hit(site, session_id="crawler", is_bot=True)
    make_recording(site, session_id="live", pages=[("p-0", RAGE)])  # still recording

    out = StringIO()
    path = tmp_path / "report.json"
    call_command("oss_clarity_measure", "--json", str(path), stdout=out)
    text = out.getvalue()

    assert "Gate: recordings 1/200 NOT met, sites 1/3 NOT met" in text
    assert "rage_window_ms" in text and "Watch these" in text
    assert f"recording {person.pk} at 1.0s" in text
    report = json.loads(path.read_text())
    assert report["recordings"] == 1 and report["sites"] == {"shop.example": 1}
    assert report["baseline"]["rage"]["total"] == 3
    # Nothing is written.
    assert not Recording.objects.filter(analyzed_at__isnull=False).exists()


def test_the_command_filters_by_site(site):
    make_recording(site, status="complete")
    out = StringIO()
    call_command("oss_clarity_measure", "--site", "elsewhere.example", stdout=out)
    assert out.getvalue().startswith("Recordings 0")
    out = StringIO()
    call_command("oss_clarity_measure", "--site", str(site.pk), "--limit", "5", stdout=out)
    assert out.getvalue().startswith("Recordings 1")


def test_negative_numbers_are_refused():
    with pytest.raises(CommandError):
        call_command("oss_clarity_measure", "--days", "-1", stdout=StringIO())
