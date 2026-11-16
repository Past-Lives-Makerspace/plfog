import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from django.conf import settings
from pytest_django.plugin import DjangoDbBlocker

# Set by CI's postgres-only job, whose whole point is running these specs: there a database
# other than PostgreSQL is a broken job, not a reason to skip.
POSTGRES_ONLY_JOB = "PLFOG_POSTGRES_ONLY_JOB"

ROOT = Path(__file__).resolve().parent


def is_migration_spec(path: Path) -> bool:
    """Whether a spec file rewinds the schema: "migration" in its path inside the repository.

    Relative to the repository root, so a checkout under a folder such as ``migrations-work``
    does not make every spec a migration spec.
    """
    return "migration" in path.resolve().relative_to(ROOT).as_posix()


def pytest_configure():
    settings.DJANGO_SETTINGS_MODULE = "plfog.settings"


def pytest_sessionstart(session):
    from pathlib import Path

    from django.conf import settings as django_settings

    django_settings.STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }

    # STATIC_ROOT is a collectstatic build artifact, absent in a fresh checkout and in
    # CI. WhiteNoise's middleware warns "No directory at: .../staticfiles/" every time
    # the handler is built — once per test client — which buried the suite in thousands
    # of identical UserWarnings. An empty directory silences it; tests serve static
    # assets through StaticFilesStorage above, not from STATIC_ROOT.
    Path(django_settings.STATIC_ROOT).mkdir(parents=True, exist_ok=True)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Mark the migration specs, and skip ``postgres_only`` specs on a database that is not PostgreSQL.

    A spec file with "migration" in its path rewinds the schema (the rule
    ``_restore_latest_migrations`` uses), so it carries the ``migration`` marker and CI runs it
    in a shard of its own (``.github/workflows/specs.yml``, #1006).

    SQLite has no row locks (it never writes FOR UPDATE) and no ``pg_*`` functions, so a spec
    that asserts a lock or provokes a real race cannot run there. CI's spec shards run on SQLite
    and skip these; the ``postgres-only`` job runs them on PostgreSQL.
    """
    from django.db import connection

    for item in items:
        if is_migration_spec(item.path):
            item.add_marker(pytest.mark.migration)

    marked = [item for item in items if "postgres_only" in item.keywords]
    if not marked or connection.vendor == "postgresql":
        return
    if os.environ.get(POSTGRES_ONLY_JOB):
        raise pytest.UsageError(f"{POSTGRES_ONLY_JOB} is set but the database is {connection.vendor}, not PostgreSQL.")
    skip = pytest.mark.skip(reason="Needs PostgreSQL; CI's postgres-only job runs it (pytest -m postgres_only).")
    for item in marked:
        item.add_marker(skip)


@pytest.fixture(autouse=True, scope="module")
def _restore_latest_migrations(request: pytest.FixtureRequest, django_db_blocker: DjangoDbBlocker) -> Iterator[None]:
    """After a migration spec file, migrate every app back to its latest migration if any is behind.

    A migration spec moves apps to an older state and must restore them. One that restored only its
    own target (or failed before its ``finally``) left later specs on an old schema, and they failed
    with missing columns far from the cause (#1002). Once per spec file with "migration" in its path, not per
    spec: on SQLite each restore rebuilds tables, and per spec it pushed the test job past its
    three hour limit (#1004).
    """
    yield
    if not is_migration_spec(request.path):
        return
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor

    with django_db_blocker.unblock():
        executor = MigrationExecutor(connection)
        leaves = executor.loader.graph.leaf_nodes()
        if executor.migration_plan(leaves):
            executor.migrate(leaves)
            # Migrating forward ran the guide seeds again; later specs expect the wiki empty.
            _drop_seeded_guide_pages()


@pytest.fixture(autouse=True)
def _disable_airtable_sync(settings):
    """Disable Airtable sync in all tests by default."""
    settings.AIRTABLE_SYNC_ENABLED = False


@pytest.fixture(autouse=True, scope="session")
def _media_in_tmp(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Uploads land in a throwaway folder, never the checkout's media/.

    factory-boy's ImageField writes a real file on every build, and nothing removes it, so
    without this the checkout's media/ grew by hundreds of thousands of example.jpg files
    (3 GB on one machine). pytest prunes tmp_path folders after a few runs on its own.
    One folder for the whole run, not one per spec: storage names must stay unique across
    specs, as in production, because ``ClassOffering.hero_aspect_ratio`` caches a photo's
    size under its name, and a fresh folder per spec handed every spec the same name
    and an earlier spec's size. Per spec it also pushed the test job past its three hour
    limit (#998).
    """
    from django.test import override_settings

    with override_settings(MEDIA_ROOT=str(tmp_path_factory.mktemp("media"))):
        yield


@pytest.fixture(autouse=True)
def _fake_stripe_keys(settings):
    """Stable Fernet encryption key so EncryptedCharField round-trips work in tests.

    All other Stripe credentials live in BillingSettings DB rows — see
    `_fake_billing_stripe_settings` below for the autouse DB fixture that populates them.
    """
    settings.STRIPE_FIELD_ENCRYPTION_KEY = "b4-PlK9DFN7ABVCQwOHuQXMydh5IUj1ysLKGJOqMEWI="


def _drop_seeded_guide_pages() -> None:
    """Remove the pages migrations ``membership.0278``, ``0279`` and ``0282`` seed, so specs start with the wiki as empty as before #1167.

    The test database is built from the migrations, so without this every wiki list, count and
    search spec would meet one page nobody made. A spec about that page asks for ``tour_guide_guide``.
    """
    from membership.models import WikiPage

    WikiPage.objects.exclude(guide_for="").delete()


@pytest.fixture(scope="session")
def django_db_setup(django_db_setup: None, django_db_blocker: DjangoDbBlocker) -> None:
    """pytest-django's database, built from the migrations, without the seeded guide pages (once per session)."""
    with django_db_blocker.unblock():
        _drop_seeded_guide_pages()


@pytest.fixture
def tour_guide_guide(db: None) -> Any:
    """The Tour Guide Guide wiki page as migration ``membership.0278`` seeds it on deploy (#1167)."""
    import importlib

    from django.apps import apps

    from membership.models import AdminCapability, WikiPage

    importlib.import_module("membership.migrations.0278_seed_tour_guide_guide").seed(apps, None)
    return WikiPage.guide_page_for(AdminCapability.Capability.TOUR_GUIDE)


@pytest.fixture(autouse=True)
def _deferred_work_inline(monkeypatch):
    """Run ``core.deferred.defer`` work at the call, in the test's own thread.

    Production starts a thread after the commit; a spec inside pytest's test transaction
    would never see the commit, and a thread would race its assertions. A spec that tests
    the real thread puts ``core.deferred.after_commit_thread`` back on ``_schedule``.
    """
    from core import deferred

    monkeypatch.setattr(deferred, "_schedule", deferred.run_inline)


@pytest.fixture
def midday_now():
    """Pin ``timezone.now`` to local noon of today, reading the wall clock once.

    For a spec that builds "open today" hours from the local weekday and then asserts
    against now: two reads either side of local midnight disagree on the day, and an
    all day window ending at 23:59 is shut in the last minute. Noon is clear of both.
    """
    from unittest.mock import patch

    from django.utils import timezone

    noon = timezone.localtime().replace(hour=12, minute=0, second=0, microsecond=0)
    with patch("django.utils.timezone.now", return_value=noon):
        yield noon


_PACIFIC = ZoneInfo("America/Los_Angeles")

#: The nights a spec seeding "two days out" must read the same on (#1036). Clocks fall back at
#: 02:00 on Sunday 2026-11-01, so on the second night before it a seed two days out starts in
#: the 1 AM hour that happens twice: at 01:30 in its first pass, at 02:30 in its second. At 23:30
#: a seed ends in it, which no spec here reads yet; the ordinary night is the control.
FALL_BACK_NIGHTS = {
    "fall-back-night-2330": datetime(2026, 10, 29, 23, 30, tzinfo=_PACIFIC),
    "fall-back-night-0130": datetime(2026, 10, 30, 1, 30, tzinfo=_PACIFIC),
    "fall-back-night-0230": datetime(2026, 10, 30, 2, 30, tzinfo=_PACIFIC),
    "ordinary-night-2330": datetime(2026, 11, 10, 23, 30, tzinfo=_PACIFIC),
}

#: The same nights for a spec seeding "five days out": the fifth night before the fall back.
FIVE_DAYS_BEFORE_FALL_BACK_NIGHTS = {
    "fall-back-night-2330": datetime(2026, 10, 26, 23, 30, tzinfo=_PACIFIC),
    "fall-back-night-0130": datetime(2026, 10, 27, 1, 30, tzinfo=_PACIFIC),
    "fall-back-night-0230": datetime(2026, 10, 27, 2, 30, tzinfo=_PACIFIC),
    "ordinary-night-2330": FALL_BACK_NIGHTS["ordinary-night-2330"],
}


def _frozen_now(moment: datetime) -> Iterator[datetime]:
    """Freeze ``timezone.now`` at ``moment`` in UTC, the way the real clock reports it."""
    from unittest.mock import patch

    moment = moment.astimezone(UTC)
    with patch("django.utils.timezone.now", return_value=moment):
        yield moment


@pytest.fixture(params=list(FALL_BACK_NIGHTS.values()), ids=list(FALL_BACK_NIGHTS))
def fall_back_nights(request: pytest.FixtureRequest) -> Iterator[datetime]:
    """Run the spec once per ``FALL_BACK_NIGHTS`` moment, with ``timezone.now`` frozen there in UTC.

    For a spec that seeds a block, event or meeting relative to now and then posts or compares
    it as a local time: it must pin the hour (10:00 local) and keep now only for the day.
    """
    yield from _frozen_now(request.param)


@pytest.fixture(params=list(FIVE_DAYS_BEFORE_FALL_BACK_NIGHTS.values()), ids=list(FIVE_DAYS_BEFORE_FALL_BACK_NIGHTS))
def five_days_before_fall_back_nights(request: pytest.FixtureRequest) -> Iterator[datetime]:
    """``fall_back_nights`` for a spec that seeds five days out."""
    yield from _frozen_now(request.param)


@pytest.fixture
def configured_billing_stripe(db):
    """Populate BillingSettings.connect_* with fake values for tests that hit
    stripe_utils helpers without patching. Most tests patch `_get_stripe_client`
    or `construct_webhook_event` directly and don't need this fixture.
    """
    from billing.models import BillingSettings

    bs = BillingSettings.load()
    bs.connect_platform_publishable_key = "pk_test_fake_for_testing"
    bs.connect_platform_secret_key = "sk_test_fake_for_testing"
    bs.connect_platform_webhook_secret = "whsec_fake_for_testing"
    # The fake values live in the LIVE-named fields, so keep test mode off — otherwise
    # the new mode toggle would shadow them behind the (empty) TEST slot.
    bs.test_mode = False
    bs.save()
    return bs
