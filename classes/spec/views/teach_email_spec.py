"""BDD specs for the Registrations tab's hand-off to the Announcement Composer.

The tab used to carry its own subject/body form and send a plain BCC email. It now validates
the ticked rows and redirects to ``hub_compose``, pre-scoped to the class and pre-checking
exactly those students — the same composer every other class-email surface opens.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest
from django.core import mail
from django.urls import reverse

from classes.factories import (
    ClassOfferingFactory,
    InstructorFactory,
    RegistrationFactory,
    UserFactory,
)
from classes.models import Registration

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _email_outbox(settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    mail.outbox = []
    yield
    mail.outbox = []


@pytest.fixture
def instructor():
    user = UserFactory(username="teacher@example.com", email="teacher@example.com")
    return InstructorFactory(user=user, full_legal_name="Teacher T", instructor_slug="teacher-t")


@pytest.fixture
def other_instructor():
    user = UserFactory(username="other@example.com", email="other@example.com")
    return InstructorFactory(user=user, full_legal_name="Other", instructor_slug="other")


def _compose_params(response):
    """The compose querystring the view redirected to, as ``{name: [values]}``."""
    assert response.status_code == 302
    parsed = urlparse(response["Location"])
    assert parsed.path == reverse("hub_compose")
    return parse_qs(parsed.query)


def _member_registration(offering, email, first_name="Ada"):
    """A registration whose registrant has a real app account (so it maps to a ``user:`` token)."""
    from membership.models import Member

    user = UserFactory(username=email, email=email)
    member = Member.objects.get(user=user)
    return RegistrationFactory(class_offering=offering, email=email, first_name=first_name, member=member)


def describe_registrations_tab_email_handoff():
    def it_requires_an_active_member(client):
        from membership.models import Member

        user = UserFactory(username="former@example.com", email="former@example.com")
        InstructorFactory(user=user, status=Member.Status.FORMER)
        client.force_login(user)
        response = client.post(reverse("classes:teach_registrations_email"), data={})
        assert response.status_code == 403

    def describe_a_selection_it_refuses():
        def it_bounces_back_when_nothing_was_ticked(instructor, client):
            client.force_login(instructor.user)
            response = client.post(reverse("classes:teach_registrations_email"), data={})
            assert response.status_code == 302
            assert response["Location"] == reverse("classes:teach_registrations")
            assert len(mail.outbox) == 0

        def it_says_which_step_was_missed(instructor, client):
            client.force_login(instructor.user)
            response = client.post(reverse("classes:teach_registrations_email"), data={}, follow=True)
            messages = [str(m) for m in response.context["messages"]]
            assert "Tick the students you want to email first." in messages

        def it_ignores_a_row_id_that_is_not_a_number(instructor, client):
            client.force_login(instructor.user)
            response = client.post(
                reverse("classes:teach_registrations_email"), data={"registration_ids": ["not-a-pk"]}
            )
            assert response["Location"] == reverse("classes:teach_registrations")

        def it_bounces_back_when_every_row_belongs_to_someone_else(instructor, other_instructor, client):
            """A crafted POST resolves to nothing, rather than emailing a stranger's students."""
            client.force_login(instructor.user)
            theirs = RegistrationFactory(class_offering=ClassOfferingFactory(instructor=other_instructor))
            response = client.post(reverse("classes:teach_registrations_email"), data={"registration_ids": [theirs.pk]})
            assert response["Location"] == reverse("classes:teach_registrations")
            assert len(mail.outbox) == 0

        def it_refuses_a_selection_spanning_two_classes(instructor, client):
            client.force_login(instructor.user)
            one = RegistrationFactory(class_offering=ClassOfferingFactory(instructor=instructor, slug="one"))
            two = RegistrationFactory(class_offering=ClassOfferingFactory(instructor=instructor, slug="two"))
            response = client.post(
                reverse("classes:teach_registrations_email"),
                data={"registration_ids": [one.pk, two.pk]},
                follow=True,
            )
            messages = [str(m) for m in response.context["messages"]]
            assert "Pick students from one class at a time." in messages

    def describe_a_selection_it_accepts():
        def it_opens_the_composer_locked_to_that_class(instructor, client):
            client.force_login(instructor.user)
            offering = ClassOfferingFactory(instructor=instructor)
            reg = RegistrationFactory(class_offering=offering, email="guest@example.com")
            response = client.post(reverse("classes:teach_registrations_email"), data={"registration_ids": [reg.pk]})
            params = _compose_params(response)
            assert params["audience"] == [f"class:{offering.pk}"]
            assert params["lock"] == ["1"]

        def it_sends_a_registrant_with_an_account_through_as_a_user_token(instructor, client):
            client.force_login(instructor.user)
            offering = ClassOfferingFactory(instructor=instructor)
            reg = _member_registration(offering, "ada@example.com")
            response = client.post(reverse("classes:teach_registrations_email"), data={"registration_ids": [reg.pk]})
            assert _compose_params(response)["recipients"] == [f"user:{reg.member.user.pk}"]

        def it_sends_a_guest_registrant_through_as_their_address(instructor, client):
            """Guest checkout leaves no account, so the composer reaches them by email alone."""
            client.force_login(instructor.user)
            offering = ClassOfferingFactory(instructor=instructor)
            reg = RegistrationFactory(class_offering=offering, email="Guest@Example.com")
            response = client.post(reverse("classes:teach_registrations_email"), data={"registration_ids": [reg.pk]})
            assert _compose_params(response)["recipients"] == ["custom:guest@example.com"]

        def it_folds_in_the_waitlist_when_a_waitlisted_student_was_ticked(instructor, client):
            """Without this the composer's roster is confirmed-only and the pick would vanish."""
            client.force_login(instructor.user)
            offering = ClassOfferingFactory(instructor=instructor)
            reg = RegistrationFactory(
                class_offering=offering, email="waiting@example.com", status=Registration.Status.WAITLISTED
            )
            response = client.post(reverse("classes:teach_registrations_email"), data={"registration_ids": [reg.pk]})
            assert _compose_params(response)["include_waitlist"] == ["1"]

        def it_leaves_the_waitlist_out_for_a_confirmed_only_selection(instructor, client):
            client.force_login(instructor.user)
            offering = ClassOfferingFactory(instructor=instructor)
            reg = RegistrationFactory(
                class_offering=offering, email="confirmed@example.com", status=Registration.Status.CONFIRMED
            )
            response = client.post(reverse("classes:teach_registrations_email"), data={"registration_ids": [reg.pk]})
            assert "include_waitlist" not in _compose_params(response)

        def it_keeps_only_the_rows_that_are_mine(instructor, other_instructor, client):
            """A mixed POST is the intersection, not an error and not a leak."""
            client.force_login(instructor.user)
            offering = ClassOfferingFactory(instructor=instructor)
            mine = RegistrationFactory(class_offering=offering, email="mine@example.com")
            theirs = RegistrationFactory(class_offering=ClassOfferingFactory(instructor=other_instructor))
            response = client.post(
                reverse("classes:teach_registrations_email"), data={"registration_ids": [mine.pk, theirs.pk]}
            )
            params = _compose_params(response)
            assert params["audience"] == [f"class:{offering.pk}"]
            assert params["recipients"] == ["custom:mine@example.com"]

        def it_lists_two_students_once_each(instructor, client):
            client.force_login(instructor.user)
            offering = ClassOfferingFactory(instructor=instructor)
            first = RegistrationFactory(class_offering=offering, email="one@example.com")
            second = RegistrationFactory(class_offering=offering, email="two@example.com")
            response = client.post(
                reverse("classes:teach_registrations_email"), data={"registration_ids": [first.pk, second.pk]}
            )
            assert _compose_params(response)["recipients"] == ["custom:one@example.com", "custom:two@example.com"]

        def it_collapses_two_rows_sharing_one_address(instructor, client):
            """One person signing a friend up twice must not become two identical checkboxes."""
            client.force_login(instructor.user)
            offering = ClassOfferingFactory(instructor=instructor)
            first = RegistrationFactory(class_offering=offering, email="shared@example.com")
            second = RegistrationFactory(class_offering=offering, email="SHARED@example.com")
            response = client.post(
                reverse("classes:teach_registrations_email"), data={"registration_ids": [first.pk, second.pk]}
            )
            assert _compose_params(response)["recipients"] == ["custom:shared@example.com"]

        def it_skips_a_registrant_with_no_way_to_reach_them(instructor, client):
            """No account and no address is nobody to email; the rest of the selection still goes."""
            client.force_login(instructor.user)
            offering = ClassOfferingFactory(instructor=instructor)
            reachable = RegistrationFactory(class_offering=offering, email="reachable@example.com")
            unreachable = RegistrationFactory(class_offering=offering, email="drop@example.com")
            Registration.objects.filter(pk=unreachable.pk).update(email="")
            response = client.post(
                reverse("classes:teach_registrations_email"),
                data={"registration_ids": [reachable.pk, unreachable.pk]},
            )
            assert _compose_params(response)["recipients"] == ["custom:reachable@example.com"]


def describe_registrations_tab_page():
    def it_offers_the_composer_button_and_no_inline_message_form(instructor, client):
        client.force_login(instructor.user)
        offering = ClassOfferingFactory(instructor=instructor)
        RegistrationFactory(class_offering=offering, first_name="Ada", last_name="Kiln")
        html = client.get(reverse("classes:teach_registrations")).content.decode()
        assert ">Email selected students</button>" in html
        assert 'name="subject"' not in html
        assert 'name="bcc_self"' not in html
