"""Turn every instructor's instructor emails back on (2026-10-14).

Before #812 a settings save wrote every box, so a save made before #612 (when these emails
defaulted off) stored "off" as if the member had chosen it: 20 instructors stopped getting
"class approved" and "changes requested" emails that way. This flips those rows on for every
member who teaches (a public instructor page or teaching access). From here on
``Member.grant_teaching`` and ``Member.ensure_instructor_slug`` do the same for new ones.

Every row it flips is stamped ``changed_at=FLIPPED_AT`` with no ``changed_by``, a marker no
save produces, so the reverse turns exactly those rows back off.
"""

import datetime

from django.db import migrations
from django.db.models import Q

# Frozen copy of core.events.preferences.INSTRUCTOR_EMAIL_KEYS as of this migration.
INSTRUCTOR_EMAIL_KEYS = (
    "instructor_class_approved",
    "instructor_changes_requested",
    "discount_code.request_approved",
    "discount_code.request_declined",
)

FLIPPED_AT = datetime.datetime(2026, 10, 14, tzinfo=datetime.UTC)


def turn_on(apps, schema_editor):
    Member = apps.get_model("membership", "Member")
    NotificationPreference = apps.get_model("core", "NotificationPreference")
    teachers = Member.objects.filter(user__isnull=False).filter(
        Q(instructor_oriented_at__isnull=False) | ~Q(instructor_slug="")
    )
    NotificationPreference.objects.filter(
        user_id__in=teachers.values("user_id"),
        event_key__in=INSTRUCTOR_EMAIL_KEYS,
        channel="email",
        enabled=False,
    ).update(enabled=True, changed_at=FLIPPED_AT, changed_by=None)


def turn_back_off(apps, schema_editor):
    NotificationPreference = apps.get_model("core", "NotificationPreference")
    NotificationPreference.objects.filter(changed_at=FLIPPED_AT, changed_by__isnull=True, enabled=True).update(
        enabled=False, changed_at=None
    )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0115_notificationpreference_changed_at_by"),
        ("membership", "0231_announcementdraft_instructors_audience"),
    ]

    operations = [migrations.RunPython(turn_on, turn_back_off)]
