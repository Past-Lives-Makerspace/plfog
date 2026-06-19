from __future__ import annotations

from django.db import migrations
from django.db.models import Count


def _multi_session_offering_ids(ClassSession):
    """PKs of offerings that have more than one scheduled session."""
    return list(
        ClassSession.objects.values("class_offering_id")
        .annotate(n=Count("id"))
        .filter(n__gt=1)
        .values_list("class_offering_id", flat=True)
    )


def backfill_series(apps, schema_editor):
    """Mark every offering with multiple sessions as a series package.

    Legacy classes (e.g. Blacksmithing 101's three hard dates) were imported as
    a single offering carrying all their dates, but predate the import rule that
    sets ``scheduling_type`` from the number of dates. This corrects those rows
    so multi-date classes render as the multi-session series they are.
    """
    ClassOffering = apps.get_model("classes", "ClassOffering")
    ClassSession = apps.get_model("classes", "ClassSession")
    ClassOffering.objects.filter(
        pk__in=_multi_session_offering_ids(ClassSession),
        scheduling_type="single_session",
    ).update(scheduling_type="series_package")


def revert_series(apps, schema_editor):
    """Reverse: send multi-session offerings back to single_session."""
    ClassOffering = apps.get_model("classes", "ClassOffering")
    ClassSession = apps.get_model("classes", "ClassSession")
    ClassOffering.objects.filter(
        pk__in=_multi_session_offering_ids(ClassSession),
        scheduling_type="series_package",
    ).update(scheduling_type="single_session")


class Migration(migrations.Migration):
    dependencies = [
        ("classes", "0035_classoffering_scheduling_type"),
    ]

    operations = [
        migrations.RunPython(backfill_series, revert_series),
    ]
