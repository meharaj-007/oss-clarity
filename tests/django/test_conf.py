from __future__ import annotations

import pytest

from oss_clarity import conf
from oss_clarity.core import Thresholds


def ids(errors):
    return sorted(e.id for e in errors)


def test_defaults_apply_when_nothing_is_set(settings):
    if hasattr(settings, "OSS_CLARITY"):
        del settings.OSS_CLARITY
    assert conf.settings.MAX_CHUNK_BYTES == 3 * 1024 * 1024
    assert conf.settings.QUICK_BACK_SECONDS == 5
    assert conf.settings.thresholds == Thresholds()
    assert conf.check_settings() == []


def test_a_value_is_read_on_every_access(settings):
    settings.OSS_CLARITY = {"QUICK_BACK_SECONDS": 8}
    assert conf.settings.QUICK_BACK_SECONDS == 8
    settings.OSS_CLARITY = {"QUICK_BACK_SECONDS": 3}
    assert conf.settings.QUICK_BACK_SECONDS == 3


def test_thresholds_come_from_settings(settings):
    settings.OSS_CLARITY = {"THRESHOLDS": {"hesitation_min_ms": 2_000}}
    assert conf.settings.thresholds.hesitation_min_ms == 2_000
    assert conf.settings.thresholds.rage_min_clicks == Thresholds().rage_min_clicks


def test_job_intervals_merge_with_the_defaults(settings):
    settings.OSS_CLARITY = {"JOB_INTERVALS": {"prune": 720}}
    assert {**conf.DEFAULTS["JOB_INTERVALS"], "prune": 720} == conf.settings.JOB_INTERVALS


def test_an_unknown_name_is_an_attribute_error():
    with pytest.raises(AttributeError):
        _ = conf.settings.NO_SUCH_SETTING


@pytest.mark.parametrize(
    "value, expected",
    [
        ({"MAX_PAGES": 64, "API_PERMISSION": "myapp.perms.can_view"}, []),
        ([("MAX_PAGES", 64)], ["oss_clarity.E001"]),
        ({"MAX_PAGE": 64}, ["oss_clarity.E002"]),
        ({"MAX_PAGES": "64"}, ["oss_clarity.E003"]),
        ({"MAX_PAGES": -1}, ["oss_clarity.E003"]),
        ({"MAX_PAGES": True}, ["oss_clarity.E003"]),
        ({"CLIENT_IP_HEADER": 5}, ["oss_clarity.E004"]),
        ({"DEFAULT_SAMPLE_PCT": 101}, ["oss_clarity.E005"]),
        ({"DEFAULT_MASK_MODE": "loose"}, ["oss_clarity.E006"]),
        ({"THRESHOLDS": {"hesitation_min": 1}}, ["oss_clarity.E007"]),
        ({"JOB_INTERVALS": {"nightly": 5}}, ["oss_clarity.E008"]),
        ({"JOB_INTERVALS": {"prune": 0}}, ["oss_clarity.E008"]),
        ({"STORAGE": "recordings"}, ["oss_clarity.E009"]),
    ],
)
def test_the_system_check_reports_bad_settings(settings, value, expected):
    settings.OSS_CLARITY = value
    assert ids(conf.check_settings()) == expected


def test_the_check_is_registered(settings):
    from django.core import checks

    settings.OSS_CLARITY = {"MAX_PAGE": 1}
    assert "oss_clarity.E002" in ids(checks.run_checks(tags=[checks.Tags.compatibility]))
