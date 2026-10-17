"""Post each class review that has waited 3 days or more to the member portal alerts channel (#817).

Registered in ``core/scheduled_jobs.py`` as a DAILY job. One plain Discord post per review per
3 days of waiting: the period is the gate plus ``days_waiting // 3``, so a re-run the same day
posts nothing and a skipped day never loses a post. The channel is the ``class_review_overdue``
event's ``DiscordWebhookRoute`` row.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.urls import reverse

from classes.overdue import OVERDUE_AFTER_DAYS, OverdueReview, overdue_reviews
from core.events.emit import emit

EVENT_KEY = "class_review_overdue"


def _starts_text(row: OverdueReview) -> str:
    if row.dates_passed:
        return "Its dates have passed"
    if row.days_until_start is None:
        return "It has no dates yet"
    if row.days_until_start <= 0:
        return "Its first session is today"
    return f"First session in {row.days_until_start} day{'s' if row.days_until_start != 1 else ''}"


def post_overdue_review(row: OverdueReview) -> bool:
    """Post one overdue review; True when it went out, False when this 3 day stretch already posted."""
    offering = row.offering
    guild = offering.category.guild if offering.category_id else None
    path = reverse("classes:admin_class_review", kwargs={"pk": offering.pk})
    result = emit(
        EVENT_KEY,
        target=offering,
        context={
            "class_title": offering.title,
            "instructor_names": offering.instructor_names or "an instructor",
            "guild_name": guild.name if guild is not None else "no guild",
            "waiting_on": row.waiting_on,
            "days_waiting": str(row.days_waiting),
            "starts_text": _starts_text(row),
            "review_url": f"{settings.MEMBER_BASE_URL.rstrip('/')}{path}",
        },
        period=f"gate:{row.gate.pk}:overdue:{row.days_waiting // OVERDUE_AFTER_DAYS}",
    )
    return bool(result.delivered)


def alerts_channel_routed() -> bool:
    """Whether the event has its own enabled Discord route.

    Without one the post would fall back to the global notify webhook and spend its 3 day slot
    there, so the job posts nothing until the alerts channel route exists.
    """
    from core.models import DiscordWebhookRoute

    return DiscordWebhookRoute.objects.filter(event_key=EVENT_KEY, is_enabled=True).exclude(webhook_url="").exists()


class Command(BaseCommand):
    help = "Post class reviews waiting 3 days or more to the member portal alerts Discord channel."

    def handle(self, *args: Any, **options: Any) -> None:
        rows = overdue_reviews()
        if not alerts_channel_routed():
            raise CommandError(
                f"{len(rows)} overdue review(s), none posted: no enabled Discord route for {EVENT_KEY}. "
                "Add one under Admin > Discord webhook routes."
            )
        posted = sum(post_overdue_review(row) for row in rows)
        self.stdout.write(f"{len(rows)} overdue review(s); {posted} posted.")
