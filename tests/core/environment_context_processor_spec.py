"""BDD specs for core.context_processors.environment (the deployment name and staging flag)."""

from __future__ import annotations

from django.test import RequestFactory

from core.context_processors import environment


def describe_environment():
    def it_exposes_the_deployment_name_and_the_staging_flag(settings):
        settings.ENVIRONMENT = "staging"
        settings.IS_STAGING = True
        assert environment(RequestFactory().get("/")) == {"ENVIRONMENT": "staging", "IS_STAGING": True}

    def it_reads_settings_at_request_time(settings):
        settings.ENVIRONMENT = "production"
        settings.IS_STAGING = False
        assert environment(RequestFactory().get("/")) == {"ENVIRONMENT": "production", "IS_STAGING": False}
