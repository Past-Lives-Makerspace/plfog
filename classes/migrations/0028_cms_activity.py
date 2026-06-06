"""Create CmsActivity + backfill from existing ClassOffering / Registration /
ClassApproval / DiscountCode rows so the new Activity tab isn't empty on
day one. The backfill is best-effort and idempotent enough to re-run via
the reverse arrow."""

from __future__ import annotations

from django.conf import settings
from django.db import migrations, models


def _ts(value, fallback):
    return value or fallback


def _backfill_registration_events(reg, CmsActivity):
    if reg.status == "waitlisted":
        CmsActivity.objects.create(
            kind="waitlist_joined",
            class_offering=reg.class_offering,
            registration=reg,
            actor=None,
            payload={},
            created_at=reg.registered_at,
        )
    else:
        CmsActivity.objects.create(
            kind="registration_created",
            class_offering=reg.class_offering,
            registration=reg,
            actor=None,
            payload={},
            created_at=reg.registered_at,
        )
    if reg.status == "confirmed" and reg.confirmed_at:
        CmsActivity.objects.create(
            kind="registration_confirmed",
            class_offering=reg.class_offering,
            registration=reg,
            actor=None,
            payload={},
            created_at=reg.confirmed_at,
        )
    if reg.status == "cancelled" and reg.cancelled_at:
        CmsActivity.objects.create(
            kind="registration_cancelled",
            class_offering=reg.class_offering,
            registration=reg,
            actor=None,
            payload={"reason": reg.cancellation_reason} if reg.cancellation_reason else {},
            created_at=reg.cancelled_at,
        )
    if reg.status == "refunded":
        CmsActivity.objects.create(
            kind="registration_refunded",
            class_offering=reg.class_offering,
            registration=reg,
            actor=None,
            payload={},
            created_at=_ts(reg.cancelled_at, reg.registered_at),
        )


def backfill_activity(apps, schema_editor):
    ClassOffering = apps.get_model("classes", "ClassOffering")
    Registration = apps.get_model("classes", "Registration")
    DiscountCode = apps.get_model("classes", "DiscountCode")
    CmsActivity = apps.get_model("classes", "CmsActivity")

    # Class lifecycle events
    for offering in ClassOffering.objects.all():
        CmsActivity.objects.create(
            kind="class_created",
            class_offering=offering,
            actor=None,
            payload={},
            created_at=offering.created_at,
        )
        if offering.status == "published" and offering.published_at:
            CmsActivity.objects.create(
                kind="class_published",
                class_offering=offering,
                actor=offering.approved_by,
                payload={},
                created_at=offering.published_at,
            )
        elif offering.status == "archived":
            CmsActivity.objects.create(
                kind="class_archived",
                class_offering=offering,
                actor=None,
                payload={},
                created_at=offering.updated_at,
            )

    for reg in Registration.objects.all():
        _backfill_registration_events(reg, CmsActivity)

    # Discount codes created in the past
    for code in DiscountCode.objects.all():
        CmsActivity.objects.create(
            kind="discount_code_created",
            class_offering=code.class_offering,
            actor=code.created_by,
            payload={"code": code.code, "auto_apply": code.auto_apply},
            created_at=code.created_at,
        )


def drop_backfilled_activity(apps, schema_editor):
    CmsActivity = apps.get_model("classes", "CmsActivity")
    CmsActivity.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("classes", "0027_waitlist"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="CmsActivity",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "kind",
                    models.CharField(
                        choices=[
                            ("class_created", "Class created"),
                            ("class_submitted", "Submitted for review"),
                            ("class_approved", "Approved"),
                            ("class_changes_requested", "Changes requested"),
                            ("class_denied", "Declined"),
                            ("class_published", "Published"),
                            ("class_archived", "Archived"),
                            ("registration_created", "Registered"),
                            ("registration_confirmed", "Payment confirmed"),
                            ("registration_cancelled", "Cancelled"),
                            ("registration_refunded", "Refunded"),
                            ("waitlist_joined", "Joined waitlist"),
                            ("waitlist_notified", "Notified of open spot"),
                            ("waitlist_left", "Left waitlist"),
                            ("discount_code_created", "Discount code created"),
                            ("discount_code_redeemed", "Discount code redeemed"),
                        ],
                        help_text="What happened.",
                        max_length=40,
                    ),
                ),
                (
                    "payload",
                    models.JSONField(
                        blank=True,
                        default=dict,
                        help_text=(
                            "Free-form per-kind detail: discount code, notes excerpt, refund "
                            "amount, etc. The feed UI is the only consumer."
                        ),
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                (
                    "class_offering",
                    models.ForeignKey(
                        blank=True,
                        help_text="Class this event belongs to, when applicable.",
                        null=True,
                        on_delete=models.deletion.SET_NULL,
                        related_name="activity",
                        to="classes.classoffering",
                    ),
                ),
                (
                    "registration",
                    models.ForeignKey(
                        blank=True,
                        help_text="Registration this event belongs to, when applicable.",
                        null=True,
                        on_delete=models.deletion.SET_NULL,
                        related_name="activity",
                        to="classes.registration",
                    ),
                ),
                (
                    "actor",
                    models.ForeignKey(
                        blank=True,
                        help_text="User who triggered this. Null for system or anonymous events.",
                        null=True,
                        on_delete=models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name_plural": "CMS activity",
                "ordering": ["-created_at"],
                "indexes": [
                    models.Index(fields=["-created_at"], name="classes_cms_created_d18bca_idx"),
                    models.Index(fields=["class_offering", "-created_at"], name="classes_cms_class_o_a3fc4f_idx"),
                    models.Index(fields=["kind", "-created_at"], name="classes_cms_kind_c0eb3e_idx"),
                ],
            },
        ),
        migrations.RunPython(backfill_activity, drop_backfilled_activity),
    ]
