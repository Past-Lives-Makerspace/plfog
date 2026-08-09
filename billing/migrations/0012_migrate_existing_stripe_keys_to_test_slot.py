"""Relocate any currently-configured TEST Stripe keys into the new test slot.

Before this change the single ``connect_*`` credential slot held whatever keys
the platform was using — in practice ``sk_test_…`` keys, because the makerspace
has been running in test mode. Now that a dedicated ``test_connect_*`` slot
exists, move those test keys there and set ``test_mode=True`` so nothing breaks,
leaving the LIVE-named slot empty and ready for go-live credentials.

A live secret (``sk_live_…``) is left exactly where it is and ``test_mode`` is
set to False so real charges keep flowing. With no secret at all we default to
test mode (the safe default) so a fresh install never lands in live mode.
"""

from __future__ import annotations

from typing import Any

from django.db import migrations

_ALL_CREDENTIAL_FIELDS = [
    "connect_client_id",
    "connect_platform_publishable_key",
    "connect_platform_secret_key",
    "connect_platform_webhook_secret",
    "test_connect_client_id",
    "test_connect_platform_publishable_key",
    "test_connect_platform_secret_key",
    "test_connect_platform_webhook_secret",
]


def _is_test_secret(secret: str) -> bool:
    """True when the secret is a Stripe TEST-mode key (secret or restricted)."""
    return secret.startswith(("sk_test_", "rk_test_"))


def migrate_to_test_slot(apps: Any, schema_editor: Any) -> None:
    """Move existing test keys from the LIVE slot into the new TEST slot."""
    BillingSettings = apps.get_model("billing", "BillingSettings")
    bs = BillingSettings.objects.filter(pk=1).first()
    if bs is None:
        return

    secret = bs.connect_platform_secret_key
    if secret and _is_test_secret(secret):
        bs.test_connect_client_id = bs.connect_client_id
        bs.test_connect_platform_publishable_key = bs.connect_platform_publishable_key
        bs.test_connect_platform_secret_key = bs.connect_platform_secret_key
        bs.test_connect_platform_webhook_secret = bs.connect_platform_webhook_secret
        bs.connect_client_id = ""
        bs.connect_platform_publishable_key = ""
        bs.connect_platform_secret_key = ""
        bs.connect_platform_webhook_secret = ""
        bs.test_mode = True
    else:
        # A live secret stays put (test_mode off); no secret at all defaults to test mode.
        bs.test_mode = not bool(secret)

    bs.save(update_fields=[*_ALL_CREDENTIAL_FIELDS, "test_mode"])


def reverse_migrate_to_test_slot(apps: Any, schema_editor: Any) -> None:
    """Fold the TEST slot back into the LIVE slot so the schema can be reverted."""
    BillingSettings = apps.get_model("billing", "BillingSettings")
    bs = BillingSettings.objects.filter(pk=1).first()
    if bs is None:
        return

    if bs.test_connect_platform_secret_key:
        bs.connect_client_id = bs.test_connect_client_id
        bs.connect_platform_publishable_key = bs.test_connect_platform_publishable_key
        bs.connect_platform_secret_key = bs.test_connect_platform_secret_key
        bs.connect_platform_webhook_secret = bs.test_connect_platform_webhook_secret
        bs.test_connect_client_id = ""
        bs.test_connect_platform_publishable_key = ""
        bs.test_connect_platform_secret_key = ""
        bs.test_connect_platform_webhook_secret = ""
        bs.save(update_fields=_ALL_CREDENTIAL_FIELDS)
    # Nothing in the test slot means nothing was relocated — leave the row untouched.


class Migration(migrations.Migration):
    dependencies = [
        ("billing", "0011_billingsettings_test_connect_client_id_and_more"),
    ]

    operations = [
        migrations.RunPython(migrate_to_test_slot, reverse_migrate_to_test_slot),
    ]
