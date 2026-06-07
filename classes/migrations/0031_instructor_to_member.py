"""Migrate ClassOffering/InstructorMessage instructor FKs from Instructor to Member.

Data migration strategy:
1. Copy slug/website/social_handle from each Instructor to its linked Member.
2. Store Member PKs in temp int columns (no FK constraint → no violation).
3. Drop old FK columns pointing to Instructor.
4. Add new FK columns pointing to Member.
5. Populate new FKs from temp columns.
6. Drop temp columns.
7. Delete the Instructor table.
"""

from __future__ import annotations

import django.db.models.deletion
from django.db import migrations, models


def _copy_instructor_to_member(apps, schema_editor):  # noqa: ANN001, C901
    Instructor = apps.get_model("classes", "Instructor")
    Member = apps.get_model("membership", "Member")
    ClassOffering = apps.get_model("classes", "ClassOffering")
    InstructorMessage = apps.get_model("classes", "InstructorMessage")

    # Build Instructor.pk → Member.pk mapping; copy profile fields over.
    pk_map: dict[int, int] = {}
    for inst in Instructor.objects.select_related("user").iterator():
        try:
            member = Member.objects.get(user=inst.user)
        except Member.DoesNotExist:
            continue
        pk_map[inst.pk] = member.pk
        if not member.instructor_slug:
            member.instructor_slug = inst.slug
        if not member.instructor_website:
            member.instructor_website = inst.website
        if not member.instructor_social_handle:
            member.instructor_social_handle = inst.social_handle
        member.save(update_fields=["instructor_slug", "instructor_website", "instructor_social_handle"])

    for offering in ClassOffering.objects.filter(instructor__isnull=False).iterator():
        if offering.instructor_id in pk_map:
            offering.temp_instructor_pk = pk_map[offering.instructor_id]
            offering.save(update_fields=["temp_instructor_pk"])

    for offering in ClassOffering.objects.filter(created_by__isnull=False).iterator():
        if offering.created_by_id in pk_map:
            offering.temp_created_by_pk = pk_map[offering.created_by_id]
            offering.save(update_fields=["temp_created_by_pk"])

    for msg in InstructorMessage.objects.filter(instructor__isnull=False).iterator():
        if msg.instructor_id in pk_map:
            msg.temp_instructor_pk = pk_map[msg.instructor_id]
            msg.save(update_fields=["temp_instructor_pk"])


def _populate_new_fks(apps, schema_editor):  # noqa: ANN001
    ClassOffering = apps.get_model("classes", "ClassOffering")
    InstructorMessage = apps.get_model("classes", "InstructorMessage")

    for offering in ClassOffering.objects.filter(temp_instructor_pk__isnull=False).iterator():
        offering.instructor_id = offering.temp_instructor_pk
        offering.save(update_fields=["instructor_id"])

    for offering in ClassOffering.objects.filter(temp_created_by_pk__isnull=False).iterator():
        offering.created_by_id = offering.temp_created_by_pk
        offering.save(update_fields=["created_by_id"])

    for msg in InstructorMessage.objects.filter(temp_instructor_pk__isnull=False).iterator():
        msg.instructor_id = msg.temp_instructor_pk
        msg.save(update_fields=["instructor_id"])


class Migration(migrations.Migration):
    # RunPython commits row updates to classes_classoffering, which fires deferred FK
    # triggers. PostgreSQL refuses ALTER TABLE while those are pending — so this
    # migration must not run in a single transaction.
    atomic = False

    dependencies = [
        ("classes", "0030_member_instructor_fields"),
        ("membership", "0032_member_instructor_fields"),
    ]

    operations = [
        # Step 1: copy Instructor → Member data, fill temp columns
        migrations.RunPython(_copy_instructor_to_member, migrations.RunPython.noop),
        # Step 2: drop old FK columns (pointing to classes_instructor)
        migrations.RemoveField(model_name="classoffering", name="instructor"),
        migrations.RemoveField(model_name="classoffering", name="created_by"),
        migrations.RemoveField(model_name="instructormessage", name="instructor"),
        # Step 3: add new FK columns pointing to membership_member
        migrations.AddField(
            model_name="classoffering",
            name="instructor",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="classes",
                to="membership.member",
                help_text="Member who teaches this class.",
            ),
        ),
        migrations.AddField(
            model_name="classoffering",
            name="created_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to="membership.member",
                help_text="Member who authored this class.",
            ),
        ),
        migrations.AddField(
            model_name="instructormessage",
            name="instructor",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="sent_messages",
                to="membership.member",
                help_text="The member/instructor who sent this, or NULL if sent by an admin.",
            ),
        ),
        # Step 4: populate new FKs from temp columns
        migrations.RunPython(_populate_new_fks, migrations.RunPython.noop),
        # Step 5: drop temp bridge columns
        migrations.RemoveField(model_name="classoffering", name="temp_instructor_pk"),
        migrations.RemoveField(model_name="classoffering", name="temp_created_by_pk"),
        migrations.RemoveField(model_name="instructormessage", name="temp_instructor_pk"),
        # Step 6: delete the Instructor table
        migrations.DeleteModel(name="Instructor"),
    ]
