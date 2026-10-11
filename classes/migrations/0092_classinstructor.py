"""A class can have more than one instructor (#686, part 1).

Adds the ``ClassInstructor`` rows behind ``ClassOffering.instructors`` and copies every
class's current ``instructor`` into them, stamped with the class's ``created_at`` so the one
instructor a class had reads as its first. ``instructor`` keeps its column as the billing
payee; only its help text changes.

Add only, so the previous release keeps serving while this applies (STANDARDS.md section 10):
old code never reads the new table, and a class it saves without a row is seeded one by
``ClassOffering.save`` on its next save under the new code.

Reverse writes each class's first listed instructor back to ``instructor`` before the table
goes, so a rollback keeps the payee the new code had chosen.
"""

from __future__ import annotations

from typing import Any

import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


def backfill_instructor_rows(apps: Any, schema_editor: Any) -> None:
    ClassOffering = apps.get_model("classes", "ClassOffering")
    ClassInstructor = apps.get_model("classes", "ClassInstructor")
    ClassInstructor.objects.bulk_create(
        ClassInstructor(class_offering_id=pk, member_id=instructor_id, added_at=created_at)
        for pk, instructor_id, created_at in ClassOffering.objects.filter(instructor__isnull=False)
        .order_by("pk")
        .values_list("pk", "instructor_id", "created_at")
    )


def restore_payee_from_rows(apps: Any, schema_editor: Any) -> None:
    ClassOffering = apps.get_model("classes", "ClassOffering")
    ClassInstructor = apps.get_model("classes", "ClassInstructor")
    first_by_class: dict[int, int] = {}
    for class_id, member_id in ClassInstructor.objects.order_by("added_at", "pk").values_list(
        "class_offering_id", "member_id"
    ):
        first_by_class.setdefault(class_id, member_id)
    for class_id, member_id in first_by_class.items():
        ClassOffering.objects.filter(pk=class_id).exclude(instructor_id=member_id).update(instructor_id=member_id)


class Migration(migrations.Migration):
    dependencies = [
        ("classes", "0091_discountcode_instructor"),
        ("membership", "0226_guildorientationsettings_booking_notice_hours"),
    ]

    operations = [
        migrations.AlterField(
            model_name="classoffering",
            name="instructor",
            field=models.ForeignKey(
                blank=True,
                help_text=(
                    "The member billing credits for this class: always the first listed instructor (#686). "
                    "Set through the instructors list; a direct assignment is overwritten on save."
                ),
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="classes",
                to="membership.member",
            ),
        ),
        migrations.CreateModel(
            name="ClassInstructor",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "added_at",
                    models.DateTimeField(
                        default=django.utils.timezone.now,
                        help_text=(
                            "When this member was added to the class; the list is in this order, the first is the payee."
                        ),
                    ),
                ),
                (
                    "class_offering",
                    models.ForeignKey(
                        help_text="The class this member teaches.",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="instructor_rows",
                        to="classes.classoffering",
                    ),
                ),
                (
                    "member",
                    models.ForeignKey(
                        help_text="The member who teaches it. PROTECT: a member on a class cannot be deleted.",
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="instructor_rows",
                        to="membership.member",
                    ),
                ),
            ],
            options={
                "ordering": ["added_at", "pk"],
            },
        ),
        migrations.AddField(
            model_name="classoffering",
            name="instructors",
            field=models.ManyToManyField(
                blank=True,
                help_text=(
                    "Everyone who teaches this class, in the order added (#686). Each one gets the instructor's "
                    "access to the class; the first is the billing payee."
                ),
                related_name="taught_classes",
                through="classes.ClassInstructor",
                to="membership.member",
            ),
        ),
        migrations.AddConstraint(
            model_name="classinstructor",
            constraint=models.UniqueConstraint(
                fields=("class_offering", "member"),
                name="cls_instructor_once_per_class",
            ),
        ),
        migrations.RunPython(backfill_instructor_rows, reverse_code=restore_payee_from_rows),
    ]
