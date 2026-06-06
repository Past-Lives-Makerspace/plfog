"""Add ClassApproval + backfill a synthetic admin-approved row for every
currently-PUBLISHED class so the audit trail is non-empty going forward."""

from __future__ import annotations

import secrets

from django.conf import settings
from django.db import migrations, models


def backfill_synthetic_admin_approvals(apps, schema_editor):
    ClassOffering = apps.get_model("classes", "ClassOffering")
    ClassApproval = apps.get_model("classes", "ClassApproval")
    for offering in ClassOffering.objects.filter(status="published"):
        if ClassApproval.objects.filter(class_offering=offering, role="admin").exists():
            continue
        ClassApproval.objects.create(
            class_offering=offering,
            role="admin",
            decision="approved",
            decided_by=offering.approved_by,
            notes="Backfilled: this class was already published before the dual-approval workflow shipped.",
            token=secrets.token_urlsafe(32),
            decided_at=offering.published_at or offering.updated_at,
        )


def drop_synthetic_admin_approvals(apps, schema_editor):
    ClassApproval = apps.get_model("classes", "ClassApproval")
    ClassApproval.objects.filter(notes__startswith="Backfilled:").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("classes", "0025_discount_code_per_class"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ClassApproval",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "role",
                    models.CharField(
                        choices=[("admin", "Admin"), ("guild_lead", "Guild Lead")],
                        help_text="Which reviewer gate this row represents.",
                        max_length=20,
                    ),
                ),
                (
                    "decision",
                    models.CharField(
                        blank=True,
                        choices=[
                            ("approved", "Approved"),
                            ("changes_requested", "Changes Requested"),
                            ("denied", "Denied"),
                        ],
                        default="",
                        help_text="Reviewer's verdict; empty means still pending.",
                        max_length=20,
                    ),
                ),
                (
                    "notes",
                    models.TextField(blank=True, help_text="Reviewer comments shown to the instructor."),
                ),
                (
                    "token",
                    models.CharField(
                        db_index=True,
                        help_text="Random token used in the emailed /classes/review/<token>/ link.",
                        max_length=64,
                        unique=True,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, help_text="When the review was requested.")),
                ("decided_at", models.DateTimeField(blank=True, help_text="When the reviewer acted.", null=True)),
                (
                    "class_offering",
                    models.ForeignKey(
                        help_text="The class submission this review row gates.",
                        on_delete=models.deletion.CASCADE,
                        related_name="approvals",
                        to="classes.classoffering",
                    ),
                ),
                (
                    "decided_by",
                    models.ForeignKey(
                        blank=True,
                        help_text="Authenticated user who recorded the decision, when known.",
                        null=True,
                        on_delete=models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
                "indexes": [
                    models.Index(fields=["class_offering", "role"], name="classes_cla_class_o_50fe07_idx"),
                ],
            },
        ),
        migrations.RunPython(backfill_synthetic_admin_approvals, drop_synthetic_admin_approvals),
    ]
