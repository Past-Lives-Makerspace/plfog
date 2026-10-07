"""BDD specs for an Eventbrite buyer finishing their registration on plfog (#652, part 3).

The email each new ticket sends, the finish form on the registration page, the account it
may make, and the roster's Eventbrite badge and unsigned waiver flag. The Eventbrite client
is :class:`FakeEventbrite`; no spec reaches Eventbrite.
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.db import IntegrityError
from django.test import Client
from django.urls import reverse

from classes import eventbrite_orders
from classes.eventbrite_orders import apply_order, eventbrite_offers_account
from classes.factories import (
    ClassOfferingFactory,
    InstructorFactory,
    RegistrationFactory,
    UserFactory,
    _MembershipPlanFactory,
)
from classes.forms import FinishRegistrationForm
from classes.models import ClassOffering, ClassSettings, Registration, RegistrationAnswer, RegistrationQuestion, Waiver
from core.integrations.eventbrite import EventbriteClient
from core.models import SiteConfiguration
from core.services.guest_account import ensure_account_for_registration
from membership.models import Member
from membership.services.provisioning import provision_user_for_member
from tests.classes.eventbrite_fakes import FakeEventbrite, attendee, listed_class, order
from tests.membership.factories import MemberFactory

pytestmark = pytest.mark.django_db

Status = Registration.Status
EVENTBRITE = Registration.Source.EVENTBRITE
SIGNED = {"liability_signature": "Ada Lovelace", "accepts_liability": "on"}


@pytest.fixture(autouse=True)
def membership_plan() -> None:
    _MembershipPlanFactory()


@pytest.fixture
def eventbrite() -> Iterator[FakeEventbrite]:
    fake = FakeEventbrite()
    with patch.object(EventbriteClient, "from_settings", return_value=fake):
        yield fake


@pytest.fixture
def alerts() -> Iterator[None]:
    with (
        patch.object(eventbrite_orders, "send_eventbrite_oversold_alert"),
        patch.object(eventbrite_orders, "send_eventbrite_shared_email_alert"),
        patch.object(eventbrite_orders, "emit_instructor_new_registration"),
        patch.object(eventbrite_orders, "send_admin_registration_notification"),
    ):
        yield


def _set_mode(mode: str) -> None:
    site = SiteConfiguration.load()
    site.registration_mode = mode
    site.save()


def _ticket(email: str = "ada@example.com", **kwargs: object) -> Registration:
    return RegistrationFactory(
        source=EVENTBRITE,
        email=email,
        first_name="Ada",
        last_name="Lovelace",
        status=Status.CONFIRMED,
        member=None,
        eventbrite_order_id="o-1",
        eventbrite_attendee_id=f"a-{email}",
        **kwargs,
    )


def _page(client: Client, registration: Registration) -> str:
    return client.get(reverse("classes:my_registration", args=[registration.self_serve_token])).content.decode()


def _finish(client: Client, registration: Registration, **data: str):  # noqa: ANN202 - test response
    url = reverse("classes:my_registration_finish", args=[registration.self_serve_token])
    return client.post(url, {**SIGNED, **data}, REMOTE_ADDR="203.0.113.7")


def describe_the_email_to_finish_registering():
    def it_goes_to_every_new_ticket_with_a_link_to_its_registration(eventbrite: FakeEventbrite, alerts: None):
        listed_class(title="Intro to Casting")
        eventbrite.orders["o-1"] = order(
            "o-1", attendee("a-1", email="ada@example.com"), attendee("a-2", first="Grace", email="grace@example.com")
        )

        apply_order("o-1")

        assert sorted(m.to[0] for m in mail.outbox) == ["ada@example.com", "grace@example.com"]
        for registration in Registration.objects.filter(eventbrite_order_id="o-1"):
            sent = next(m for m in mail.outbox if m.to == [registration.email])
            assert sent.subject == "Finish registering for Intro to Casting"
            assert reverse("classes:my_registration", args=[registration.self_serve_token]) in sent.body
            assert "Create a free Past Lives account" in sent.body
            assert "another seat on your order" not in sent.body

    def it_sends_a_seat_alias_ticket_to_the_buyer_to_pass_on(eventbrite: FakeEventbrite, alerts: None):
        offering = listed_class()
        RegistrationFactory(class_offering=offering, email="ada@example.com", status=Status.CONFIRMED)
        eventbrite.orders["o-1"] = order("o-1", attendee("a-1"))

        apply_order("o-1")

        assert Registration.objects.get(eventbrite_attendee_id="a-1").email == "ada+seat2@example.com"
        [sent] = mail.outbox
        assert sent.to == ["ada@example.com"]
        assert "another seat on your order" in sent.body
        assert "Create a free Past Lives account" not in sent.body

    def it_lists_the_photo_release_and_questions_when_the_class_asks_for_them(eventbrite: FakeEventbrite, alerts: None):
        listed_class(requires_model_release=True)
        RegistrationQuestion.objects.create(prompt="Experience?")
        eventbrite.orders["o-1"] = order("o-1", attendee("a-1"))

        apply_order("o-1")

        [sent] = mail.outbox
        assert "and the photo release" in sent.body
        assert "Answer a few short questions" in sent.body
        assert "3. Create a free Past Lives account" in sent.body

    def it_sends_nothing_when_the_order_is_delivered_again(eventbrite: FakeEventbrite, alerts: None):
        listed_class()
        eventbrite.orders["o-1"] = order("o-1", attendee("a-1"))

        apply_order("o-1")
        apply_order("o-1")

        assert len(mail.outbox) == 1

    def it_sends_nothing_for_a_ticket_already_refunded(eventbrite: FakeEventbrite, alerts: None):
        listed_class()
        eventbrite.orders["o-1"] = order("o-1", attendee("a-1", refunded=True))

        apply_order("o-1")

        assert mail.outbox == []


def describe_the_registration_page():
    def it_shows_the_finish_form_on_an_unsigned_eventbrite_ticket(client: Client):
        html = _page(client, _ticket())

        assert 'id="finish-form"' in html
        assert 'name="liability_signature"' in html
        assert 'name="create_account"' in html
        assert 'name="model_release_signature"' not in html

    def it_asks_for_the_photo_release_and_the_questions_when_the_class_does(client: Client):
        question = RegistrationQuestion.objects.create(prompt="Experience?")
        offering = ClassOfferingFactory(requires_model_release=True)

        html = _page(client, _ticket(class_offering=offering))

        assert 'name="model_release_signature"' in html
        assert f'name="custom_q_{question.pk}"' in html

    def it_offers_no_account_on_a_seat_alias(client: Client):
        html = _page(client, _ticket(email="ada+seat2@example.com"))

        assert 'id="finish-form"' in html
        assert 'name="create_account"' not in html

    def it_shows_no_form_on_a_site_booking(client: Client):
        registration = RegistrationFactory(status=Status.CONFIRMED)

        assert 'id="finish-form"' not in _page(client, registration)

    def it_shows_no_form_once_the_waiver_is_signed(client: Client):
        registration = _ticket()
        Waiver.objects.create(
            registration=registration, kind=Waiver.Kind.LIABILITY, waiver_text="t", signature_text="s"
        )

        assert 'id="finish-form"' not in _page(client, registration)

    def it_shows_no_form_on_a_refunded_ticket(client: Client):
        registration = _ticket()
        Registration.objects.filter(pk=registration.pk).update(status=Status.REFUNDED)

        assert 'id="finish-form"' not in _page(client, registration)


def describe_finishing():
    def it_records_the_waiver_exactly_as_the_site_form_does(client: Client):
        registration = _ticket()

        response = _finish(client, registration)

        assert response.status_code == 302
        assert response.url == reverse("classes:my_registration", args=[registration.self_serve_token])
        [waiver] = registration.waivers.all()
        assert waiver.kind == Waiver.Kind.LIABILITY
        assert waiver.waiver_text == ClassSettings.load().liability_waiver_text
        assert waiver.signature_text == "Ada Lovelace"
        assert waiver.ip_address == "203.0.113.7"

    def it_records_the_photo_release_when_the_class_requires_it(client: Client):
        registration = _ticket(class_offering=ClassOfferingFactory(requires_model_release=True))

        _finish(client, registration, model_release_signature="Ada L", accepts_model_release="on")

        release = registration.waivers.get(kind=Waiver.Kind.MODEL_RELEASE)
        assert release.waiver_text == ClassSettings.load().model_release_waiver_text
        assert release.signature_text == "Ada L"

    def it_refuses_without_the_photo_release_when_the_class_requires_it(client: Client):
        registration = _ticket(class_offering=ClassOfferingFactory(requires_model_release=True))

        response = _finish(client, registration, model_release_signature="Ada L")

        assert response.status_code == 200
        assert "Photo release acceptance is required" in response.content.decode()
        assert not registration.waivers.exists()

    def it_saves_the_answers_to_the_questions(client: Client):
        question = RegistrationQuestion.objects.create(prompt="Experience?")
        registration = _ticket()

        _finish(client, registration, **{f"custom_q_{question.pk}": "Some brazing"})

        assert list(
            RegistrationAnswer.objects.filter(registration=registration).values_list("answer_text", flat=True)
        ) == ["Some brazing"]

    def it_shows_the_form_again_with_errors_when_the_signature_is_missing(client: Client):
        registration = _ticket()

        response = _finish(client, registration, liability_signature="")

        assert response.status_code == 200
        assert 'id="finish-form"' in response.content.decode()
        assert not registration.waivers.exists()

    def it_saves_nothing_more_on_a_second_submit(client: Client):
        registration = _ticket()

        _finish(client, registration)
        response = _finish(client, registration, liability_signature="Someone Else")

        assert response.status_code == 302
        assert list(registration.waivers.values_list("signature_text", flat=True)) == ["Ada Lovelace"]

    def it_treats_a_submit_that_lost_the_race_as_finished(client: Client):
        registration = _ticket()

        with patch.object(FinishRegistrationForm, "save_to", side_effect=IntegrityError):
            response = _finish(client, registration)

        assert response.status_code == 302
        assert get_user_model().objects.count() == 0

    def it_ignores_a_site_booking(client: Client):
        registration = RegistrationFactory(status=Status.CONFIRMED)

        response = _finish(client, registration)

        assert response.status_code == 302
        assert not registration.waivers.exists()

    def it_takes_only_a_post(client: Client):
        url = reverse("classes:my_registration_finish", args=[_ticket().self_serve_token])

        assert client.get(url).status_code == 405


def describe_the_account():
    def it_makes_a_guest_account_linked_to_the_registration(client: Client):
        _set_mode(SiteConfiguration.RegistrationMode.OPEN)
        registration = _ticket()

        _finish(client, registration, create_account="on")

        registration.refresh_from_db()
        assert registration.create_account is True
        assert registration.member is not None
        assert registration.member.status == Member.Status.GUEST
        assert registration.member.user.email == "ada@example.com"

    def it_makes_one_for_an_eventbrite_ticket_even_under_invite_only(client: Client):
        _set_mode(SiteConfiguration.RegistrationMode.INVITE_ONLY)
        registration = _ticket()

        assert 'name="create_account"' in _page(client, registration)
        _finish(client, registration, create_account="on")

        registration.refresh_from_db()
        assert registration.member is not None
        assert registration.member.status == Member.Status.GUEST

    def it_still_makes_none_for_a_site_booking_under_invite_only():
        _set_mode(SiteConfiguration.RegistrationMode.INVITE_ONLY)
        registration = RegistrationFactory(email="site@example.com", create_account=True, member=None)

        ensure_account_for_registration(registration)

        assert not get_user_model().objects.filter(email="site@example.com").exists()

    def it_links_an_existing_account_and_keeps_its_status(client: Client):
        existing = MemberFactory(_pre_signup_email="ada@example.com", status=Member.Status.ACTIVE)
        provision_user_for_member(existing)
        registration = _ticket()

        _finish(client, registration, create_account="on")

        registration.refresh_from_db()
        existing.refresh_from_db()
        assert registration.member == existing
        assert existing.status == Member.Status.ACTIVE

    def it_makes_none_when_the_box_is_unticked(client: Client):
        registration = _ticket()

        _finish(client, registration)

        assert registration.waivers.exists()
        assert not get_user_model().objects.filter(email="ada@example.com").exists()

    def it_never_makes_one_on_a_seat_alias(client: Client):
        registration = _ticket(email="ada+seat2@example.com")

        _finish(client, registration, create_account="on")

        assert registration.waivers.exists()
        assert get_user_model().objects.count() == 0

    def it_is_offered_to_any_eventbrite_address_but_a_seat_alias():
        assert eventbrite_offers_account(_ticket()) is True
        assert eventbrite_offers_account(_ticket(email="ada+seat12@example.com")) is False
        assert eventbrite_offers_account(_ticket(email="seat2@example.com")) is True


def describe_the_roster():
    @pytest.fixture
    def offering(client: Client) -> ClassOffering:
        user = UserFactory(username="teacher")
        offering = ClassOfferingFactory(instructor=InstructorFactory(user=user, instructor_slug="teacher"), capacity=12)
        client.force_login(user)
        return offering

    def _roster(client: Client, offering: ClassOffering) -> str:
        return client.get(reverse("classes:teach_class_registrations", args=[offering.pk])).content.decode()

    def it_marks_an_eventbrite_row_and_flags_its_unsigned_waiver(client: Client, offering: ClassOffering):
        _ticket(class_offering=offering)

        html = _roster(client, offering)

        assert 'data-roster-source="eventbrite"' in html
        assert 'data-roster-flag="no-waiver"' in html

    def it_drops_the_flag_once_the_waiver_is_signed(client: Client, offering: ClassOffering):
        registration = _ticket(class_offering=offering)
        Waiver.objects.create(
            registration=registration, kind=Waiver.Kind.LIABILITY, waiver_text="t", signature_text="s"
        )

        html = _roster(client, offering)

        assert 'data-roster-source="eventbrite"' in html
        assert 'data-roster-flag="no-waiver"' not in html

    def it_leaves_a_site_row_unmarked_even_without_a_waiver(client: Client, offering: ClassOffering):
        RegistrationFactory(class_offering=offering, status=Status.CONFIRMED)

        html = _roster(client, offering)

        assert 'data-roster-source="eventbrite"' not in html
        assert 'data-roster-flag="no-waiver"' not in html


def describe_the_finish_form():
    def it_reports_no_account_wanted_when_the_box_was_not_offered():
        form = FinishRegistrationForm(
            SIGNED | {"create_account": "on"}, offering=ClassOfferingFactory(), offers_account=False
        )

        assert form.is_valid()
        assert form.wants_account is False
