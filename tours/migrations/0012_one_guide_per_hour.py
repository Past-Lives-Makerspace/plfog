"""No booking waits for a guide's Confirm any more, and every guide sets their own notice (#1309, #1312).

Moves every guide to Confirm Automatically and, when any request is still waiting for its guide,
queues ``tours.tasks.book_waiting_tour_requests`` 20 minutes after the migration commits (past the
deploy, so the new release's worker runs it), which books each one as its guide would have (the
visitor's confirmation and invite, the guide's calendar event).

Adds each guide's own minimum notice in minutes (``TourGuideSettings.min_notice_minutes``, blank
meaning Tour Settings' default) and copies every saved notice into it, hours to minutes. The hours
column stays one release, unread, so the release still serving during the deploy keeps working.

The reverse (:func:`leave_guides_automatic`) cannot tell which guides were on Confirm First, since
the forward step kept no list, so it leaves every guide on Confirm Automatically and logs how many
guides and booked tours it leaves as they are; a request the task booked stays booked. It copies each
notice a guide set back into hours, rounded up, before the minutes column goes.
"""

import logging
from datetime import timedelta

import django.core.validators
from django.db import migrations, models

#: How long after the migration the booking task is due: past the deploy, so the new worker runs it.
RUN_AFTER_DEPLOY = timedelta(minutes=20)


def everyone_confirms_automatically(apps, schema_editor):
    """Every guide on Confirm Automatically; a request still waiting is booked by the queued task."""
    guide_settings = apps.get_model("tours", "TourGuideSettings")
    booking = apps.get_model("tours", "TourBooking")
    guide_settings.objects.filter(confirm_mode="first").update(confirm_mode="automatic")
    if booking.objects.filter(status="pending").exists():
        from django.utils import timezone

        from core.tasks import enqueue_at_on_commit
        from tours.tasks import book_waiting_tour_requests

        # Migrations run before the new release serves, while the task worker may still run the
        # last release's code, which has no such task. Due after the deploy settles, so the new
        # worker runs it.
        enqueue_at_on_commit(book_waiting_tour_requests, timezone.now() + RUN_AFTER_DEPLOY)


def leave_guides_automatic(apps, schema_editor):
    """Reverse of :func:`everyone_confirms_automatically`: undo nothing it cannot know, and say so.

    Which guides were on Confirm First, and which booked tours were requests, was never recorded,
    so no guide is moved back and no booking is turned back into a request. The log names how many
    guides stay on Confirm Automatically, so whoever rolls back can set Confirm First again by hand.
    """
    guide_settings = apps.get_model("tours", "TourGuideSettings")
    automatic = guide_settings.objects.filter(confirm_mode="automatic").count()
    logging.getLogger(__name__).warning(
        "tours 0012 reversed: %d guides stay on Confirm Automatically and any request it booked stays booked; "
        "set Confirm First again by hand where a guide had it.",
        automatic,
    )


def notice_into_minutes(apps, schema_editor):
    """Every saved notice, hours to minutes (#1312)."""
    guide_settings = apps.get_model("tours", "TourGuideSettings")
    guide_settings.objects.update(min_notice_minutes=models.F("min_notice_hours") * 60)


def notice_into_hours(apps, schema_editor):
    """Each notice a guide set, back into hours, rounded up and held to 0 to 336."""
    guide_settings = apps.get_model("tours", "TourGuideSettings")
    for row in guide_settings.objects.filter(min_notice_minutes__isnull=False):
        row.min_notice_hours = min(-(-row.min_notice_minutes // 60), 336)
        row.save(update_fields=["min_notice_hours"])


class Migration(migrations.Migration):
    dependencies = [
        ("tours", "0011_tourbooking_guest_answers"),
        ("django_tasks_database", "0021_conditional_partial_index_ordering"),
    ]

    operations = [
        migrations.RunPython(everyone_confirms_automatically, leave_guides_automatic),
        migrations.AddField(
            model_name="tourguidesettings",
            name="min_notice_minutes",
            field=models.PositiveIntegerField(
                blank=True,
                default=None,
                help_text=(
                    "How many minutes before a tour visitors on the website can still book this guide (#1312; 0 is "
                    "no notice). Blank: Tour Settings' default minimum notice. The kiosk and staff ignore it."
                ),
                null=True,
                validators=[django.core.validators.MaxValueValidator(20160, message="At most two weeks.")],
            ),
        ),
        migrations.RunPython(notice_into_minutes, notice_into_hours),
        migrations.AlterField(
            model_name="facilitytoursettings",
            name="min_notice_hours",
            field=models.PositiveSmallIntegerField(
                default=24,
                help_text=(
                    "The default minimum notice (#1312): how many hours before a tour visitors on the website can "
                    "still book it, for a guide who has not set their own on My Hours. Never a floor: a guide may "
                    "pick less or more."
                ),
                validators=[django.core.validators.MaxValueValidator(336, "Use 0 to 336 hours.")],
            ),
        ),
    ]
