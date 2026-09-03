"""The per-member notification-email preference on the event spine.

``notification_email_for`` resolves where an event-driven notification email goes:
the member's chosen VERIFIED address, else the allauth primary mirror
(``user.email``). Both email adapters route through it, and ``emit(extra_emails=…)``
dedupes extra addresses against each recipient's resolved target so a chosen
address never gets the same mail twice. Fail-soft: a deleted or unverified chosen
address silently falls back to the primary — notifications are never stranded.
"""

from __future__ import annotations

import pytest
from allauth.account.models import EmailAddress
from django.contrib.auth.models import User

from core.events.channels import EmailAdapter, Message, ScheduledEmailAdapter, notification_email_for
from core.events.emit import emit
from core.models import TransactionalEmailLog
from tests.membership.factories import GuildFactory, GuildMembershipFactory

pytestmark = pytest.mark.django_db


def _message(**kw):
    base = {"title": "T", "body": "B", "url": "/x/", "trigger_kind": "class_published"}
    base.update(kw)
    return Message(**base)


def _choose(member, email, *, verified=True):
    """Stamp a notification target on a linked member, with its EmailAddress row."""
    EmailAddress.objects.create(user=member.user, email=email, verified=verified, primary=False)
    member.notification_email = email
    member.save(update_fields=["notification_email"])


def describe_notification_email_for():
    def it_returns_the_chosen_address_while_it_is_verified(linked_member):
        member = linked_member(email="primary@example.com")
        _choose(member, "shop@example.com")
        assert notification_email_for(member.user) == "shop@example.com"

    def it_matches_the_stored_value_case_insensitively(linked_member):
        member = linked_member(email="primary@example.com")
        EmailAddress.objects.create(user=member.user, email="shop@example.com", verified=True, primary=False)
        member.notification_email = "Shop@Example.com"
        member.save(update_fields=["notification_email"])
        assert notification_email_for(member.user) == "Shop@Example.com"

    def it_returns_the_primary_when_the_field_is_blank(linked_member):
        member = linked_member(email="primary@example.com")
        assert notification_email_for(member.user) == "primary@example.com"

    def it_falls_back_to_the_primary_when_the_chosen_address_is_unverified(linked_member):
        member = linked_member(email="primary@example.com")
        _choose(member, "shop@example.com", verified=False)
        assert notification_email_for(member.user) == "primary@example.com"

    def it_falls_back_to_the_primary_when_the_chosen_address_was_deleted(linked_member):
        member = linked_member(email="primary@example.com")
        _choose(member, "shop@example.com")
        EmailAddress.objects.filter(user=member.user, email="shop@example.com").delete()
        assert notification_email_for(member.user) == "primary@example.com"

    def it_returns_user_email_for_a_user_with_no_member():
        user = User.objects.create_user(username="nomember_notif", email="lone@example.com")
        assert notification_email_for(user) == "lone@example.com"

    def it_returns_empty_for_a_user_with_no_member_and_no_email():
        user = User.objects.create_user(username="nothing_notif", email="")
        assert notification_email_for(user) == ""


def _delivered_to(trigger: str = "class_published") -> set[str]:
    return set(TransactionalEmailLog.objects.filter(trigger_kind=trigger).values_list("to_email", flat=True))


def describe_email_adapter_targeting():
    def it_delivers_to_the_chosen_notification_address(linked_member):
        member = linked_member(email="primary@example.com")
        _choose(member, "shop@example.com")
        EmailAdapter().deliver(member.user, _message())
        assert _delivered_to() == {"shop@example.com"}

    def it_skips_a_user_whose_resolved_address_is_empty():
        user = User.objects.create_user(username="noaddr_email", email="")
        EmailAdapter().deliver(user, _message())
        assert not TransactionalEmailLog.objects.exists()


def describe_scheduled_email_adapter_targeting():
    def it_delivers_to_the_chosen_notification_address(linked_member):
        member = linked_member(email="primary@example.com")
        _choose(member, "shop@example.com")
        ScheduledEmailAdapter().deliver(member.user, _message())
        assert _delivered_to() == {"shop@example.com"}

    def it_skips_a_user_whose_resolved_address_is_empty():
        user = User.objects.create_user(username="noaddr_sched", email="")
        ScheduledEmailAdapter().deliver(user, _message())
        assert not TransactionalEmailLog.objects.exists()


def describe_extra_emails_dedup_against_notification_targets():
    def it_drops_an_extra_address_equal_to_a_recipients_chosen_target(linked_member):
        guild = GuildFactory()
        member = linked_member(email="primary@example.com")
        _choose(member, "shop@example.com")
        GuildMembershipFactory(guild=guild, member=member)
        emit(
            "guild_announcement",
            context={"guild": guild},
            title="t",
            body="b",
            extra_emails=["SHOP@example.com"],
        )
        assert _delivered_to("guild_announcement") == {"shop@example.com"}

    def it_still_drops_an_extra_address_equal_to_a_recipients_user_email(linked_member):
        guild = GuildFactory()
        member = linked_member(email="primary@example.com")
        _choose(member, "shop@example.com")
        GuildMembershipFactory(guild=guild, member=member)
        emit(
            "guild_announcement",
            context={"guild": guild},
            title="t",
            body="b",
            extra_emails=["primary@example.com"],
        )
        assert _delivered_to("guild_announcement") == {"shop@example.com"}

    def it_keeps_an_unrelated_extra_address(linked_member):
        guild = GuildFactory()
        member = linked_member(email="primary@example.com")
        _choose(member, "shop@example.com")
        GuildMembershipFactory(guild=guild, member=member)
        emit(
            "guild_announcement",
            context={"guild": guild},
            title="t",
            body="b",
            extra_emails=["booster@example.com"],
        )
        assert _delivered_to("guild_announcement") == {"shop@example.com", "booster@example.com"}
