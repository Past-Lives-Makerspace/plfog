"""Django system checks for required runtime configuration."""

import os

from django.conf import settings
from django.core.checks import Error, register


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
