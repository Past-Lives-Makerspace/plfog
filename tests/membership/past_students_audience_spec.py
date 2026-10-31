"""BDD specs for #1042 part 1: the My past students announcement audience on the model.

Covers its names and title, its count, the selection narrowing, its send (members by bell, push
and email, guests by email, nobody twice, never Discord), the stop line in every email, and the
round trip: a person who opens their stop link is skipped by that instructor's next send and
still reached by another instructor's.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.core.exceptions import ValidationError
from django.test import Client
from django.utils import timezone

from classes.factories import ClassOfferingFactory, RegistrationFactory
from classes.models import ClassOffering, PastStudentOptOut, Registration
from core.email_prefs import stop_token_placeholder, stop_url
from core.models import EventDelivery, Notification
from membership.models import AnnouncementDraft, GuildAnnouncement, Member
from tests.membership.factories import MembershipPlanFactory

pytestmark = pytest.mark.django_db

_STUDENTS = AnnouncementDraft.Audience.STUDENTS
_STOP_LINK = re.compile(r"https?://[^\s\"'<>]+/classes/stop-emails/[^\s\"'<>/]+/")


def _user(username: str) -> User:
    """A user with an auto-provisioned, active linked Member."""
    MembershipPlanFactory()
    return User.objects.create_user(
        username=username, email=f"{username}@example.com", password="x", last_login=timezone.now()
    )


def _class(instructor: Member) -> ClassOffering:
    return ClassOfferingFactory(instructor=instructor, image="", gallery=0)


def _took(offering: ClassOffering, *, user: User | None = None, email: str = "") -> Registration:
    return RegistrationFactory(
        class_offering=offering,
        member=user.member if user is not None else None,
        email=email or (user.email if user is not None else "guest@example.com"),
        status=Registration.Status.CONFIRMED,
    )


def _draft(sender: User, **fields: object) -> AnnouncementDraft:
    values = {"title": "Class Announcement", "body": "<p>Last minute workshop Saturday.</p>", **fields}
    return AnnouncementDraft.objects.create(author=sender, audience=_STUDENTS, **values)


def _reached(channel: str) -> set[str]:
    return set(
        EventDelivery.objects.filter(
            event_key="site_announcement", channel=channel, status=EventDelivery.Status.SENT
        ).values_list("target_ref", flat=True)
    )


def _teacher_with_students() -> tuple[User, User]:
    """A teacher, one student account who took two of their classes, and one guest."""
    teacher = _user("teacher")
    Member.objects.filter(pk=teacher.member.pk).update(preferred_name="Kay")
    student = _user("student")
    first, second = _class(teacher.member), _class(teacher.member)
    _took(first, user=student)
    _took(second, user=student)
    _took(second, email="guest@example.com")
    return User.objects.get(pk=teacher.pk), student


def describe_AnnouncementDraft_students_audience():
    def it_names_the_audience_by_whose_students_they_are():
        teacher, _student = _teacher_with_students()
        draft = AnnouncementDraft(audience=_STUDENTS, author=teacher)
        assert draft.audience_value == "students"
        assert draft.audience_label == "Past students of Kay"
        assert draft.announcement_category == "Class Announcement"
        assert AnnouncementDraft.Audience.STUDENTS.label == "My past students"

    def it_never_posts_to_discord_whatever_the_switch_says():
        draft = AnnouncementDraft(
            audience=_STUDENTS, discord_enabled=True, discord_channel=GuildAnnouncement.DiscordChannel.GENERAL
        )
        assert draft.discord_on is False
        assert "Discord" not in draft.channel_labels

    def it_counts_each_person_once():
        teacher, _student = _teacher_with_students()
        assert AnnouncementDraft(audience=_STUDENTS, author=teacher).recipient_count() == 2

    def it_reaches_nobody_without_a_sender():
        assert AnnouncementDraft(audience=_STUDENTS).recipient_count() == 0

    def it_narrows_to_the_saved_selection_and_never_past_the_roster():
        teacher, student = _teacher_with_students()
        stranger = _user("stranger")
        draft = AnnouncementDraft(
            audience=_STUDENTS,
            author=teacher,
            recipient_selection={"users": [student.pk, stranger.pk], "custom": ["nobody@example.com"]},
        )
        assert draft._student_recipients() == ({student.pk}, [])
        guest_only = AnnouncementDraft(
            audience=_STUDENTS, author=teacher, recipient_selection={"users": [], "custom": ["Guest@example.com"]}
        )
        assert guest_only._student_recipients() == (set(), ["guest@example.com"])

    def it_is_refused_with_no_sender_to_be_the_instructor():
        draft = AnnouncementDraft.objects.create(audience=_STUDENTS, title="Class Announcement", body="<p>x</p>")
        with pytest.raises(ValidationError, match="Only an instructor"):
            draft.queue_send()

    def describe_email():
        def it_ends_with_the_stop_line_in_both_parts():
            teacher, _student = _teacher_with_students()
            message = AnnouncementDraft(
                audience=_STUDENTS, author=teacher, title="Workshop", body="<p>Hi</p>"
            ).build_email_message("https://example.com/")
            assert "You took a class with Kay. <a href=" in message.html_body
            assert "Stop emails from Kay</a>" in message.html_body
            url = stop_url(stop_token_placeholder(teacher.member.pk))
            assert url in message.html_body
            assert message.body.endswith(url)

        def it_leaves_other_audiences_without_it():
            teacher, _student = _teacher_with_students()
            message = AnnouncementDraft(
                audience=AnnouncementDraft.Audience.SITE, author=teacher, title="Hi", body="<p>Hi</p>"
            ).build_email_message("https://example.com/")
            assert "data-past-students-stop" not in message.html_body

    def describe_send():
        def it_reaches_the_account_by_bell_push_and_email_and_the_guest_by_email():
            teacher, student = _teacher_with_students()
            emailed, total = _draft(teacher).send_in_request()
            assert (emailed, total) == (2, 2)
            assert _reached("in_app") == {f"user:{student.pk}"}
            assert _reached("push") == {f"user:{student.pk}"}
            assert _reached("email") == {f"user:{student.pk}", "email:guest@example.com"}
            assert Notification.objects.filter(user=student).count() == 1
            assert sorted(message.to[0] for message in mail.outbox) == ["guest@example.com", "student@example.com"]

        def it_never_posts_to_discord():
            teacher, _student = _teacher_with_students()
            _draft(
                teacher, discord_enabled=True, discord_channel=GuildAnnouncement.DiscordChannel.GENERAL
            ).send_in_request()
            assert _reached("discord") == set()

        def it_gives_each_recipient_their_own_working_stop_link():
            teacher, _student = _teacher_with_students()
            _draft(teacher).send_in_request()
            links = {message.to[0]: _STOP_LINK.search(message.body).group(0) for message in mail.outbox}
            assert len(set(links.values())) == 2
            assert "__PL_STOP_TOKEN_" not in "".join(message.body for message in mail.outbox)
            assert all(
                message.extra_headers["List-Unsubscribe"] == f"<{links[message.to[0]]}>" for message in mail.outbox
            )

        def it_counts_nobody_emailed_with_email_off():
            teacher, _student = _teacher_with_students()
            assert _draft(teacher, send_email=False).send_in_request() == (0, 2)
            assert mail.outbox == []

        def it_stamps_the_ledger_period_it_sent_under():
            teacher, _student = _teacher_with_students()
            draft = _draft(teacher)
            draft.send_in_request()
            draft.refresh_from_db()
            assert draft.delivery_period == f"announce:{draft.pk}"
            assert draft.reach().people == 2

        def it_lists_a_guest_whose_email_failed_with_the_reason():
            teacher, _student = _teacher_with_students()
            draft = _draft(teacher)
            with patch("core.email._deliver", side_effect=RuntimeError("mailbox full")):
                draft.send_in_request()
            failed = {r.address: r.email_error for r in draft.recipient_list().recipients if r.email == "failed"}
            assert failed["guest@example.com"] == "mailbox full"
            assert draft._email_only_audience() == {"guest@example.com"}

    def describe_stop_round_trip():
        def it_skips_whoever_opened_their_link_on_the_same_instructors_next_send():
            teacher, student = _teacher_with_students()
            _draft(teacher).send_in_request()
            link = _STOP_LINK.search(next(m for m in mail.outbox if m.to == [student.email]).body).group(0)
            assert Client().get(urlparse(link).path).status_code == 200
            assert not PastStudentOptOut.objects.exists()
            assert Client().post(urlparse(link).path).status_code == 200
            assert PastStudentOptOut.objects.filter(instructor=teacher.member, user=student).exists()
            mail.outbox.clear()
            EventDelivery.objects.all().delete()
            _draft(teacher, body="<p>Another one.</p>").send_in_request()
            assert [message.to[0] for message in mail.outbox] == ["guest@example.com"]

        def it_still_reaches_them_from_a_different_instructor():
            teacher, student = _teacher_with_students()
            other = _user("other")
            _took(_class(other.member), user=student)
            PastStudentOptOut.objects.record(instructor=teacher.member, email=student.email)
            _draft(other).send_in_request()
            assert [message.to[0] for message in mail.outbox] == [student.email]
