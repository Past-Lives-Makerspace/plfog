"""class_published now broadcasts to Discord with an ABSOLUTE (book) URL + curated copy.

Publishing an offering used to ring only the in-app bell with a relative ``/classes/…``
link. It now also posts one central Discord embed, links are absolute (BOOK_BASE_URL),
the in-app row + embed render from the curated copy, and the email channel stays OFF.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.conf import settings
from django.utils import timezone

from classes.factories import ClassOfferingFactory, UserFactory
from classes.models import ClassApproval, ClassOffering
from core.models import Notification, TransactionalEmailLog

pytestmark = pytest.mark.django_db


def _active_member_user():
    """An *activated* User (``last_login`` set) the signal links to a fresh ACTIVE Member.

    Activated so the broadcast resolvers address it — the spine skips members who have
    never signed in.
    """
    return UserFactory(last_login=timezone.now())


def _publish(offering: ClassOffering) -> None:
    """Drive an offering DRAFT → PENDING → PUBLISHED through the approval pathway."""
    offering.status = ClassOffering.Status.PENDING
    offering.save(update_fields=["status", "updated_at"])
    approval = ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.ADMIN)
    approval.decide(ClassApproval.Decision.APPROVED, user=UserFactory())
    offering.refresh_from_db()


def describe_class_published_discord_broadcast():
    def it_posts_one_central_embed_with_an_absolute_book_url():
        _active_member_user()
        offering = ClassOfferingFactory(status=ClassOffering.Status.DRAFT, title="Forge Night")
        with patch("core.events.discord.post_embed", return_value=True) as mock_post:
            _publish(offering)
        assert mock_post.call_count == 1
        message = mock_post.call_args[0][1]
        assert message.url.startswith(settings.BOOK_BASE_URL)
        assert not message.url.startswith("/")

    def it_does_not_post_to_a_guild_webhook():
        # class_published is site-wide — its emit carries no ``guild`` context, so the
        # dual-route guild branch never fires. Exactly one central post, never a second.
        _active_member_user()
        offering = ClassOfferingFactory(status=ClassOffering.Status.DRAFT, title="Glass Day")
        with patch("core.events.discord.post_embed", return_value=True) as mock_post:
            _publish(offering)
        assert mock_post.call_count == 1

    def it_renders_the_in_app_row_from_the_curated_copy():
        recipient = _active_member_user()
        offering = ClassOfferingFactory(status=ClassOffering.Status.DRAFT, title="Casting 101")
        Notification.objects.all().delete()
        _publish(offering)
        note = Notification.objects.get(trigger="class_published", user=recipient)
        assert note.title == "New class: Casting 101"

    def it_does_not_email_because_class_published_email_defaults_off():
        _active_member_user()
        offering = ClassOfferingFactory(status=ClassOffering.Status.DRAFT, title="No Email Class")
        _publish(offering)
        assert not TransactionalEmailLog.objects.filter(trigger_kind="class_published").exists()
