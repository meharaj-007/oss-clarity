from __future__ import annotations

import re
import uuid
from io import StringIO

import pytest
from django.core.management import call_command
from django.db import IntegrityError
from django.utils import timezone

from oss_clarity.core import SIGNAL_KINDS
from oss_clarity.models import (
    PUBLIC_KEY_PATTERN,
    SIGNAL_COLUMNS,
    Hit,
    Recording,
    RecordingSettings,
    SessionNavigation,
    Site,
)

pytestmark = pytest.mark.django_db


def make_site(**fields):
    return Site.objects.create(name="Shop", domain="shop.example", **fields)


def test_migrations_match_the_models():
    out = StringIO()
    call_command("makemigrations", "oss_clarity", "--check", "--dry-run", stdout=out)


def test_there_is_one_initial_migration():
    from pathlib import Path

    import oss_clarity

    folder = Path(oss_clarity.__file__).parent / "migrations"
    assert sorted(p.name for p in folder.glob("0*.py")) == ["0001_initial.py"]


def test_public_keys_are_random_url_safe_and_fixed_length():
    keys = {make_site().public_key for _ in range(20)}
    assert len(keys) == 20
    assert all(re.fullmatch(PUBLIC_KEY_PATTERN, key) for key in keys)


def test_recording_settings_are_created_off_with_the_configured_defaults(settings):
    settings.OSS_CLARITY = {"DEFAULT_SAMPLE_PCT": 25, "DEFAULT_MASK_MODE": "strict"}
    site = make_site()
    row = RecordingSettings.for_site(site)
    assert (row.enabled, row.sample_pct, row.mask_mode) == (False, 25, "strict")
    assert RecordingSettings.for_site(site).pk == row.pk


def test_a_recording_has_a_uuid_and_one_row_per_session():
    site = make_site()
    now = timezone.now()
    recording = Recording.objects.create(
        site=site, session_id="s-1", started_at=now, last_event_at=now
    )
    assert isinstance(recording.pk, uuid.UUID)
    with pytest.raises(IntegrityError):
        Recording.objects.create(site=site, session_id="s-1", started_at=now, last_event_at=now)


def test_every_signal_has_a_counter_column():
    assert set(SIGNAL_KINDS) | {"quick_back", "loop"} == set(SIGNAL_COLUMNS)
    names = {f.name for f in Recording._meta.get_fields()}
    assert set(SIGNAL_COLUMNS.values()) <= names


def test_visitor_id_is_indexed_wherever_it_is_stored():
    for model in (Hit, Recording, SessionNavigation):
        assert model._meta.get_field("visitor_id").db_index, model.__name__


def test_no_table_stores_an_ip_address_or_a_raw_user_agent():
    from django.apps import apps

    for model in apps.get_app_config("oss_clarity").get_models():
        for field in model._meta.get_fields():
            assert field.get_internal_type() != "GenericIPAddressField", model.__name__
            assert field.name not in ("ip", "ip_address", "user_agent"), model.__name__


def test_path_holds_long_paths():
    assert Hit._meta.get_field("path").max_length == 512
