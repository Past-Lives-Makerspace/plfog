"""The five legacy events that should always email, and the invite-accepted period.

Every event seeded into the registry from ``core/triggers.py`` inherited ``EMAIL = OFF``
because no legacy :class:`core.triggers.Trigger` ever set ``force_email`` /
``email_default`` — so a cancelled class, a refund, a tab charge an admin added, a tab
near its limit, and an expiring lease all reached members as a bell row only. These
specs pin the fix:

* each of the five now declares ``EMAIL = FORCED`` and actually puts a message in the
  outbox when emitted through its real call site;
* the two class events reach a **guest** registrant (no linked ``Member``, therefore
  invisible to the ``registrant`` / ``all_active_members`` resolvers) via ``email_to``;
* ``Invite.mark_accepted`` carries a per-invite ``period``, so a second invite's
  acceptance is not swallowed as a ledger duplicate of the first.

Emails are asserted on ``django.core.mail.outbox`` (the locmem backend the test env
installs); bell rows on :class:`core.models.Notification`.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.core.management import call_command
from django.db.models.signals import post_save
from django.utils import timezone
from factory.django import mute_signals

from classes.factories import ClassOfferingFactory, ClassSessionFactory, RegistrationFactory, UserFactory
from classes.models import ClassOffering, Registration
from core.events.registry import Channel, ChannelDefault, get_event
from core.models import Invite, Notification
from membership.models import Member
from tests.billing.factories import BillingSettingsFactory, ProductFactory, TabFactory
from tests.membership.factories import LeaseFactory, MemberFactory

pytestmark = pytest.mark.django_db

# The five legacy events Josh decided must always email.
FORCED_EMAIL_EVENTS = (
    "class_cancelled",
    "refund_issued",
    "tab_entry_added",
    "tab_approaching_limit",
    "lease_expiring",
)


def _linked_member(*, email: str, username: str) -> Member:
    """A Member with a linked, email-bearing User so a resolver can address it.

    Signals are muted while creating the User so ``ensure_user_has_member`` does not
    auto-create a second Member that would collide on the one-to-one ``user`` key.
    """
    member = MemberFactory(_pre_signup_email=email)
    with mute_signals(post_save):
        user = User.objects.create_user(username=username, email=email)
    member.user = user
    member.save(update_fields=["user"])
    return member


def _emails_to(address: str) -> list[mail.EmailMessage]:
    return [message for message in mail.outbox if address in message.to]


def _invite(email: str, inviter: User) -> Invite:
    return Invite.objects.create(email=email, invited_by=inviter)


def describe_forced_email_registry():
    def it_declares_email_forced_for_every_transactional_event():
        for key in FORCED_EMAIL_EVENTS:
            spec = get_event(key).channel(Channel.EMAIL)
            assert spec is not None, f"{key} declares no EMAIL channel"
            assert spec.default is ChannelDefault.FORCED, f"{key} EMAIL is {spec.default}, expected FORCED"

    def it_short_circuits_the_preference_check_for_a_member_who_opted_out():
        from core.events import preferences
        from core.models import NotificationPreference

        user = UserFactory()
        for key in FORCED_EMAIL_EVENTS:
            NotificationPreference.objects.create(user=user, event_key=key, channel=Channel.EMAIL.value, enabled=False)
            assert preferences.wants(user, key, Channel.EMAIL) is True


def describe_class_cancelled_email():
    def it_emails_the_member_who_booked_the_class():
        member = _linked_member(email="booked@example.com", username="booked_member")
        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED, title="Lost-Wax Casting")
        RegistrationFactory(
            class_offering=offering,
            member=member,
            email="booked@example.com",
            status=Registration.Status.CONFIRMED,
        )
        mail.outbox.clear()

        offering.archive()

        sent = _emails_to("booked@example.com")
        assert len(sent) == 1
        assert "Lost-Wax Casting" in sent[0].subject

    def it_emails_a_guest_registrant_with_no_member_account():
        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED, title="Blacksmithing Basics")
        RegistrationFactory(
            class_offering=offering,
            member=None,
            email="guest@example.com",
            status=Registration.Status.CONFIRMED,
        )
        mail.outbox.clear()

        offering.archive()

        sent = _emails_to("guest@example.com")
        assert len(sent) == 1
        assert "Blacksmithing Basics" in sent[0].subject

    def it_names_the_class_and_its_date_in_the_body():
        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED, title="Wheel Throwing")
        ClassSessionFactory(class_offering=offering, starts_at=timezone.now() + timedelta(days=9))
        RegistrationFactory(
            class_offering=offering,
            member=None,
            email="dated@example.com",
            status=Registration.Status.CONFIRMED,
        )
        mail.outbox.clear()

        offering.archive()

        body = _emails_to("dated@example.com")[0].body
        assert "Wheel Throwing" in body
        assert offering.cancellation_date_label in body
        assert "[missing:" not in body

    def it_does_not_email_a_registrant_who_already_cancelled():
        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)
        RegistrationFactory(
            class_offering=offering,
            member=None,
            email="gone@example.com",
            status=Registration.Status.CANCELLED,
        )
        mail.outbox.clear()

        offering.archive()

        assert _emails_to("gone@example.com") == []

    def it_never_emails_the_whole_membership():
        # The event resolves ALL_ACTIVE_MEMBERS for the bell row; the email must stay
        # with the registrants, so an uninvolved active member gets a bell and no mail.
        bystander_user = UserFactory(last_login=timezone.now(), email="bystander@example.com")
        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)
        RegistrationFactory(
            class_offering=offering,
            member=None,
            email="onlyme@example.com",
            status=Registration.Status.CONFIRMED,
        )
        mail.outbox.clear()

        offering.archive()

        assert _emails_to("bystander@example.com") == []
        assert Notification.objects.filter(trigger="class_cancelled", user=bystander_user).exists()

    def describe_when_nobody_booked_the_class():
        def it_sends_no_email_at_all():
            UserFactory(last_login=timezone.now(), email="quiet@example.com")
            offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)
            mail.outbox.clear()

            offering.archive()

            assert mail.outbox == []


def describe_refund_issued_email():
    def it_emails_the_member_the_refund_concerns():
        member = _linked_member(email="refunded@example.com", username="refunded_member")
        offering = ClassOfferingFactory(title="Screen Printing")
        registration = RegistrationFactory(
            class_offering=offering,
            member=member,
            email="refunded@example.com",
            status=Registration.Status.CONFIRMED,
            amount_paid_cents=6500,
        )
        mail.outbox.clear()

        registration.status = Registration.Status.REFUNDED
        registration.save()

        sent = _emails_to("refunded@example.com")
        assert len(sent) == 1
        assert "Screen Printing" in sent[0].subject
        assert "$65.00" in sent[0].body
        assert "[missing:" not in sent[0].body

    def it_emails_a_guest_registrant_with_no_member_account():
        registration = RegistrationFactory(
            member=None,
            email="guestrefund@example.com",
            status=Registration.Status.CONFIRMED,
            amount_paid_cents=2500,
        )
        mail.outbox.clear()

        registration.status = Registration.Status.REFUNDED
        registration.save()

        sent = _emails_to("guestrefund@example.com")
        assert len(sent) == 1
        assert "$25.00" in sent[0].body

    def it_still_writes_the_bell_row_for_a_linked_member():
        member = _linked_member(email="bell@example.com", username="bell_member")
        registration = RegistrationFactory(
            class_offering=ClassOfferingFactory(),
            member=member,
            email="bell@example.com",
            status=Registration.Status.CONFIRMED,
        )

        registration.status = Registration.Status.REFUNDED
        registration.save()

        assert Notification.objects.filter(trigger="refund_issued", user=member.user).exists()


def describe_tab_email():
    @pytest.fixture
    def tab():
        BillingSettingsFactory()
        member = _linked_member(email="tabholder@example.com", username="tab_member")
        return TabFactory(member=member)

    def it_emails_the_member_when_an_admin_adds_an_entry(tab):
        admin = UserFactory()
        product = ProductFactory()
        mail.outbox.clear()

        tab.add_entry(description="Bandsaw blade", amount=Decimal("18.00"), added_by=admin, product=product)

        sent = _emails_to("tabholder@example.com")
        assert len(sent) == 1
        assert "$18.00" in sent[0].subject
        assert "Bandsaw blade" in sent[0].body
        assert "[missing:" not in sent[0].body

    def it_emails_the_member_when_the_tab_nears_its_limit(tab):
        admin = UserFactory()
        product = ProductFactory()
        mail.outbox.clear()

        # 80% of the $200 default limit trips the warning.
        tab.add_entry(description="Kiln firing", amount=Decimal("160.00"), added_by=admin, product=product)

        subjects = [message.subject for message in _emails_to("tabholder@example.com")]
        assert any("near its limit" in subject for subject in subjects)
        limit_email = next(message for message in mail.outbox if "near its limit" in message.subject)
        assert "$160.00" in limit_email.body
        assert "[missing:" not in limit_email.body

    def it_stays_quiet_for_a_self_service_entry(tab):
        product = ProductFactory()
        mail.outbox.clear()

        tab.add_entry(description="Self swipe", amount=Decimal("5.00"), is_self_service=True, product=product)

        assert _emails_to("tabholder@example.com") == []


def describe_lease_expiring_email():
    def it_emails_the_tenant_thirty_days_out():
        member = _linked_member(email="tenant@example.com", username="tenant_member")
        LeaseFactory(tenant_obj=member, end_date=timezone.now().date() + timedelta(days=30))
        mail.outbox.clear()

        call_command("send_lease_expiry_reminders")

        sent = _emails_to("tenant@example.com")
        assert len(sent) == 1
        assert "lease ends" in sent[0].subject
        assert "[missing:" not in sent[0].body

    def it_sends_only_once_across_reruns():
        member = _linked_member(email="oncetenant@example.com", username="once_tenant")
        LeaseFactory(tenant_obj=member, end_date=timezone.now().date() + timedelta(days=30))
        mail.outbox.clear()

        call_command("send_lease_expiry_reminders")
        call_command("send_lease_expiry_reminders")

        assert len(_emails_to("oncetenant@example.com")) == 1


def describe_invite_accepted_period():
    def it_notifies_the_inviter_for_every_invite_not_just_the_first():
        # A blank ``period`` collapses every acceptance onto one EventDelivery slot, so
        # only the first invite an admin ever sent would ever notify them.
        inviter = UserFactory()
        first = _invite("first@example.com", inviter)
        second = _invite("second@example.com", inviter)

        first.mark_accepted()
        second.mark_accepted()

        assert Notification.objects.filter(trigger="invite_accepted", user=inviter).count() == 2

    def it_stays_idempotent_for_a_repeated_acceptance_of_one_invite():
        inviter = UserFactory()
        invite = _invite("repeat@example.com", inviter)

        invite.mark_accepted()
        invite.mark_accepted()

        assert Notification.objects.filter(trigger="invite_accepted", user=inviter).count() == 1
