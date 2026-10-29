import os
from collections.abc import Iterator

import pytest

from django.conf import settings
from pytest_django.plugin import DjangoDbBlocker

# Set by CI's postgres-only job, whose whole point is running these specs: there a database
# other than PostgreSQL is a broken job, not a reason to skip.
POSTGRES_ONLY_JOB = "PLFOG_POSTGRES_ONLY_JOB"


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
    """Skip ``postgres_only`` specs on a database that is not PostgreSQL, with the reason.

    SQLite has no row locks (it never writes FOR UPDATE) and no ``pg_*`` functions, so a spec
    that asserts a lock or provokes a real race cannot run there. CI's ``test`` job runs on SQLite
    and skips these; the ``postgres-only`` job runs them on PostgreSQL.
    """
    from django.db import connection

    marked = [item for item in items if "postgres_only" in item.keywords]
    if not marked or connection.vendor == "postgresql":
        return
    if os.environ.get(POSTGRES_ONLY_JOB):
        raise pytest.UsageError(f"{POSTGRES_ONLY_JOB} is set but the database is {connection.vendor}, not PostgreSQL.")
    skip = pytest.mark.skip(reason="Needs PostgreSQL; CI's postgres-only job runs it (pytest -m postgres_only).")
    for item in marked:
        item.add_marker(skip)


@pytest.fixture(autouse=True)
def _restore_latest_migrations(request: pytest.FixtureRequest, django_db_blocker: DjangoDbBlocker) -> Iterator[None]:
    """After a migration spec, migrate every app back to its latest migration if any is behind.

    A migration spec moves apps to an older state and must restore them. One that restored only its
    own target (or failed before its ``finally``) left later specs on an old schema, and they failed
    with missing columns far from the cause (#1002). Only ``*migration*_spec.py`` files pay for this.
    """
    yield
    if "migration" not in request.node.path.name:
        return
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor

    with django_db_blocker.unblock():
        executor = MigrationExecutor(connection)
        leaves = executor.loader.graph.leaf_nodes()
        if executor.migration_plan(leaves):
            executor.migrate(leaves)


@pytest.fixture(autouse=True)
def _disable_airtable_sync(settings):
    """Disable Airtable sync in all tests by default."""
    settings.AIRTABLE_SYNC_ENABLED = False


@pytest.fixture(autouse=True)
def _fake_stripe_keys(settings):
    """Stable Fernet encryption key so EncryptedCharField round-trips work in tests.

    All other Stripe credentials live in BillingSettings DB rows — see
    `_fake_billing_stripe_settings` below for the autouse DB fixture that populates them.
    """
    settings.STRIPE_FIELD_ENCRYPTION_KEY = "b4-PlK9DFN7ABVCQwOHuQXMydh5IUj1ysLKGJOqMEWI="


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
