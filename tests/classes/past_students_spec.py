"""BDD specs for #1042 part 1: an instructor's past students roster and the stop link.

Covers who the roster holds (every confirmed registrant of any class the instructor is listed
on, once per account, then once per lowercased address, other statuses left out), the per
instructor opt out that the stop page records, the signed token ``core.email.send`` swaps into
each email, and the no login page itself.
"""

from __future__ import annotations

import re

import pytest
from allauth.account.models import EmailAddress
from django.contrib.auth.models import User
from django.core import mail, signing
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from classes.factories import ClassOfferingFactory, RegistrationFactory
from classes.models import ClassOffering, PastStudentOptOut, Registration
from classes.past_students import instructor_name, past_students, stop_line
from core.email import send as send_email
from core.email_prefs import StopLink, finalize_stop_link, make_stop_token, read_stop_token, stop_url
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


def _token_in(url: str) -> str:
    """The stop token inside a stop page URL."""
    return re.search(r"/classes/stop-emails/([^/]+)/", url).group(1)


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


def describe_stopped_member_aliases():
    def it_skips_a_guest_registration_at_a_verified_alias_of_a_member_who_stopped():
        teacher = _user("teacher").member
        member = _user("member")
        EmailAddress.objects.create(user=member, email="Alias@Example.com", verified=True)
        _stop(teacher, member.email)
        _took(_class(teacher), email="alias@example.com")
        assert past_students(teacher).guests == []

    def it_skips_it_though_the_member_took_no_class_of_theirs():
        teacher = _user("teacher").member
        member = _user("member")
        member.member.notification_email = "notify@example.com"
        member.member.save(update_fields=["notification_email"])
        _stop(teacher, member.email)
        _took(_class(teacher), email="notify@example.com")
        assert member.pk not in past_students(teacher).user_ids
        assert past_students(teacher).guests == []

    def it_still_emails_an_unverified_alias():
        teacher = _user("teacher").member
        member = _user("member")
        EmailAddress.objects.create(user=member, email="maybe@example.com", verified=False)
        _stop(teacher, member.email)
        _took(_class(teacher), email="maybe@example.com")
        assert past_students(teacher).addresses == ["maybe@example.com"]


def describe_stop_tokens():
    def it_round_trips_the_instructor_and_a_lowercased_address():
        assert read_stop_token(make_stop_token(7, " Me@Example.com ")) == (7, "me@example.com")

    def it_refuses_a_token_this_site_did_not_sign():
        assert read_stop_token("not-a-token") is None
        assert read_stop_token("") is None

    @pytest.mark.parametrize("payload", [5, [5], ["5", "a@b.co"], [5, ""], [5, 6]])
    def it_refuses_a_signed_payload_of_the_wrong_shape(payload: object):
        assert read_stop_token(signing.dumps(payload, salt="past-students-stop")) is None

    def describe_StopLink():
        def it_makes_a_fresh_placeholder_that_names_no_instructor():
            first, second = StopLink.for_instructor(7), StopLink.for_instructor(7)
            assert first.instructor_pk == 7
            assert first.placeholder != second.placeholder
            assert re.fullmatch(r"__PL_STOP_[0-9a-f]{32}__", first.placeholder)
            assert first.url == stop_url(first.placeholder)

    def describe_finalize_stop_link():
        def it_puts_the_one_recipients_token_in_both_parts_and_returns_the_link():
            link = StopLink.for_instructor(7)
            text, html, url = finalize_stop_link(
                f"stop {link.placeholder}", f"<a href='{link.placeholder}'>", ["me@example.com"], link
            )
            token = text.removeprefix("stop ")
            assert read_stop_token(token) == (7, "me@example.com")
            assert html == f"<a href='{token}'>"
            assert url == stop_url(token)

        def it_leaves_the_token_empty_and_no_link_for_several_recipients():
            link = StopLink.for_instructor(7)
            text, html, url = finalize_stop_link(
                f"x{link.placeholder}y", None, ["a@example.com", "b@example.com"], link
            )
            assert (text, html, url) == ("xy", None, "")

        def it_fills_an_html_part_alone():
            link = StopLink.for_instructor(7)
            text, html, url = finalize_stop_link("plain", f"<a href='{link.placeholder}'>", ["me@example.com"], link)
            assert text == "plain"
            assert read_stop_token(html.removeprefix("<a href='").removesuffix("'>")) == (7, "me@example.com")
            assert url

        def it_touches_nothing_without_a_stop_link_whatever_the_body_says():
            body = "__PL_STOP_TOKEN_99__ __PL_STOP_0123456789abcdef__"
            assert finalize_stop_link(body, f"<p>{body}</p>", ["a@example.com"], None) == (body, f"<p>{body}</p>", "")

        def it_signs_only_its_own_placeholder_never_text_that_looks_like_one():
            link = StopLink.for_instructor(7)
            other = StopLink.for_instructor(99).placeholder
            body = f"__PL_STOP_TOKEN_99__ {other} {link.placeholder}"
            text, _html, url = finalize_stop_link(body, None, ["me@example.com"], link)
            literal, forged, token = text.split(" ")
            assert (literal, forged) == ("__PL_STOP_TOKEN_99__", other)
            assert read_stop_token(token) == (7, "me@example.com")
            assert read_stop_token(_token_in(url)) == (7, "me@example.com")

        def describe_inside_core_email_send():
            def it_sends_the_token_and_the_one_click_unsubscribe_headers():
                link = StopLink.for_instructor(7)
                send_email(
                    to="me@example.com",
                    subject="Hi",
                    trigger_kind="test",
                    text_body=f"stop {link.placeholder}",
                    html_body=f"<p>{link.placeholder}</p>",
                    stop_link=link,
                )
                message = mail.outbox[0]
                token = message.body.removeprefix("stop ")
                assert read_stop_token(token) == (7, "me@example.com")
                assert token in message.alternatives[0][0]
                assert message.extra_headers["List-Unsubscribe"] == f"<{stop_url(token)}>"
                assert message.extra_headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"

            def it_gives_any_other_email_no_stop_link_whatever_its_body_says():
                body = f"__PL_STOP_TOKEN_99__ {StopLink.for_instructor(99).placeholder}"
                send_email(
                    to="me@example.com", subject="Hi", trigger_kind="test", text_body=body, html_body=f"<p>{body}</p>"
                )
                assert mail.outbox[0].body == body
                assert "List-Unsubscribe" not in mail.outbox[0].extra_headers


def describe_instructor_name():
    def it_is_the_name_their_profile_and_classes_show():
        teacher = _user("teacher").member
        Member.objects.filter(pk=teacher.pk).update(preferred_name="Kay")
        teacher.refresh_from_db()
        assert instructor_name(teacher) == "Kay"

    def it_falls_back_to_your_instructor_never_an_address():
        teacher = _user("teacher").member
        Member.objects.filter(pk=teacher.pk).update(preferred_name="", full_legal_name="")
        teacher.refresh_from_db()
        assert instructor_name(teacher) == "your instructor"


def describe_stop_line():
    def it_names_the_instructor_and_links_their_stop_page():
        teacher = _user("teacher").member
        Member.objects.filter(pk=teacher.pk).update(preferred_name="Kay <b>")
        teacher.refresh_from_db()
        link = StopLink.for_instructor(teacher.pk)
        html, text = stop_line(teacher, link)
        url = link.url
        assert "You took a class with Kay &lt;b&gt;." in html
        assert "Stop emails from Kay &lt;b&gt;</a>" in html
        assert url in html
        assert text == f"You took a class with Kay <b>. Stop emails from Kay <b>: {url}"

    def it_never_shows_the_instructors_email():
        teacher = _user("teacher").member
        Member.objects.filter(pk=teacher.pk).update(preferred_name="", full_legal_name="")
        teacher.refresh_from_db()
        html, text = stop_line(teacher, StopLink.for_instructor(teacher.pk))
        assert "teacher@example.com" not in html + text
        assert "Stop emails from your instructor</a>" in html


def describe_stop_page():
    def _url(instructor: Member, email: str) -> str:
        return reverse("classes:stop_past_student_emails", args=[make_stop_token(instructor.pk, email)])

    def _kay() -> Member:
        teacher = _user("teacher").member
        Member.objects.filter(pk=teacher.pk).update(preferred_name="Kay")
        teacher.refresh_from_db()
        return teacher

    def it_only_shows_a_stop_button_on_a_get_or_head():
        teacher = _kay()
        client = Client()
        response = client.get(_url(teacher, "guest@example.com"))
        assert response.status_code == 200
        assert b"data-past-students-stop-confirm" in response.content
        assert b"Stop emails from Kay</button>" in response.content
        assert client.head(_url(teacher, "guest@example.com")).status_code == 200
        assert not PastStudentOptOut.objects.exists()

    def it_stops_the_emails_on_a_post_from_a_visitor_who_is_not_signed_in():
        teacher = _kay()
        response = Client(enforce_csrf_checks=True).post(_url(teacher, "guest@example.com"))
        assert response.status_code == 200
        assert b"data-past-students-stopped" in response.content
        assert b"You won't get any more emails from Kay about their classes." in response.content
        assert PastStudentOptOut.objects.filter(instructor=teacher, email="guest@example.com").exists()

    def it_takes_a_mail_clients_one_click_post():
        teacher = _kay()
        response = Client(enforce_csrf_checks=True).post(
            _url(teacher, "guest@example.com"), {"List-Unsubscribe": "One-Click"}
        )
        assert response.status_code == 200
        assert PastStudentOptOut.objects.count() == 1

    def it_stops_only_the_instructor_the_token_was_signed_for():
        teacher, other = _kay(), _user("other").member
        Client().post(_url(teacher, "guest@example.com"))
        assert list(PastStudentOptOut.objects.values_list("instructor_id", flat=True)) == [teacher.pk]
        forged = signing.dumps([other.pk, "guest@example.com"], key="not-the-site-key", salt="past-students-stop")
        url = reverse("classes:stop_past_student_emails", args=[forged])
        assert Client().post(url).status_code == 404
        assert not PastStudentOptOut.objects.filter(instructor=other).exists()

    def it_is_a_404_for_a_link_this_site_did_not_sign():
        url = reverse("classes:stop_past_student_emails", args=["forged"])
        assert Client().get(url).status_code == 404
        assert Client().post(url).status_code == 404
        assert not PastStudentOptOut.objects.exists()

    def it_is_a_404_for_an_instructor_no_longer_here():
        url = reverse("classes:stop_past_student_emails", args=[make_stop_token(999999, "a@b.co")])
        assert Client().post(url).status_code == 404

    def it_refuses_other_methods():
        teacher = _kay()
        assert Client().put(_url(teacher, "guest@example.com")).status_code == 405
        assert not PastStudentOptOut.objects.exists()
