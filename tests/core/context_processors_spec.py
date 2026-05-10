"""BDD-style tests for core.context_processors."""

import pytest
from django.test import RequestFactory

from core.context_processors import app_version, google_analytics, registration_mode
from core.models import SiteConfiguration
from plfog.version import CHANGELOG, VERSION

pytestmark = pytest.mark.django_db


def describe_registration_mode():
    def it_returns_true_when_open():
        config = SiteConfiguration.load()
        config.registration_mode = SiteConfiguration.RegistrationMode.OPEN
        config.save()

        rf = RequestFactory()
        request = rf.get("/")
        result = registration_mode(request)
        assert result == {"registration_is_open": True}

    def it_returns_false_when_invite_only():
        config = SiteConfiguration.load()
        config.registration_mode = SiteConfiguration.RegistrationMode.INVITE_ONLY
        config.save()

        rf = RequestFactory()
        request = rf.get("/")
        result = registration_mode(request)
        assert result == {"registration_is_open": False}

    def it_defaults_to_invite_only():
        rf = RequestFactory()
        request = rf.get("/")
        result = registration_mode(request)
        assert result == {"registration_is_open": False}


def describe_app_version():
    def it_returns_version_string():
        rf = RequestFactory()
        request = rf.get("/")
        result = app_version(request)
        assert result["app_version"] == VERSION

    def it_returns_changelog_list():
        rf = RequestFactory()
        request = rf.get("/")
        result = app_version(request)
        assert isinstance(result["changelog"], list)
        assert len(result["changelog"]) >= 1
        assert result["changelog"][0]["version"] == CHANGELOG[0]["version"]


def describe_google_analytics():
    def it_returns_empty_when_id_not_configured():
        rf = RequestFactory()
        request = rf.get("/")
        result = google_analytics(request)
        assert result == {"google_analytics_measurement_id": ""}

    def it_returns_measurement_id_when_configured():
        config = SiteConfiguration.load()
        config.google_analytics_measurement_id = "G-TEST123"
        config.save()

        rf = RequestFactory()
        request = rf.get("/")
        result = google_analytics(request)
        assert result == {"google_analytics_measurement_id": "G-TEST123"}

    def it_returns_empty_on_admin_paths_even_when_configured():
        config = SiteConfiguration.load()
        config.google_analytics_measurement_id = "G-TEST123"
        config.save()

        rf = RequestFactory()
        request = rf.get("/admin/")
        result = google_analytics(request)
        assert result == {"google_analytics_measurement_id": ""}
