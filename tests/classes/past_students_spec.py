"""BDD specs for #1042 part 1: an instructor's past students roster and the stop link.

Covers who the roster holds (every confirmed registrant of any class the instructor is listed
on, once per account, then once per lowercased address, other statuses left out), the per
instructor opt out that the stop page records, the signed token ``core.email.send`` swaps into
each email, and the no login page itself.
"""

from __future__ import annotations

import pytest
from allauth.account.models import EmailAddress
from django.contrib.auth.models import User
from django.core import mail, signing
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from classes.factories import ClassOfferingFactory, RegistrationFactory
from classes.models import ClassOffering, PastStudentOptOut, Registration
from classes.past_students import past_students, stop_line
from core.email import send as send_email
from core.email_prefs import STOP_TOKEN_PLACEHOLDER, finalize_stop_link, make_stop_token, read_stop_token
from membership.models import Member
from tests.membership.factories import MemberFactory, MembershipPlanFactory

pytestmark = pytest.mark.django_db

_CONFIRMED = Registration.Status.CONFIRMED


def _user(username: str) -> User:
    """A user with an auto-provisioned, active linked Member."""
    MembershipPlanFactory()
    return User.objects.create_user(
        username=username, email=f"{username}@example.com", password="x", last_login=timezone.now()
    )


def _class(instructor: Member, **fields: object) -> ClassOffering:
    return ClassOfferingFactory(instructor=instructor, image="", gallery=0, **fields)


def _took(
    offering: ClassOffering,
    *,
    user: User | None = None,
    email: str = "",
    status: str = _CONFIRMED,
    first_name: str = "Guest",
) -> Registration:
    return RegistrationFactory(
        class_offering=offering,
        member=user.member if user is not None else None,
        email=email or (user.email if user is not None else "guest@example.com"),
        status=status,
        first_name=first_name,
        last_name="Student",
    )


def _stop(instructor: Member, email: str) -> PastStudentOptOut:
    return PastStudentOptOut.objects.record(instructor=instructor, email=email)


def describe_past_students():
    def it_lists_a_person_who_took_three_classes_once():
        teacher = _user("teacher").member
        student = _user("student")
        for _ in range(3):
            _took(_class(teacher), user=student)
        roster = past_students(teacher)
        assert roster.users == [student]
        assert roster.guests == []

    def it_reaches_a_guest_once_by_lowercased_address_with_their_name():
        teacher = _user("teacher").member
        _took(_class(teacher), email="Guest@Example.com", first_name="Gail")
        _took(_class(teacher), email="guest@example.com")
        roster = past_students(teacher)
        assert roster.addresses == ["guest@example.com"]
        assert roster.guests == [("guest@example.com", "Gail Student · guest@example.com")]

    def it_labels_a_guest_with_no_name_by_address():
        teacher = _user("teacher").member
        RegistrationFactory(
            class_offering=_class(teacher), email="anon@example.com", first_name="", last_name="", status=_CONFIRMED
        )
        assert past_students(teacher).guests == [("anon@example.com", "anon@example.com")]

    def it_drops_a_guest_address_a_listed_account_already_uses():
        teacher = _user("teacher").member
        student = _user("student")
        EmailAddress.objects.create(user=student, email="alias@example.com", verified=True)
        offering = _class(teacher)
        _took(offering, user=student)
        _took(offering, email="STUDENT@example.com")
        _took(offering, email="alias@example.com")
        roster = past_students(teacher)
        assert roster.user_ids == {student.pk}
        assert roster.guests == []

    def it_reaches_a_member_with_no_account_by_the_registration_address():
        teacher = _user("teacher").member
        unlinked = MemberFactory()
        RegistrationFactory(
            class_offering=_class(teacher), member=unlinked, email="unlinked@example.com", status=_CONFIRMED
        )
        assert past_students(teacher).addresses == ["unlinked@example.com"]

    @pytest.mark.parametrize(
        "status",
        [
            Registration.Status.CANCELLED,
            Registration.Status.REFUNDED,
            Registration.Status.PENDING,
            Registration.Status.WAITLISTED,
        ],
    )
    def it_leaves_out_registrations_that_are_not_confirmed(status: str):
        teacher = _user("teacher").member
        offering = _class(teacher)
        _took(offering, user=_user("student"), status=status)
        _took(offering, email="guest@example.com", status=status)
        roster = past_students(teacher)
        assert roster.users == []
        assert roster.guests == []

    def it_counts_a_class_the_instructor_co_teaches_whatever_its_status():
        teacher = _user("teacher").member
        other = _user("other").member
        student = _user("student")
        _took(_class(other, instructors=[other, teacher], status=ClassOffering.Status.ARCHIVED), user=student)
        assert past_students(teacher).users == [student]

    def it_leaves_out_students_of_classes_the_instructor_does_not_teach():
        teacher = _user("teacher").member
        _took(_class(_user("other").member), user=_user("student"))
        assert past_students(teacher).users == []

    def it_orders_accounts_by_name():
        teacher = _user("teacher").member
        offering = _class(teacher)
        zed, amy = _user("zed"), _user("amy")
        _took(offering, user=zed)
        _took(offering, user=amy)
        assert past_students(teacher).users == [amy, zed]

    def describe_when_someone_stopped_the_emails():
        def it_skips_an_account_that_stopped_them_at_any_of_its_addresses():
            teacher = _user("teacher").member
            student = _user("student")
            student.member.notification_email = "notify@example.com"
            student.member.save(update_fields=["notification_email"])
            _took(_class(teacher), user=student)
            _took(_class(teacher), email="student@example.com")
            PastStudentOptOut.objects.create(instructor=teacher, email="notify@example.com")
            roster = past_students(teacher)
            assert roster.users == []
            assert roster.guests == []

        def it_skips_an_account_by_the_account_the_stop_recorded():
            teacher = _user("teacher").member
            student = _user("student")
            EmailAddress.objects.create(user=student, email="old@example.com", verified=True)
            _took(_class(teacher), user=student)
            _stop(teacher, "old@example.com")
            assert past_students(teacher).users == []

        def it_skips_a_guest_by_address():
            teacher = _user("teacher").member
            _took(_class(teacher), email="guest@example.com")
            _stop(teacher, "Guest@Example.com")
            assert past_students(teacher).guests == []

        def it_still_lists_them_for_another_instructor():
            teacher, other = _user("teacher").member, _user("other").member
            student = _user("student")
            _took(_class(teacher), user=student)
            _took(_class(other), user=student)
            _stop(teacher, student.email)
            assert past_students(teacher).users == []
            assert past_students(other).users == [student]


def describe_PastStudentOptOut():
    def describe_record():
        def it_stores_the_address_lowercased_with_the_account_that_owns_it():
            teacher = _user("teacher").member
            student = _user("student")
            row = _stop(teacher, " Student@Example.com ")
            assert (row.email, row.user) == ("student@example.com", student)
            assert str(row) == f"student@example.com stopped emails from member #{teacher.pk}"

        def it_keeps_one_row_however_often_the_link_is_opened():
            teacher = _user("teacher").member
            first = _stop(teacher, "guest@example.com")
            assert _stop(teacher, "guest@example.com") == first
            assert PastStudentOptOut.objects.count() == 1
            assert first.user is None

        def it_attaches_an_account_made_since_on_the_next_open():
            teacher = _user("teacher").member
            _stop(teacher, "late@example.com")
            late = _user("late")
            assert _stop(teacher, "late@example.com").user == late

    def describe_skipped_by():
        def it_returns_the_accounts_and_addresses_of_one_instructor():
            teacher, other = _user("teacher").member, _user("other").member
            student = _user("student")
            _stop(teacher, student.email)
            _stop(teacher, "guest@example.com")
            _stop(other, "elsewhere@example.com")
            assert PastStudentOptOut.objects.skipped_by(teacher) == (
                {student.pk},
                {"student@example.com", "guest@example.com"},
            )


def describe_stop_tokens():
    def it_round_trips_a_lowercased_address():
        assert read_stop_token(make_stop_token(" Me@Example.com ")) == "me@example.com"

    def it_refuses_a_token_this_site_did_not_sign():
        assert read_stop_token("not-a-token") is None
        assert read_stop_token("") is None

    def it_refuses_a_signed_payload_that_is_not_an_address():
        assert read_stop_token(signing.dumps(5, salt="past-students-stop")) is None

    def describe_finalize_stop_link():
        def it_puts_the_one_recipients_token_in_both_parts():
            text, html = finalize_stop_link(
                f"stop {STOP_TOKEN_PLACEHOLDER}", f"<a href='{STOP_TOKEN_PLACEHOLDER}'>", ["me@example.com"]
            )
            token = make_stop_token("me@example.com")
            assert text == f"stop {token}"
            assert html == f"<a href='{token}'>"

        def it_leaves_the_token_empty_for_several_recipients():
            text, html = finalize_stop_link(f"x{STOP_TOKEN_PLACEHOLDER}y", None, ["a@example.com", "b@example.com"])
            assert (text, html) == ("xy", None)

        def it_fills_an_html_part_alone():
            text, html = finalize_stop_link("plain", f"<a href='{STOP_TOKEN_PLACEHOLDER}'>", ["me@example.com"])
            assert (text, html) == ("plain", f"<a href='{make_stop_token('me@example.com')}'>")

        def it_changes_nothing_without_the_placeholder():
            assert finalize_stop_link("plain", "<p>plain</p>", ["a@example.com"]) == ("plain", "<p>plain</p>")

        def it_runs_inside_core_email_send():
            send_email(
                to="me@example.com",
                subject="Hi",
                trigger_kind="test",
                text_body=f"stop {STOP_TOKEN_PLACEHOLDER}",
                html_body=f"<p>{STOP_TOKEN_PLACEHOLDER}</p>",
            )
            token = make_stop_token("me@example.com")
            assert mail.outbox[0].body == f"stop {token}"
            assert token in mail.outbox[0].alternatives[0][0]


def describe_stop_line():
    def it_names_the_instructor_and_links_their_stop_page():
        teacher = _user("teacher").member
        html, text = stop_line(teacher, "Kay <b>")
        url = reverse("classes:stop_past_student_emails", args=[teacher.pk, STOP_TOKEN_PLACEHOLDER])
        assert "You took a class with Kay &lt;b&gt;." in html
        assert "Stop emails from Kay &lt;b&gt;</a>" in html
        assert url in html
        assert text.startswith("You took a class with Kay <b>. Stop emails from Kay <b>: http")
        assert text.endswith(url)


def describe_stop_page():
    def _url(instructor: Member, email: str) -> str:
        return reverse("classes:stop_past_student_emails", args=[instructor.pk, make_stop_token(email)])

    def it_stops_the_emails_for_a_visitor_who_is_not_signed_in():
        teacher = _user("teacher")
        Member.objects.filter(pk=teacher.member.pk).update(preferred_name="Kay")
        response = Client().get(_url(teacher.member, "guest@example.com"))
        assert response.status_code == 200
        assert b"data-past-students-stopped" in response.content
        assert b"You won't get any more emails from Kay about their classes." in response.content
        assert PastStudentOptOut.objects.filter(instructor=teacher.member, email="guest@example.com").exists()

    def it_names_an_instructor_with_no_account_by_their_member_name():
        teacher = MemberFactory(full_legal_name="Rae Rivers")
        response = Client().get(_url(teacher, "guest@example.com"))
        assert f"emails from {teacher.display_name} about".encode() in response.content

    def it_is_a_404_for_a_link_this_site_did_not_sign():
        teacher = _user("teacher").member
        url = reverse("classes:stop_past_student_emails", args=[teacher.pk, "forged"])
        assert Client().get(url).status_code == 404
        assert not PastStudentOptOut.objects.exists()

    def it_is_a_404_for_an_instructor_no_longer_here():
        assert (
            Client()
            .get(reverse("classes:stop_past_student_emails", args=[999999, make_stop_token("a@b.co")]))
            .status_code
            == 404
        )

    def it_records_nothing_on_a_post():
        teacher = _user("teacher").member
        assert Client().post(_url(teacher, "guest@example.com")).status_code == 405
        assert not PastStudentOptOut.objects.exists()
