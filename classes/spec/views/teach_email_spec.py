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
    return RegistrationFactory(
        class_offering=offering,
        email=email,
        first_name=first_name,
        member=member,
        status=Registration.Status.CONFIRMED,
    )


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

        def it_refuses_a_selection_of_students_it_cannot_reach(instructor, client):
            """The reviewer's blocker: a cancelled pick used to arm the WHOLE roster.

            Its token matches no checkbox on the composer's roster, so the pre-selection came
            out empty, and an empty pre-selection means "everyone" — one click from emailing
            the whole class. It has to be a refusal, not a silent widening.
            """
            client.force_login(instructor.user)
            offering = ClassOfferingFactory(instructor=instructor)
            cancelled = RegistrationFactory(
                class_offering=offering, email="gone@example.com", status=Registration.Status.CANCELLED
            )
            RegistrationFactory(class_offering=offering, email="a@example.com", status=Registration.Status.CONFIRMED)
            response = client.post(
                reverse("classes:teach_registrations_email"),
                data={"registration_ids": [cancelled.pk]},
                follow=True,
            )
            assert response.redirect_chain[0][0] == reverse("classes:teach_registrations")
            messages = [str(m) for m in response.context["messages"]]
            assert "Those students can't be emailed from here." in messages[0]
            assert "You can only email confirmed students and people on the waitlist." in messages[0]

        def it_refuses_a_student_who_has_not_paid_yet(instructor, client):
            """PENDING is the likeliest reason an instructor emails a subset in the first place."""
            client.force_login(instructor.user)
            offering = ClassOfferingFactory(instructor=instructor)
            unpaid = RegistrationFactory(
                class_offering=offering, email="unpaid@example.com", status=Registration.Status.PENDING
            )
            response = client.post(reverse("classes:teach_registrations_email"), data={"registration_ids": [unpaid.pk]})
            assert response["Location"] == reverse("classes:teach_registrations")

        def it_refuses_a_selection_spanning_two_classes(instructor, client):
            client.force_login(instructor.user)
            one = RegistrationFactory(
                class_offering=ClassOfferingFactory(instructor=instructor, slug="one"),
                status=Registration.Status.CONFIRMED,
            )
            two = RegistrationFactory(
                class_offering=ClassOfferingFactory(instructor=instructor, slug="two"),
                status=Registration.Status.CONFIRMED,
            )
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
            reg = RegistrationFactory(
                class_offering=offering, email="guest@example.com", status=Registration.Status.CONFIRMED
            )
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
            reg = RegistrationFactory(
                class_offering=offering, email="Guest@Example.com", status=Registration.Status.CONFIRMED
            )
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
            mine = RegistrationFactory(
                class_offering=offering, email="mine@example.com", status=Registration.Status.CONFIRMED
            )
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
            first = RegistrationFactory(
                class_offering=offering, email="one@example.com", status=Registration.Status.CONFIRMED
            )
            second = RegistrationFactory(
                class_offering=offering, email="two@example.com", status=Registration.Status.CONFIRMED
            )
            response = client.post(
                reverse("classes:teach_registrations_email"), data={"registration_ids": [first.pk, second.pk]}
            )
            assert _compose_params(response)["recipients"] == ["custom:one@example.com", "custom:two@example.com"]

        def it_collapses_two_rows_sharing_one_address(instructor, client):
            """One person signing a friend up twice must not become two identical checkboxes."""
            client.force_login(instructor.user)
            offering = ClassOfferingFactory(instructor=instructor)
            first = RegistrationFactory(
                class_offering=offering, email="shared@example.com", status=Registration.Status.CONFIRMED
            )
            second = RegistrationFactory(
                class_offering=offering, email="SHARED@example.com", status=Registration.Status.CONFIRMED
            )
            response = client.post(
                reverse("classes:teach_registrations_email"), data={"registration_ids": [first.pk, second.pk]}
            )
            assert _compose_params(response)["recipients"] == ["custom:shared@example.com"]

        def it_carries_the_reachable_half_and_names_the_rest(instructor, client):
            """Dropping half a selection without a word is the same defect, just smaller."""
            client.force_login(instructor.user)
            offering = ClassOfferingFactory(instructor=instructor)
            confirmed = RegistrationFactory(
                class_offering=offering,
                email="going@example.com",
                first_name="Ada",
                last_name="Kiln",
                status=Registration.Status.CONFIRMED,
            )
            cancelled = RegistrationFactory(
                class_offering=offering,
                email="gone@example.com",
                first_name="Bo",
                last_name="Stone",
                status=Registration.Status.CANCELLED,
            )
            response = client.post(
                reverse("classes:teach_registrations_email"),
                data={"registration_ids": [confirmed.pk, cancelled.pk]},
            )
            assert _compose_params(response)["recipients"] == ["custom:going@example.com"]
            follow = client.get(reverse("classes:teach_registrations"))
            notices = [str(m) for m in follow.context["messages"]]
            assert any("Left out: Bo Stone." in m for m in notices)

        def it_says_nothing_extra_when_every_pick_is_going(instructor, client):
            client.force_login(instructor.user)
            offering = ClassOfferingFactory(instructor=instructor)
            reg = RegistrationFactory(
                class_offering=offering, email="all@example.com", status=Registration.Status.CONFIRMED
            )
            client.post(reverse("classes:teach_registrations_email"), data={"registration_ids": [reg.pk]})
            follow = client.get(reverse("classes:teach_registrations"))
            assert [str(m) for m in follow.context["messages"]] == []

        def it_skips_a_registrant_with_no_way_to_reach_them(instructor, client):
            """No account and no address is nobody to email; the rest of the selection still goes."""
            client.force_login(instructor.user)
            offering = ClassOfferingFactory(instructor=instructor)
            reachable = RegistrationFactory(
                class_offering=offering, email="reachable@example.com", status=Registration.Status.CONFIRMED
            )
            unreachable = RegistrationFactory(
                class_offering=offering, email="drop@example.com", status=Registration.Status.CONFIRMED
            )
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
        RegistrationFactory(
            class_offering=offering,
            first_name="Ada",
            last_name="Kiln",
            status=Registration.Status.CONFIRMED,
        )
        html = client.get(reverse("classes:teach_registrations")).content.decode()
        assert ">Email selected students</button>" in html
        assert 'name="subject"' not in html
        assert 'name="bcc_self"' not in html

    def it_does_not_grow_a_query_per_student(instructor, client):
        """``can_receive_class_announcement`` reads ``member.user`` for every row, so the roster
        query has to select_related all the way to the user. Counting the queries the REQUEST
        makes, not a cached instance afterwards: the render warms the FK cache, so a loop over
        the returned rows would report zero either way."""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        from membership.models import Member

        client.force_login(instructor.user)
        offering = ClassOfferingFactory(instructor=instructor)

        def add_linked_student(tag: str) -> None:
            address = f"{tag}@example.com"
            member = Member.objects.get(user=UserFactory(username=address, email=address))
            RegistrationFactory(
                class_offering=offering,
                email=address,
                member=member,
                status=Registration.Status.CONFIRMED,
            )

        def roster_queries() -> int:
            with CaptureQueriesContext(connection) as captured:
                assert client.get(reverse("classes:teach_registrations")).status_code == 200
            return len(captured)

        add_linked_student("one")
        add_linked_student("two")
        roster_queries()  # warm up: the first render of the page primes per-process caches
        with_two = roster_queries()
        add_linked_student("three")
        add_linked_student("four")
        assert roster_queries() == with_two, "the roster tab grew a query per student"

    def it_gives_a_confirmed_student_a_tick_box(instructor, client):
        client.force_login(instructor.user)
        offering = ClassOfferingFactory(instructor=instructor)
        reg = RegistrationFactory(
            class_offering=offering, first_name="Ada", last_name="Kiln", status=Registration.Status.CONFIRMED
        )
        html = client.get(reverse("classes:teach_registrations")).content.decode()
        assert f'name="registration_ids" value="{reg.pk}"' in html

    def it_drops_the_email_controls_when_nobody_on_the_class_can_be_reached(instructor, client):
        """Otherwise the tab offers a button whose only answer is "tick someone first", with
        nothing on the page to tick."""
        client.force_login(instructor.user)
        offering = ClassOfferingFactory(instructor=instructor)
        RegistrationFactory(
            class_offering=offering, first_name="Bo", last_name="Stone", status=Registration.Status.CANCELLED
        )
        html = client.get(reverse("classes:teach_registrations")).content.decode()
        assert "Bo" in html
        assert ">Email selected students</button>" not in html
        assert "Select all in" not in html

    def it_keeps_the_email_controls_when_someone_can_be_reached(instructor, client):
        client.force_login(instructor.user)
        offering = ClassOfferingFactory(instructor=instructor)
        RegistrationFactory(
            class_offering=offering, first_name="Bo", last_name="Stone", status=Registration.Status.CANCELLED
        )
        RegistrationFactory(
            class_offering=offering, first_name="Ada", last_name="Kiln", status=Registration.Status.CONFIRMED
        )
        html = client.get(reverse("classes:teach_registrations")).content.decode()
        assert ">Email selected students</button>" in html
        assert "Select all in" in html

    def it_withholds_the_tick_box_from_a_student_it_cannot_email(instructor, client):
        """The row still shows (it is the roster), but there is nothing to tick."""
        client.force_login(instructor.user)
        offering = ClassOfferingFactory(instructor=instructor)
        reg = RegistrationFactory(
            class_offering=offering,
            first_name="Bo",
            last_name="Stone",
            status=Registration.Status.CANCELLED,
        )
        html = client.get(reverse("classes:teach_registrations")).content.decode()
        assert "Bo" in html
        assert f'name="registration_ids" value="{reg.pk}"' not in html
