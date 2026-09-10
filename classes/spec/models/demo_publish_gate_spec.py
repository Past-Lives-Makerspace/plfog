"""BDD specs for the demo-content gate on the class-published Discord broadcast.

A demo class exists so a walkthrough can be run end to end on production, final
approval included. Publishing one used to post it to the makerspace-wide Discord
channel alongside the real catalog, telling 200 members about a workshop that does
not exist. ``ClassOffering.publish`` now suppresses the BROADCAST for demo content
only — the in-app row and the email fan-out are untouched, so the flow a demo is
meant to show still happens.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone

from classes.factories import ClassImageFactory, ClassOfferingFactory
from classes.models import ClassOffering, ClassSession

pytestmark = pytest.mark.django_db


def _publishable(**kwargs: object) -> ClassOffering:
    """A DRAFT offering that passes every readiness check, so ``publish`` can run."""
    offering = ClassOfferingFactory(
        status=ClassOffering.Status.DRAFT,
        capacity=6,
        description="A real description, long enough to clear the readiness threshold for a class page.",
        **kwargs,
    )
    ClassImageFactory(class_offering=offering)
    starts = timezone.now() + timedelta(days=21)
    ClassSession.objects.create(class_offering=offering, starts_at=starts, ends_at=starts + timedelta(hours=2))
    return offering


def _patch_post() -> "AbstractContextManager[MagicMock]":
    """Patch the spine's outbound Discord POST so call counts can be asserted."""
    return patch("core.events.discord.post_embed", return_value=True)


def describe_is_demo():
    def it_is_true_for_a_demo_slug():
        assert ClassOfferingFactory(slug="demo-map-compass", title="Compass Basics").is_demo is True

    def it_is_true_for_a_bracketed_demo_title():
        assert ClassOfferingFactory(slug="compass-basics", title="[DEMO] Compass Basics").is_demo is True

    def it_ignores_the_case_and_the_leading_whitespace_of_the_title():
        assert ClassOfferingFactory(slug="c-b", title="  [demo] Compass Basics").is_demo is True

    def it_is_false_for_an_ordinary_class():
        assert ClassOfferingFactory(slug="compass-basics", title="Compass Basics").is_demo is False

    def it_does_not_match_a_class_that_merely_mentions_a_demo():
        """The marker is a prefix, not a substring — "Live Demo Day" is a real class."""
        assert ClassOfferingFactory(slug="live-demo-day", title="Live Demo Day").is_demo is False


def describe_publish():
    def it_does_not_post_a_demo_class_to_discord():
        offering = _publishable(slug="demo-map-watercolor", title="[DEMO] Watercolor Washes")
        with _patch_post() as mock_post:
            offering.publish(actor=None)
        assert mock_post.call_count == 0
        offering.refresh_from_db()
        assert offering.status == ClassOffering.Status.PUBLISHED

    def it_still_posts_a_real_class_to_discord():
        offering = _publishable(slug="real-watercolor", title="Watercolor Washes")
        with _patch_post() as mock_post:
            offering.publish(actor=None)
        assert mock_post.call_count == 1

    def it_still_rings_the_bell_for_a_demo_class():
        """Only the broadcast is held back; the in-app fan-out is what a walkthrough shows."""
        from django.contrib.auth.models import User

        from core.models import Notification

        # A linked, ACTIVE, ACTIVATED member: the class_published resolver fans out to
        # users who have signed in at least once, so a userless (or never-logged-in) row
        # would resolve to nobody and prove nothing.
        user = User.objects.create_user(username="bell@example.com", email="bell@example.com", password="pw")
        user.last_login = timezone.now()
        user.save(update_fields=["last_login"])
        offering = _publishable(slug="demo-bell", title="[DEMO] Bell Check")
        with _patch_post():
            offering.publish(actor=None)
        assert Notification.objects.filter(trigger="class_published").exists()
