"""BDD specs for #1042 part 2 through the views: Feature a class on a past students email.

Covers the Write step's select (the sender's eligible classes, past students only), the email
preview and the sent email carrying the card, a blank choice sending none, the bell and push
keeping to subject and body, a crafted class refused, and a draft that resumes with its class.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from classes.factories import ClassOfferingFactory, ClassSessionFactory, RegistrationFactory
from classes.models import ClassOffering, Registration
from core.models import Notification
from membership.models import AnnouncementDraft, Member
from tests.membership.factories import GuildFactory, MembershipPlanFactory

pytestmark = pytest.mark.django_db


def _login(client: Client, username: str) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(
        username=username, email=f"{username}@example.com", password="pass", last_login=timezone.now()
    )
    Member.objects.filter(pk=user.member.pk).update(instructor_oriented_at=timezone.now())
    client.login(username=username, password="pass")
    return user


def _class(instructor: Member, title: str, *, days: int = 4) -> ClassOffering:
    offering = ClassOfferingFactory(
        instructor=instructor, title=title, image="", gallery=0, status=ClassOffering.Status.PUBLISHED
    )
    start = timezone.now() + timedelta(days=days)
    ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))
    return offering


def _setup(client: Client) -> tuple[User, ClassOffering, User]:
    """A signed in instructor with one upcoming class, one past class and one student who took it."""
    teacher = _login(client, "teacher1042")
    upcoming = _class(teacher.member, "Silver Ring Making")
    past = _class(teacher.member, "Old Casting Night", days=-10)
    MembershipPlanFactory()
    student = User.objects.create_user(username="ada", email="ada@example.com", password="x")
    RegistrationFactory(
        class_offering=past, member=student.member, email=student.email, status=Registration.Status.CONFIRMED
    )
    return teacher, upcoming, student


def _post(client: Client, name: str, **data: object) -> object:
    values = {"audience": "students", "body": "<p>Spots open.</p>", "send_email": "on", "mention": "none", **data}
    return client.post(reverse(name), values)


def describe_the_write_step():
    def it_offers_the_senders_eligible_classes_on_a_past_students_email():
        client = Client()
        _teacher, upcoming, _student = _setup(client)
        html = client.get(reverse("hub_compose"), {"audience": "students"}).content.decode()
        assert "data-compose-featured-class" in html
        assert f'<option value="{upcoming.pk}">Silver Ring Making</option>' in html
        assert ">Old Casting Night</option>" not in html

    def it_is_absent_for_a_sender_who_teaches_nothing():
        client = Client()
        lead = _login(client, "lead1042")
        GuildFactory(guild_lead=lead.member)
        html = client.get(reverse("hub_compose")).content.decode()
        assert 'name="audience"' in html
        assert "data-compose-featured-class" not in html

    def it_refuses_a_class_the_sender_may_not_feature():
        client = Client()
        _setup(client)
        other = _class(_login(Client(), "other1042").member, "Not Yours")
        response = _post(client, "hub_compose_save_draft", featured_class=str(other.pk))
        assert response.status_code == 204
        assert not AnnouncementDraft.objects.exists()

    def it_drops_the_class_on_any_other_audience():
        client = Client()
        teacher, upcoming, _student = _setup(client)
        offering = ClassOffering.objects.get(title="Old Casting Night")
        _post(client, "hub_compose_save_draft", audience=f"class:{offering.pk}", featured_class=str(upcoming.pk))
        assert AnnouncementDraft.objects.get().featured_class is None

    def it_resumes_a_draft_with_its_class_chosen():
        client = Client()
        _teacher, upcoming, _student = _setup(client)
        _post(client, "hub_compose_save_draft", featured_class=str(upcoming.pk))
        draft = AnnouncementDraft.objects.get()
        assert draft.featured_class == upcoming
        html = client.get(reverse("hub_compose_resume", args=[draft.pk])).content.decode()
        assert f'<option value="{upcoming.pk}" selected>Silver Ring Making</option>' in html


def describe_the_email():
    def it_shows_the_card_in_the_preview():
        client = Client()
        _teacher, upcoming, _student = _setup(client)
        html = _post(client, "hub_compose_preview", featured_class=str(upcoming.pk)).content.decode()
        assert "data-class-card" in html
        assert "Silver Ring Making" in html
        assert "Register" in html

    def it_ignores_a_crafted_class_in_the_preview():
        client = Client()
        _setup(client)
        other = _class(_login(Client(), "other1042").member, "Not Yours")
        html = _post(client, "hub_compose_preview", featured_class=str(other.pk)).content.decode()
        assert "data-class-card" not in html

    def it_sends_the_card_by_email_and_keeps_the_bell_to_subject_and_body():
        client = Client()
        _teacher, upcoming, student = _setup(client)
        _post(client, "hub_compose_send", featured_class=str(upcoming.pk), subject="Last minute spots")
        message = mail.outbox[0]
        assert "data-class-card" in message.alternatives[0][0]
        assert "Register: http" in message.body
        note = Notification.objects.get(user=student)
        assert "Silver Ring Making" not in f"{note.title} {note.body}"

    def it_sends_no_card_when_left_blank():
        client = Client()
        _setup(client)
        _post(client, "hub_compose_send", featured_class="")
        assert "data-class-card" not in mail.outbox[0].alternatives[0][0]
        assert "Register:" not in mail.outbox[0].body


def describe_send_once():
    def it_refuses_the_same_send_with_the_same_card_as_a_repeat():
        client = Client()
        _teacher, upcoming, _student = _setup(client)
        _post(client, "hub_compose_send", featured_class=str(upcoming.pk))
        _post(client, "hub_compose_send", featured_class=str(upcoming.pk))
        assert AnnouncementDraft.objects.count() == 1
        assert len(mail.outbox) == 1

    def it_sends_the_same_message_again_with_a_card_added():
        client = Client()
        _teacher, upcoming, _student = _setup(client)
        _post(client, "hub_compose_send")
        _post(client, "hub_compose_send", featured_class=str(upcoming.pk))
        assert AnnouncementDraft.objects.filter(sent_at__isnull=False).count() == 2
        assert len(mail.outbox) == 2

    def it_sends_the_same_message_again_with_another_card():
        client = Client()
        teacher, upcoming, _student = _setup(client)
        other = _class(teacher.member, "Resin Printing Basics")
        _post(client, "hub_compose_send", featured_class=str(upcoming.pk))
        _post(client, "hub_compose_send", featured_class=str(other.pk))
        assert [row.featured_class for row in AnnouncementDraft.objects.order_by("pk")] == [upcoming, other]
        assert len(mail.outbox) == 2
