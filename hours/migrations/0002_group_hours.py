"""Group each person's tour hours and personal orientation hours into sets of hours (#1300, part 1).

Every ``tours.TourHours`` row and every ``membership.OrientationAvailability`` row with an
orientor is grouped with that person's other rows of the identical time (date or weekday,
cadence, start day, last of the month, From, Until), and each group gets one ``hours.Hours``.
Only the rows' ``hours`` key is written: no row's time changes, so no slot or open window is
made, moved or retired and nobody is emailed. The shared orientation rows (no orientor) keep no
set. Self-contained on purpose: a migration never imports app code that may change after it.
The reverse lets go of every key and deletes the sets.
"""

from django.apps.registry import Apps
from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor


def group_hours(apps: Apps, schema_editor: BaseDatabaseSchemaEditor | None) -> None:
    Hours = apps.get_model("hours", "Hours")
    TourHours = apps.get_model("tours", "TourHours")
    OrientationAvailability = apps.get_model("membership", "OrientationAvailability")

    groups = {}
    for row in TourHours.objects.all():
        key = (row.guide_id, row.date, row.weekday, "weekly", None, False, row.start_time, row.end_time)
        groups.setdefault(key, ([], []))[0].append(row.pk)
    for row in OrientationAvailability.objects.filter(orienter__isnull=False):
        key = (
            row.orienter_id,
            None,
            row.weekday,
            row.cadence,
            row.anchor_date,
            row.last_of_month,
            row.start_time,
            row.end_time,
        )
        groups.setdefault(key, ([], []))[1].append(row.pk)
    for key, (tour_pks, orientation_pks) in groups.items():
        member_id, date, weekday, cadence, anchor_date, last_of_month, start_time, end_time = key
        hours = Hours.objects.create(
            member_id=member_id,
            date=date,
            weekday=weekday,
            cadence=cadence,
            anchor_date=anchor_date,
            last_of_month=last_of_month,
            start_time=start_time,
            end_time=end_time,
        )
        TourHours.objects.filter(pk__in=tour_pks).update(hours=hours)
        OrientationAvailability.objects.filter(pk__in=orientation_pks).update(hours=hours)


def ungroup_hours(apps: Apps, schema_editor: BaseDatabaseSchemaEditor | None) -> None:
    apps.get_model("tours", "TourHours").objects.update(hours=None)
    apps.get_model("membership", "OrientationAvailability").objects.update(hours=None)
    apps.get_model("hours", "Hours").objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ("hours", "0001_initial"),
        ("membership", "0292_orientationavailability_hours"),
        ("tours", "0013_tourhours_hours"),
    ]

    operations = [migrations.RunPython(group_hours, ungroup_hours)]
