"""BDD specs for teach_class_emails — the per-class welcome email authoring page."""

from __future__ import annotations

import pytest
from django.urls import reverse

from classes.factories import CategoryFactory, ClassOfferingFactory, InstructorFactory, UserFactory
from tests.membership.factories import GuildFactory

pytestmark = pytest.mark.django_db


def _instructor_and_class(username="welc-teacher@example.com", slug="welc-class"):
    user = UserFactory(username=username)
    member = InstructorFactory(user=user)
    offering = ClassOfferingFactory(slug=slug, instructor=member)
    return user, offering


def describe_teach_class_emails():
    def it_renders_the_emails_tab_for_the_instructor(client):
        user, offering = _instructor_and_class()
        client.force_login(user)
        response = client.get(reverse("classes:teach_class_emails", kwargs={"pk": offering.pk}))
        assert response.status_code == 200
        assert b"Welcome email" in response.content

    def it_saves_the_welcome_email(client):
        user, offering = _instructor_and_class(username="welc-save@example.com", slug="welc-save")
        client.force_login(user)
        response = client.post(
            reverse("classes:teach_class_emails", kwargs={"pk": offering.pk}),
            {"welcome_email_enabled": "on", "welcome_email_subject": "Hi", "welcome_email_body": "Bring tools."},
        )
        assert response.status_code == 302
        offering.refresh_from_db()
        assert offering.welcome_email_enabled is True
        assert offering.welcome_email_subject == "Hi"

    def it_re_renders_with_errors_on_an_invalid_save(client):
        user, offering = _instructor_and_class(username="welc-inv@example.com", slug="welc-inv")
        client.force_login(user)
        response = client.post(
            reverse("classes:teach_class_emails", kwargs={"pk": offering.pk}),
            {"welcome_email_enabled": "on", "welcome_email_subject": "", "welcome_email_body": ""},
        )
        assert response.status_code == 200  # re-rendered, not redirected
        offering.refresh_from_db()
        assert offering.welcome_email_enabled is False

    def it_sends_a_test_to_the_requester(client, mailoutbox):
        user, offering = _instructor_and_class(username="welc-test@example.com", slug="welc-test")
        client.force_login(user)
        client.post(
            reverse("classes:teach_class_emails", kwargs={"pk": offering.pk}),
            {
                "welcome_email_enabled": "on",
                "welcome_email_subject": "Hi",
                "welcome_email_body": "Body",
                "send_test": "1",
            },
        )
        assert len(mailoutbox) == 1
        assert mailoutbox[0].to == ["welc-test@example.com"]

    def it_lets_a_guild_lead_edit_their_guilds_class(client):
        user = UserFactory(username="welc-lead@example.com")
        lead = InstructorFactory(user=user)
        guild = GuildFactory(guild_lead=lead)
        offering = ClassOfferingFactory(slug="welc-guild", category=CategoryFactory(guild=guild))
        client.force_login(user)
        response = client.get(reverse("classes:teach_class_emails", kwargs={"pk": offering.pk}))
        assert response.status_code == 200

    def it_404s_for_a_class_the_member_cannot_edit(client):
        user = UserFactory(username="welc-nope@example.com")
        InstructorFactory(user=user)  # an instructor, but not of this class
        other = ClassOfferingFactory(slug="welc-other")
        client.force_login(user)
        response = client.get(reverse("classes:teach_class_emails", kwargs={"pk": other.pk}))
        assert response.status_code == 404
