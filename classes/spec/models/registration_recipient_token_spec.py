"""BDD specs for a registration's announcement-composer recipient token.

The teaching portal's Registrations tab hands its ticked rows to the composer as recipient
values. The mapping has to match how ``hub.forms.announcement_recipient_choices`` builds the
same roster, or a handed-over token would land on no checkbox at all.
"""

from __future__ import annotations

import pytest

from classes.factories import ClassOfferingFactory, RegistrationFactory, UserFactory
from classes.models import Registration

pytestmark = pytest.mark.django_db


def _linked_registration(email: str = "ada@example.com"):
    """A registration whose registrant has a real app account."""
    from membership.models import Member

    user = UserFactory(username=email, email=email)
    member = Member.objects.get(user=user)
    return RegistrationFactory(email=email, member=member), user


def describe_announcement_recipient_token():
    def it_uses_the_account_when_the_registrant_has_one(db):
        registration, user = _linked_registration()
        assert registration.announcement_recipient_token == f"user:{user.pk}"

    def it_falls_back_to_the_address_for_a_guest_checkout(db):
        registration = RegistrationFactory(email="Guest@Example.com", member=None)
        assert registration.announcement_recipient_token == "custom:guest@example.com"

    def it_falls_back_to_the_address_for_a_member_with_no_login(db):
        """An Airtable-imported member who never signed up has a Member but no User."""
        from tests.membership.factories import MemberFactory

        member = MemberFactory(user=None)
        registration = RegistrationFactory(email="unlinked@example.com", member=member)
        assert registration.announcement_recipient_token == "custom:unlinked@example.com"

    def it_is_empty_when_there_is_no_way_to_reach_them(db):
        registration = RegistrationFactory(email="gone@example.com", member=None)
        Registration.objects.filter(pk=registration.pk).update(email="")
        registration.refresh_from_db()
        assert registration.announcement_recipient_token == ""


def describe_announcement_recipient_tokens():
    def it_keeps_the_order_it_was_given(db):
        offering = ClassOfferingFactory()
        first = RegistrationFactory(class_offering=offering, email="b@example.com")
        second = RegistrationFactory(class_offering=offering, email="a@example.com")
        assert Registration.announcement_recipient_tokens([first, second]) == [
            "custom:b@example.com",
            "custom:a@example.com",
        ]

    def it_collapses_two_rows_sharing_one_address(db):
        offering = ClassOfferingFactory()
        first = RegistrationFactory(class_offering=offering, email="same@example.com")
        second = RegistrationFactory(class_offering=offering, email="SAME@example.com")
        assert Registration.announcement_recipient_tokens([first, second]) == ["custom:same@example.com"]

    def it_drops_anyone_it_cannot_reach(db):
        offering = ClassOfferingFactory()
        reachable = RegistrationFactory(class_offering=offering, email="here@example.com")
        unreachable = RegistrationFactory(class_offering=offering, email="nowhere@example.com")
        Registration.objects.filter(pk=unreachable.pk).update(email="")
        unreachable.refresh_from_db()
        assert Registration.announcement_recipient_tokens([reachable, unreachable]) == ["custom:here@example.com"]

    def it_returns_nothing_for_nobody(db):
        assert Registration.announcement_recipient_tokens([]) == []


def describe_can_receive_class_announcement():
    """Guards the reviewer's blocker: a token the composer's roster cannot match is worse than
    no token at all, because an empty pre-selection means "everyone"."""

    @pytest.mark.parametrize(
        "status",
        [Registration.Status.CONFIRMED, Registration.Status.WAITLISTED],
    )
    def it_reaches_a_confirmed_or_waitlisted_student(db, status):
        assert RegistrationFactory(status=status).can_receive_class_announcement is True

    @pytest.mark.parametrize(
        "status",
        [
            Registration.Status.PENDING,
            Registration.Status.CANCELLED,
            Registration.Status.REFUNDED,
        ],
    )
    def it_cannot_reach_anyone_else(db, status):
        assert RegistrationFactory(status=status).can_receive_class_announcement is False

    def it_cannot_reach_a_confirmed_student_with_no_address(db):
        registration = RegistrationFactory(status=Registration.Status.CONFIRMED)
        Registration.objects.filter(pk=registration.pk).update(email="")
        registration.refresh_from_db()
        assert registration.can_receive_class_announcement is False

    def it_agrees_with_the_composers_own_roster(db):
        """The two must not drift: this property decides what we hand over, that query decides
        what the composer will accept."""
        offering = ClassOfferingFactory()
        for status in Registration.Status:
            RegistrationFactory(class_offering=offering, status=status)
        roster_pks = {r.pk for r in offering.announcement_recipients(include_waitlist=True)}
        for registration in offering.registrations.all():
            assert registration.can_receive_class_announcement is (registration.pk in roster_pks), (
                f"{registration.status} disagrees"
            )


def describe_roster_name():
    def it_reads_as_the_roster_shows_it(db):
        registration = RegistrationFactory(first_name="Ada", last_name="Kiln")
        assert registration.roster_name == "Ada Kiln"

    def it_falls_back_to_the_address_when_there_is_no_name(db):
        registration = RegistrationFactory(first_name="", last_name="", email="only@example.com")
        assert registration.roster_name == "only@example.com"

    def it_names_the_row_when_there_is_neither(db):
        """It goes into a list of names, so it must never render as an empty gap."""
        registration = RegistrationFactory(first_name="", last_name="", email="gone@example.com")
        Registration.objects.filter(pk=registration.pk).update(email="")
        registration.refresh_from_db()
        assert registration.roster_name == f"registration #{registration.pk}"
