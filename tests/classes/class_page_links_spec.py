"""BDD specs: class emails, Discord posts and other outbound links reach the class page (#1261, part 2 of 3).

Since #1260 a class has one permanent page, ``/classes/<class>/``, and a run's own old address
only answers with a redirect to it. These pin each outbound builder to the class page, using a
run whose own slug differs from its class's, so a builder that slipped back to the run's
address would fail here rather than quietly costing every reader a redirect.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from classes.factories import (
    CategoryFactory,
    ClassOfferingFactory,
    ClassSessionFactory,
    InstructorFactory,
    RegistrationFactory,
    UserFactory,
)
from classes.models import ClassOffering, Registration
from core.urls_util import book_absolute_url

pytestmark = pytest.mark.django_db

CLASS_SLUG = "qzx-glass-pour"
RUN_SLUG = "qzx-glass-pour-march-run"


def _live_run(*, slug: str = RUN_SLUG, days: int = 3) -> ClassOffering:
    """A published run with a date in the coming week, filed under a class whose address is not the run's."""
    offering = ClassOfferingFactory(
        title="Qzx Glass Pour",
        slug=slug,
        status=ClassOffering.Status.PUBLISHED,
        instructor=InstructorFactory(user=UserFactory()),
        category=CategoryFactory(name="Qzx Glass"),
    )
    starts = timezone.now() + timedelta(days=days)
    ClassSessionFactory(class_offering=offering, starts_at=starts, ends_at=starts + timedelta(hours=2))
    offering.refresh_from_db()
    return offering


@pytest.fixture
def run() -> ClassOffering:
    offering = _live_run()
    type(offering.class_record).objects.filter(pk=offering.class_record_id).update(slug=CLASS_SLUG)
    offering.refresh_from_db()
    assert offering.slug != offering.class_record.slug
    return offering


@pytest.fixture
def class_url() -> str:
    return book_absolute_url(reverse("classes:public_class_detail", kwargs={"slug": CLASS_SLUG}))


def describe_discord():
    def it_links_the_class_page_in_the_new_class_post(run, class_url):
        from hub.discord_class_posts import class_announcement_embeds

        assert class_announcement_embeds(run)[0]["url"] == class_url

    def it_links_the_class_page_in_the_weekly_digest(run, class_url):
        from hub.discord_class_posts import build_weekly_classes_digest_embeds

        text = "\n".join(embed.get("description", "") for embed in build_weekly_classes_digest_embeds(timezone.now()))
        assert f"]({class_url})" in text
        assert RUN_SLUG not in text

    def it_builds_the_digest_without_a_query_per_class(run):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        from hub.discord_class_posts import build_weekly_classes_digest_embeds

        with CaptureQueriesContext(connection) as one:
            build_weekly_classes_digest_embeds(timezone.now())
        for n in range(3):
            _live_run(slug=f"qzx-extra-run-{n}", days=2 + n)
        with CaptureQueriesContext(connection) as four:
            build_weekly_classes_digest_embeds(timezone.now())
        assert len(four.captured_queries) == len(one.captured_queries)


def describe_emails():
    def it_links_the_class_page_in_the_registration_confirmation(run, class_url):
        from classes.emails import send_registration_confirmation

        user = UserFactory()
        registration = RegistrationFactory(
            class_offering=run, member=user.member, email=user.email, status=Registration.Status.CONFIRMED
        )
        mail.outbox.clear()
        send_registration_confirmation(registration)
        assert len(mail.outbox) == 1
        assert class_url in mail.outbox[0].body
        assert f"/classes/{RUN_SLUG}/" not in mail.outbox[0].body

    def it_links_the_class_page_in_the_waitlist_confirmation(run, class_url):
        from classes.emails import send_waitlist_joined_confirmation

        registration = RegistrationFactory(
            class_offering=run, email="qzx-waiting@example.com", status=Registration.Status.WAITLISTED
        )
        mail.outbox.clear()
        send_waitlist_joined_confirmation(registration)
        assert len(mail.outbox) == 1
        assert class_url in mail.outbox[0].body


def describe_other_surfaces():
    def it_prints_the_class_page_on_the_instagram_image(run, class_url):
        from classes.instagram import instagram_copy

        assert instagram_copy(run).address == class_url.split("://", 1)[-1].rstrip("/")

    def it_sends_the_member_tour_to_the_class_page_not_the_runs_old_address(run):
        from core.tours import _demo_class_slug

        assert _demo_class_slug(run.instructor) == {"slug": CLASS_SLUG}
