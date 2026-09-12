"""Django system checks for required runtime configuration.

Where a check runs in production decides whether it protects anything. The Render
web service builds from the Dockerfile, and the container start runs ``migrate``
and ``seed_notification_templates`` before gunicorn, with the live environment.
Those commands run the plain system checks, not the ``--deploy`` ones, so a check
registered here without ``deploy=True`` fires on every deploy and an ``Error``
stops the container before it can serve. Nothing on Render ever runs
``manage.py check --deploy``, so a ``deploy=True`` check never fires there.

The same plain checks also run inside every management command with the default
``requires_system_checks``, which includes the ``run_scheduled_tasks`` cron every
fifteen minutes, on that service's own environment. Render has no env groups, so
the cron's copy of a variable can drift from the web service's. A check about
serving HTTP has nothing to protect on a cron, so it skips there rather than
taking every scheduled task down over a variable the cron never uses.
"""

import os
from collections.abc import Sequence

from django.apps import AppConfig
from django.conf import settings
from django.core.checks import CheckMessage, Error, register
from django.http.request import split_domain_port, validate_host


@register(deploy=True)
def check_webpush_settings(app_configs, **kwargs):
    """Ensure WEBPUSH VAPID keys are configured in production."""
    errors = []

    if settings.DEBUG or os.environ.get("CI"):
        return errors

    webpush = getattr(settings, "WEBPUSH_SETTINGS", {})
    required_keys = ["VAPID_PUBLIC_KEY", "VAPID_PRIVATE_KEY", "VAPID_ADMIN_EMAIL"]

    for key in required_keys:
        if not webpush.get(key):
            errors.append(
                Error(
                    f"WEBPUSH_SETTINGS['{key}'] is empty.",
                    hint=f"Set the WEBPUSH_{key} environment variable.",
                    id="core.E001",
                )
            )

    return errors


@register()
def check_public_hosts_are_allowed(app_configs: Sequence[AppConfig] | None, **kwargs: object) -> list[CheckMessage]:
    """Every PUBLIC_HOSTS entry must also pass ALLOWED_HOSTS.

    PUBLIC_HOSTS and ALLOWED_HOSTS come from separate environment variables and
    nothing else ties them together. A public host that ALLOWED_HOSTS rejects answers
    every request with a 400, so the guest surface is down on that host while the
    members surface looks fine. Failing the deploy here surfaces the mismatch before
    DNS points anyone at it.

    Deliberately not ``deploy=True``: see the module docstring for why a deploy-only
    check would never fire on Render.
    """
    errors: list[CheckMessage] = []

    if settings.DEBUG or os.environ.get("CI"):
        return errors
    # Render sets RENDER_SERVICE_TYPE on every service; a cron never serves a request, so
    # ALLOWED_HOSTS is meaningless there and an error would only stop the scheduled tasks.
    if os.environ.get("RENDER_SERVICE_TYPE") == "cron":
        return errors

    for host in settings.PUBLIC_HOSTS:
        domain, _port = split_domain_port(host)
        if not validate_host(domain, settings.ALLOWED_HOSTS):
            errors.append(
                Error(
                    f"PUBLIC_HOSTS entry '{host}' is not allowed by ALLOWED_HOSTS.",
                    hint=(
                        f"Effective ALLOWED_HOSTS is {settings.ALLOWED_HOSTS!r}. Add the host to "
                        "DJANGO_ALLOWED_HOSTS (and CSRF_TRUSTED_ORIGINS) in the same change that adds "
                        "it to PUBLIC_HOSTS, or remove it from PUBLIC_HOSTS."
                    ),
                    id="core.E002",
                )
            )

    return errors
