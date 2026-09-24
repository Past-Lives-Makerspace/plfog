"""Bring production's Stripe TEST-mode keys onto staging after a database refresh.

The refreshed row carries production's test-slot credentials, but the two secret ones are
Fernet ciphertext under production's ``STRIPE_FIELD_ENCRYPTION_KEY``; under staging's own
key the field reads them back blank. This command reads the raw ciphertext through a
cursor (the field would already have blanked it), decrypts each value with production's
key supplied in the environment for this one run, and saves the plaintexts back through
the model so they are re-encrypted under the staging key. The live-slot secrets are
blanked (live credentials never exist on staging) and ``test_mode`` is forced on.

Production has no test-mode webhook secret (its slot is blank), while the staging box has
its own Stripe test endpoint whose signing secret lives in the box's ``.env`` as
``STAGING_STRIPE_TEST_WEBHOOK_SECRET``. When production's value decrypts to blank and that
variable is set, the slot is filled from it, so every refresh restores the staging
endpoint; a non-blank production value still wins, because it means someone pointed one
shared test endpoint at both.

Fails closed like the rest of staging mode: refuses outside ``ENVIRONMENT=staging``,
refuses without ``PROD_STRIPE_FIELD_ENCRYPTION_KEY``, and writes nothing when a value does
not decrypt. Never prints a plaintext; each imported value is shown masked.

    PROD_STRIPE_FIELD_ENCRYPTION_KEY='...' python manage.py staging_import_stripe_test_keys
"""

from __future__ import annotations

import os
from typing import Any, cast

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, models

from billing.fields import fernet_from_key
from billing.models import BillingSettings

ENV_KEY = "PROD_STRIPE_FIELD_ENCRYPTION_KEY"
FALLBACK_WEBHOOK_ENV = "STAGING_STRIPE_TEST_WEBHOOK_SECRET"
WEBHOOK_FIELD = "test_connect_platform_webhook_secret"
ENCRYPTED_TEST_FIELDS: tuple[str, ...] = ("test_connect_platform_secret_key", "test_connect_platform_webhook_secret")
LIVE_ENCRYPTED_FIELDS: tuple[str, ...] = ("connect_platform_secret_key", "connect_platform_webhook_secret")
_MASK_VISIBLE_CHARS = 8


def masked(value: str) -> str:
    """The first characters of ``value`` and its length, never the whole secret."""
    return f"{value[:_MASK_VISIBLE_CHARS]}... {len(value)} characters"


class Command(BaseCommand):
    help = (
        "Staging only: decrypt production's Stripe test-slot secrets with production's Fernet key "
        "(from PROD_STRIPE_FIELD_ENCRYPTION_KEY) and re-encrypt them under this box's key."
    )

    def handle(self, *args: Any, **options: Any) -> None:
        if not settings.IS_STAGING:
            raise CommandError("Refusing: this command only runs with ENVIRONMENT=staging.")
        prod_key = os.environ.get(ENV_KEY, "").strip()
        if not prod_key:
            raise CommandError(f"{ENV_KEY} is not set; pass production's Fernet key in the environment for this run.")
        try:
            prod_fernet = fernet_from_key(prod_key, name=ENV_KEY)
        except ImproperlyConfigured as exc:
            raise CommandError(str(exc)) from exc

        plaintexts = {
            field: _decrypt(prod_fernet, field, ciphertext) for field, ciphertext in _raw_ciphertexts().items()
        }
        lines = {field: _report(field, plaintext) for field, plaintext in plaintexts.items()}

        fallback_webhook = os.environ.get(FALLBACK_WEBHOOK_ENV, "").strip()
        if not plaintexts[WEBHOOK_FIELD] and fallback_webhook:
            plaintexts[WEBHOOK_FIELD] = fallback_webhook
            lines[WEBHOOK_FIELD] = f"{WEBHOOK_FIELD}: restored from {FALLBACK_WEBHOOK_ENV} ({masked(fallback_webhook)})"

        billing = BillingSettings.load()
        for field, plaintext in plaintexts.items():
            setattr(billing, field, plaintext)
        for field in LIVE_ENCRYPTED_FIELDS:
            setattr(billing, field, "")
        billing.test_mode = True
        billing.save()

        for line in lines.values():
            self.stdout.write(line)


def _report(field: str, plaintext: str) -> str:
    """The stdout line for a value taken from production: masked, or noted as blank."""
    if plaintext:
        return f"{field}: imported ({masked(plaintext)})"
    return f"{field}: blank on production"


def _column(field_name: str) -> str:
    """The database column behind ``field_name`` on BillingSettings (``db_column`` or the attname)."""
    field = cast("models.Field[Any, Any]", BillingSettings._meta.get_field(field_name))
    return field.get_attname_column()[1] or field.get_attname()


def _raw_ciphertexts() -> dict[str, str]:
    """The stored ciphertext of each encrypted test-slot column, read past the field's decrypt."""
    meta = BillingSettings._meta
    quote = connection.ops.quote_name
    columns = ", ".join(quote(_column(field)) for field in ENCRYPTED_TEST_FIELDS)
    pk_column = _column(cast("models.Field[Any, Any]", meta.pk).name)
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT {columns} FROM {quote(meta.db_table)} WHERE {quote(pk_column)} = 1")  # noqa: S608
        row = cursor.fetchone()
    if row is None:
        raise CommandError("No BillingSettings row to import from; refresh the database first.")
    return {field: (value or "") for field, value in zip(ENCRYPTED_TEST_FIELDS, row, strict=True)}


def _decrypt(prod_fernet: Fernet, field: str, ciphertext: str) -> str:
    """``ciphertext`` under production's key as plaintext; blank stays blank."""
    if not ciphertext:
        return ""
    try:
        return prod_fernet.decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise CommandError(f"{field}: the stored value does not decrypt with {ENV_KEY}; nothing was written.") from exc
