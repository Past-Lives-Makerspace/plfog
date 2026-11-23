"""Copy every tour and orientation day off into ``hours.DayOff`` (#1300, part 1).

Each ``tours.TourDayOff`` is copied as it is (a range); each ``membership.OrientationDayOff`` is
copied as a one day range, unless one of the same person's tour days off already covers that
date. The old rows stay where they are and nothing reads the copies yet. The reverse deletes the
copies.
"""

from django.db import migrations


def copy_days_off(apps, schema_editor):
    DayOff = apps.get_model("hours", "DayOff")
    TourDayOff = apps.get_model("tours", "TourDayOff")
    OrientationDayOff = apps.get_model("membership", "OrientationDayOff")

    ranges = {}
    copies = []
    for day_off in TourDayOff.objects.all():
        ranges.setdefault(day_off.guide_id, []).append((day_off.starts_on, day_off.ends_on))
        copies.append(
            DayOff(member_id=day_off.guide_id, starts_on=day_off.starts_on, ends_on=day_off.ends_on, note=day_off.note)
        )
    for day_off in OrientationDayOff.objects.all():
        covered = any(first <= day_off.date <= last for first, last in ranges.get(day_off.orienter_id, []))
        if not covered:
            copies.append(
                DayOff(member_id=day_off.orienter_id, starts_on=day_off.date, ends_on=day_off.date, note=day_off.note)
            )
    DayOff.objects.bulk_create(copies)


def delete_copies(apps, schema_editor):
    apps.get_model("hours", "DayOff").objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [("hours", "0002_group_hours")]

    operations = [migrations.RunPython(copy_days_off, delete_copies)]
