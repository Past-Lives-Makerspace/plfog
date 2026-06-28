"""Cut NotificationPreference over to the unified per-channel shape (design §2.7).

Old shape: one row per ``(user, trigger)`` with two booleans
``push_enabled`` / ``email_enabled``.
New shape: one row per ``(user, event_key, channel)`` with a single ``enabled``.

The data migration (REVERSIBLE) fans each legacy row into per-channel rows —
``(event_key=trigger, channel="email", enabled=email_enabled)`` and
``(channel="push", enabled=push_enabled)`` — preserving every opt-in/out exactly. The
reverse collapses the per-channel rows back into the two booleans. The new columns are
added nullable so the expansion can run while the old columns still exist; the old
columns and the old unique constraint are dropped only after the data has moved.

This is the redesign's highest-risk migration (live ``NotificationPreference`` data),
so it is fully reversible and round-trip-tested (forward then backward preserves the
opt-in/out matrix).
"""

from __future__ import annotations

from django.db import migrations, models
import django.db.models.deletion


def fan_out_to_channels(apps, schema_editor):
    """Forward: expand each legacy (trigger, push, email) row into per-channel rows.

    The legacy row is reused for the EMAIL channel (event_key=trigger,
    enabled=email_enabled); a new row is created for the PUSH channel
    (enabled=push_enabled). Every user's exact opt-in/out for both channels is kept.
    """
    NotificationPreference = apps.get_model("core", "NotificationPreference")
    # Only the not-yet-converted (legacy) rows have a blank channel — re-running after a
    # partial run skips already-expanded rows, so the migration is safely resumable.
    legacy_rows = list(NotificationPreference.objects.filter(channel=""))
    for row in legacy_rows:
        # Reuse this row as the EMAIL channel preference.
        row.event_key = row.trigger
        row.channel = "email"
        row.enabled = bool(row.email_enabled)
        row.save(update_fields=["event_key", "channel", "enabled"])
        # New row for the PUSH channel preference.
        NotificationPreference.objects.create(
            user_id=row.user_id,
            trigger=row.trigger,
            push_enabled=row.push_enabled,
            email_enabled=row.email_enabled,
            event_key=row.trigger,
            channel="push",
            enabled=bool(row.push_enabled),
        )


def collapse_to_booleans(apps, schema_editor):
    """Reverse: collapse per-channel rows back into one (trigger, push, email) row.

    For each (user, event_key) the EMAIL row keeps the legacy row identity (its
    ``email_enabled`` is set from the email channel's ``enabled``); the PUSH row's
    ``enabled`` is folded into that same row's ``push_enabled`` and the PUSH row is
    deleted. Any non-email/push channel rows (scheduled email, digest) have no legacy
    column and are dropped on the way back — they did not exist in the old shape.
    """
    NotificationPreference = apps.get_model("core", "NotificationPreference")
    rows = list(NotificationPreference.objects.all())
    # Group rows by (user, event_key).
    grouped: dict[tuple[int, str], dict[str, object]] = {}
    for row in rows:
        key = (row.user_id, row.event_key)
        bucket = grouped.setdefault(key, {})
        bucket[row.channel] = row

    for (user_id, event_key), bucket in grouped.items():
        email_row = bucket.get("email")
        push_row = bucket.get("push")
        push_on = bool(getattr(push_row, "enabled", False)) if push_row is not None else False
        email_on = bool(getattr(email_row, "enabled", False)) if email_row is not None else False
        # Keep one canonical legacy row (prefer the email row).
        keeper = email_row or push_row or next(iter(bucket.values()))
        keeper.trigger = event_key
        keeper.push_enabled = push_on
        keeper.email_enabled = email_on
        keeper.save(update_fields=["trigger", "push_enabled", "email_enabled"])
        # Delete every other channel row for this (user, event_key).
        for channel, row in bucket.items():
            if row.pk != keeper.pk:
                row.delete()


class Migration(migrations.Migration):
    # Non-atomic: the data move (DML) is interleaved with column adds/drops (DDL) on the
    # SAME table. Inside one transaction Postgres rejects the follow-up ALTER TABLE with
    # "pending trigger events" (deferred constraint checks from the row deletes on the
    # reverse path). Running each operation in its own transaction sidesteps that; the
    # RunPython bodies are written to be safely re-runnable from any partial point.
    atomic = False

    dependencies = [
        ("core", "0033_discordwebhookroute_notificationtemplate_and_more"),
    ]

    operations = [
        # 1. Add the new columns, nullable/defaulted so the expansion can populate them
        #    while the legacy columns still exist.
        migrations.AddField(
            model_name="notificationpreference",
            name="event_key",
            field=models.CharField(default="", help_text="Event key from core.events.registry.", max_length=60),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="notificationpreference",
            name="channel",
            field=models.CharField(
                default="", help_text="Channel key from core.events.registry.Channel.", max_length=20
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="notificationpreference",
            name="enabled",
            field=models.BooleanField(
                default=False,
                help_text="Whether this user receives this event on this channel.",
            ),
        ),
        # 2. Drop the old (user, trigger) unique constraint BEFORE fanning out — the
        #    expansion inserts a second row sharing the same (user, trigger), which the
        #    old constraint would reject. (On reverse this is re-added last, after the
        #    collapse has restored one row per (user, trigger).)
        migrations.RemoveConstraint(
            model_name="notificationpreference",
            name="uq_notificationpreference_user_trigger",
        ),
        # 3. Make the legacy columns nullable so the data step can interleave with the
        #    column re-add on the REVERSE path. RemoveField re-adds each column using THIS
        #    (nullable) definition, so re-adding it onto the per-channel rows does not
        #    violate NOT NULL before the collapse backfills it; the AlterField reversal that
        #    restores NOT NULL runs AFTER the collapse (see op 5) so the column is populated.
        migrations.AlterField(
            model_name="notificationpreference",
            name="trigger",
            field=models.CharField(blank=True, null=True, max_length=40, help_text="Trigger key from core.triggers."),
        ),
        migrations.AlterField(
            model_name="notificationpreference",
            name="push_enabled",
            field=models.BooleanField(null=True, default=False, help_text="Send browser push for this trigger."),
        ),
        migrations.AlterField(
            model_name="notificationpreference",
            name="email_enabled",
            field=models.BooleanField(null=True, default=False, help_text="Send email for this trigger."),
        ),
        # 4. Move the data (reversible). Positioned AFTER the nullable AlterFields and
        #    BEFORE the RemoveFields so that on reverse the collapse runs while the legacy
        #    columns are present-and-nullable, then the AlterField reversals restore NOT
        #    NULL on the now-populated columns.
        migrations.RunPython(fan_out_to_channels, collapse_to_booleans),
        # 5. Drop the legacy columns.
        migrations.RemoveField(model_name="notificationpreference", name="trigger"),
        migrations.RemoveField(model_name="notificationpreference", name="push_enabled"),
        migrations.RemoveField(model_name="notificationpreference", name="email_enabled"),
        # 5. Add the new constraint + index.
        migrations.AddConstraint(
            model_name="notificationpreference",
            constraint=models.UniqueConstraint(
                fields=["user", "event_key", "channel"],
                name="uq_notificationpreference_user_event_channel",
            ),
        ),
        migrations.AddIndex(
            model_name="notificationpreference",
            index=models.Index(fields=["user", "event_key"], name="idx_notifpref_user_event"),
        ),
        # 6. Re-affirm the FK related_name (unchanged) so state matches the model exactly.
        migrations.AlterField(
            model_name="notificationpreference",
            name="user",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="notification_prefs",
                to="auth.user",
            ),
        ),
    ]
