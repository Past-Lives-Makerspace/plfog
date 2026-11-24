"""Group everyone's hours and copy everyone's days off again before part 2 reads them (#1300, part 2).

Part 1's backfills ran while the previous release still served, so a kind row or a day off the old
code wrote in that minute was never grouped or copied (``hours/AGENTS.md``, Deploy gap). Part 2's
readers take every day off from ``hours.DayOff``, so before they switch, this lets go of every set
of hours and every copy and makes them again from the kind rows and the old days off, the way
``0002`` and ``0003`` first did. No kind row's time changes, so no slot, window or tour moves and
nobody is emailed. Nothing references a set of hours or a day off by its id yet (My Hours is part
3), so new ids lose nothing. Running it backwards does the same again: the result is the state
the forward run leaves, which is also the state before it with the deploy gap filled.
"""

from importlib import import_module

from django.apps.registry import Apps
from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor

_group = import_module("hours.migrations.0002_group_hours")
_copy = import_module("hours.migrations.0003_copy_days_off")


def regroup_everyone(apps: Apps, schema_editor: BaseDatabaseSchemaEditor | None) -> None:
    _group.ungroup_hours(apps, schema_editor)
    _group.group_hours(apps, schema_editor)
    _copy.delete_copies(apps, schema_editor)
    _copy.copy_days_off(apps, schema_editor)


class Migration(migrations.Migration):
    dependencies = [("hours", "0003_copy_days_off")]

    operations = [migrations.RunPython(regroup_everyone, regroup_everyone)]
