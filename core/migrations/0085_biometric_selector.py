"""Split the biometric token into a stable selector and a rotating verifier.

Hand written rather than generated, because the selector column has to be filled with a
unique value per row in between being added and being made unique, and because the rows
that already exist have to be revoked on the way through. See
:class:`core.models.BiometricCredential` for why the selector must be unguessable.
"""

import secrets

from django.db import migrations, models
from django.utils import timezone

# Kept as a literal rather than imported from core.models. A migration has to keep working
# against the code as it was when it ran, and importing a constant lets a later edit
# silently change what this one did.
SELECTOR_BYTES = 48


def mint_selectors_and_revoke(apps, schema_editor):
    """Give every existing credential a selector, and revoke it.

    Both halves are necessary. The column is about to become unique, so every row needs a
    distinct value. And no shipped device knows its selector — every enrollment that exists
    was minted before selectors did — so not one of these credentials can be rescued.

    Revoking is the honest way to end them. Left live with a selector the device will never
    send, each row would fail on its first unlock and then be revoked anyway, one at a time,
    by the very replay branch this migration exists to enable — a phone that "stops working
    for no reason" instead of a clean handoff. Revoked here, the member's next app open
    falls straight back to an emailed code and the app offers to enrol again.
    """
    credential_model = apps.get_model("core", "BiometricCredential")
    now = timezone.now()
    for credential in credential_model.objects.all().iterator():
        credential.selector = secrets.token_urlsafe(SELECTOR_BYTES)
        # Rows revoked before today keep the timestamp that says when they really died.
        if credential.revoked_at is None:
            credential.revoked_at = now
        credential.save(update_fields=["selector", "revoked_at"])


def clear_selectors(apps, schema_editor):
    """Undo the selector minting. The revocations DELIBERATELY stand.

    Rolling a migration back must never re-arm a bearer credential. Nothing here records
    which rows this migration revoked and which were already dead, so un-revoking would
    resurrect credentials a member killed by hand from Signed In Devices — and any phone
    that has re-enrolled since holds a selector-format token the pre-migration code cannot
    read anyway, so there is nothing to restore it to. Clearing the column is the whole of
    the reversible part.

    Safe to run at this point: the operation that made ``selector`` unique has already been
    reversed by the time this executes, so blanking every row does not collide.
    """
    credential_model = apps.get_model("core", "BiometricCredential")
    credential_model.objects.update(selector="")


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0084_alter_siteconfiguration_signage_show_calendar_and_more"),
    ]

    operations = [
        migrations.RenameField(
            model_name="biometriccredential",
            old_name="secret_hash",
            new_name="verifier_hash",
        ),
        migrations.RenameField(
            model_name="biometriccredential",
            old_name="previous_secret_hash",
            new_name="previous_verifier_hash",
        ),
        migrations.AddField(
            model_name="biometriccredential",
            name="selector",
            field=models.CharField(
                default="",
                help_text="Random, permanent id for this credential. Identifies it; never authenticates it.",
                max_length=64,
            ),
            preserve_default=False,
        ),
        migrations.RunPython(mint_selectors_and_revoke, clear_selectors),
        migrations.AlterField(
            model_name="biometriccredential",
            name="selector",
            field=models.CharField(
                help_text="Random, permanent id for this credential. Identifies it; never authenticates it.",
                max_length=64,
                unique=True,
            ),
        ),
        migrations.AlterField(
            model_name="biometriccredential",
            name="verifier_hash",
            field=models.CharField(
                help_text="SHA-256 hex of the live verifier. The raw verifier is never stored.",
                max_length=64,
                unique=True,
            ),
        ),
        migrations.AlterField(
            model_name="biometriccredential",
            name="previous_verifier_hash",
            field=models.CharField(
                blank=True,
                default="",
                help_text="SHA-256 hex of the verifier this one replaced. Tells a dropped reply from a replay.",
                max_length=64,
            ),
        ),
        migrations.AlterField(
            model_name="biometriccredential",
            name="rotated_at",
            field=models.DateTimeField(
                blank=True,
                help_text="When the previous verifier was superseded. Starts the 60 second grace window.",
                null=True,
            ),
        ),
    ]
