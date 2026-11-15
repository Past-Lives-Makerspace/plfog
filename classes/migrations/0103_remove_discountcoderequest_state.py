"""Forget DiscountCodeRequest (#1185) in Django's state only; the table stays for one release.

Deploys run migrations while the previous release still serves (STANDARDS.md section 10), and
that release still reads this table. Nothing in the new code touches it and production holds no
rows, so leaving it costs nothing, with one exception: its foreign keys. A table Django no longer
knows still referencing classes, members, users and codes makes a flush (TRUNCATE on PostgreSQL)
refuse those tables, so the database half drops the four constraints and keeps the columns. The
follow-up to #1185 drops the table with RunSQL once this release is live.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("classes", "0102_classoffering_minimum_signups"),
        ("membership", "0282_seed_grant_guides"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[migrations.DeleteModel(name="DiscountCodeRequest")],
            database_operations=[
                migrations.AlterField(
                    model_name="discountcoderequest",
                    name="class_offering",
                    field=models.ForeignKey(
                        db_constraint=False,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="discount_code_requests",
                        to="classes.classoffering",
                    ),
                ),
                migrations.AlterField(
                    model_name="discountcoderequest",
                    name="requested_by",
                    field=models.ForeignKey(
                        db_constraint=False,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="discount_code_requests",
                        to="membership.member",
                    ),
                ),
                migrations.AlterField(
                    model_name="discountcoderequest",
                    name="decided_by",
                    field=models.ForeignKey(
                        blank=True,
                        db_constraint=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                migrations.AlterField(
                    model_name="discountcoderequest",
                    name="discount_code",
                    field=models.OneToOneField(
                        blank=True,
                        db_constraint=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="request",
                        to="classes.discountcode",
                    ),
                ),
            ],
        ),
    ]
