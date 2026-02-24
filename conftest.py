from django.conf import settings


def pytest_configure():
    settings.DJANGO_SETTINGS_MODULE = "plfog.settings"


def pytest_sessionstart(session):
    from django.conf import settings as django_settings

    django_settings.STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
