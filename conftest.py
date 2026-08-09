import pytest

from django.conf import settings


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


@pytest.fixture
def configured_billing_stripe(db):
    """Populate BillingSettings.connect_* with fake values for tests that hit
    stripe_utils helpers without patching. Most tests patch `_get_stripe_client`
    or `construct_webhook_event` directly and don't need this fixture.
    """
    from billing.models import BillingSettings

    bs = BillingSettings.load()
    bs.connect_client_id = "ca_test_fake_for_testing"
    bs.connect_platform_publishable_key = "pk_test_fake_for_testing"
    bs.connect_platform_secret_key = "sk_test_fake_for_testing"
    bs.connect_platform_webhook_secret = "whsec_fake_for_testing"
    # The fake values live in the LIVE-named fields, so keep test mode off — otherwise
    # the new mode toggle would shadow them behind the (empty) TEST slot.
    bs.test_mode = False
    bs.save()
    return bs
