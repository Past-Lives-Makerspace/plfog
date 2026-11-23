"""Each tour hour belongs to one guide, and no booking waits for a guide's Confirm (#1309).

Adds Tour Settings' ``guides_share_hours`` switch (off), moves every guide to Confirm Automatically,
and, when any request is still waiting for its guide, queues ``tours.tasks.book_waiting_tour_requests``
20 minutes after the migration commits (past the deploy, so the new release's worker runs it), which books each one as its guide would have (the visitor's confirmation
and invite, the guide's calendar event).

The reverse drops the column and leaves every guide on Confirm Automatically: nobody can pick Confirm
First any more, so there is no earlier choice to put back, and a request the task booked stays booked.
"""

from datetime import timedelta

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


class Migration(migrations.Migration):
    dependencies = [
        ("tours", "0010_tourbooking_proposal"),
        ("django_tasks_database", "0021_conditional_partial_index_ordering"),
    ]

    operations = [
        migrations.AddField(
            model_name="facilitytoursettings",
            name="guides_share_hours",
            field=models.BooleanField(
                db_default=False,
                default=False,
                help_text=(
                    "Off (the default, #1309): each tour hour belongs to one guide, a guide cannot post hours another "
                    "guide already has, and visitors pick a time with no guide named. On: guides may post the same "
                    "hours, and visitors pick a guide or No preference for a time two guides share (#1255)."
                ),
            ),
        ),
        migrations.RunPython(everyone_confirms_automatically, migrations.RunPython.noop),
    ]
