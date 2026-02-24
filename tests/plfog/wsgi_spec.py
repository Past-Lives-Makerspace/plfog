"""Tests for plfog.wsgi module."""

import os


def describe_wsgi_module():
    def it_exposes_callable_application():
        from plfog.wsgi import application

        assert callable(application)

    def it_sets_django_settings_module_env_var():
        import plfog.wsgi  # noqa: F401

        assert os.environ["DJANGO_SETTINGS_MODULE"] == "plfog.settings"
