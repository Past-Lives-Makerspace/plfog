"""BDD specs for #894 part 4: three FYI staff notices wait for the 8 AM staff digest.

A new member signing up, a class registration (the Admins' copy) and an equipment booking
tell staff what already happened; nothing decides them. Their email now waits for the 8 AM
digest, one line per signup, registration or booking under the event's own heading, and
every queued line goes out. The bell, push and the #reservations Discord post stay instant.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from unittest.mock import patch

import pytest
from allauth.account.signals import user_signed_up
from django.contrib.auth.models import User
from django.core import mail
from django.test import RequestFactory
from django.utils import timezone

from core.events import resolvers
from core.events.channels import DiscordAdapter, PushAdapter, digest_context
from core.events.email_catalogue import build_email_catalogue
from core.events.registry import Channel, EmailDelivery, get_event
from core.models import EventDelivery, Notification, NotificationPreference
from membership import equipment as equipment_service
from membership.models import Equipment, Member
from tests.core.events.digest_support import next_8am
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentHoursFactory,
    EquipmentStaffMembershipFactory,
    MembershipPlanFactory,
)

pytestmark = pytest.mark.django_db

NOTICES = ("new_member_joined", "class_registration_admin_notice", "equipment.reservation_made")


@pytest.fixture(autouse=True)
def _outbox() -> None:
    mail.outbox = []


def _member(username: str, *, fog_role: str = Member.FogRole.MEMBER) -> Member:
    """An active member with a login and an email."""
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="x")
    member = user.member
    member.status = Member.Status.ACTIVE
    member.fog_role = fog_role
    member.save(update_fields=["status", "fog_role"])
    return member


def _email_on(member: Member, event_key: str) -> None:
    NotificationPreference.objects.update_or_create(
        user=member.user, event_key=event_key, channel=Channel.EMAIL.value, defaults={"enabled": True}
    )


def _morning(address: str) -> list:
    """Send the next 8 AM digest; return every email in the outbox addressed to ``address``."""
    from core.events.scheduler import flush_digests

    flush_digests(next_8am())
    return [m for m in mail.outbox if m.to == [address]]


def _sign_up(username: str) -> User:
    user = User.objects.create_user(username=username, email=f"{username}@example.com")
    user_signed_up.send(sender=User, request=RequestFactory().get("/"), user=user)
    return user


def describe_the_registry():
    @pytest.mark.parametrize("key", NOTICES)
    def it_sends_each_notice_in_the_8_am_digest_with_every_line_included(key: str) -> None:
        assert get_event(key).email_delivery is EmailDelivery.DIGEST
        assert resolvers.has_open_check(key)
        assert resolvers.is_always_included(key)

    def it_shows_the_8_am_digest_pill_on_the_emails_tab() -> None:
        rows = {row.key: row for _category, group in build_email_catalogue() for row in group}
        for key in NOTICES:
            assert rows[key].delivery_label == "8 AM digest"


def describe_new_member_joined():
    def it_rings_the_bell_at_once_and_mails_nothing_until_morning() -> None:
        admin = _member("fogadmin", fog_role=Member.FogRole.ADMIN)
        _email_on(admin, "new_member_joined")
        mail.outbox = []

        _sign_up("join1")

        assert Notification.objects.filter(user=admin.user, trigger="new_member_joined").count() == 1
        assert [m for m in mail.outbox if m.to == [admin.user.email]] == []

    def it_gives_two_signups_the_same_evening_two_lines_under_one_heading() -> None:
        admin = _member("fogadmin2", fog_role=Member.FogRole.ADMIN)
        _email_on(admin, "new_member_joined")
        first = _sign_up("join_a")
        second = _sign_up("join_b")

        periods = set(
            EventDelivery.objects.filter(event_key="new_member_joined", channel=Channel.DIGEST.value).values_list(
                "period", flat=True
            )
        )
        assert periods == {f"signup:{first.pk}", f"signup:{second.pk}"}
        (digest,) = _morning(admin.user.email)
        assert digest.subject == "2 Updates Since the Last Email"
        assert "Here is what happened since the last email. Nothing here needs you to act." in digest.body
        assert digest.body.count("NEW MEMBER JOINED") == 1
        assert "join_a just signed up." in digest.body
        assert "join_b just signed up." in digest.body

    def it_still_pushes_at_once() -> None:
        admin = _member("fogadmin3", fog_role=Member.FogRole.ADMIN)
        NotificationPreference.objects.create(
            user=admin.user, event_key="new_member_joined", channel=Channel.PUSH.value, enabled=True
        )
        with patch.object(PushAdapter, "deliver", return_value=True) as push:
            _sign_up("join_push")
        assert [c.args[0] for c in push.call_args_list] == [admin.user]

    def it_leaves_out_an_admin_who_switched_the_email_off_before_morning() -> None:
        admin = _member("fogadmin4", fog_role=Member.FogRole.ADMIN)
        _email_on(admin, "new_member_joined")
        _sign_up("join_off")
        NotificationPreference.objects.filter(user=admin.user, event_key="new_member_joined").update(enabled=False)

        assert _morning(admin.user.email) == []


def _open_tool() -> Equipment:
    equipment = EquipmentFactory()
    day = timezone.localdate() + timedelta(days=2)
    EquipmentHoursFactory(equipment=equipment, weekday=day.weekday(), start_time=time(9, 0), end_time=time(17, 0))
    return equipment


def _at(hour: int) -> datetime:
    day = timezone.localdate() + timedelta(days=2)
    return timezone.make_aware(datetime.combine(day, time(hour, 0)))


def describe_equipment_reservation_made():
    def it_rings_the_bell_and_posts_to_discord_at_once_and_mails_the_manager_in_the_morning() -> None:
        equipment = _open_tool()
        manager = _member("tool_mgr")
        EquipmentStaffMembershipFactory(equipment=equipment, member=manager)
        _email_on(manager, "equipment.reservation_made")
        booker = _member("tool_booker")
        mail.outbox = []

        with patch.object(DiscordAdapter, "broadcast", return_value=True) as discord:
            equipment_service.reserve(equipment, booker, _at(10), 60)
            equipment_service.reserve(equipment, booker, _at(13), 60)

        posted = [c.args[0].trigger_kind for c in discord.call_args_list]
        assert posted.count("equipment.reservation_made") == 2
        assert Notification.objects.filter(user=manager.user, trigger="equipment.reservation_made").count() == 2
        assert [m for m in mail.outbox if m.to == [manager.user.email]] == []

        (digest,) = _morning(manager.user.email)
        assert digest.subject == "2 Updates Since the Last Email"
        assert digest.body.count("NEW EQUIPMENT RESERVATION") == 1
        assert digest.body.count(f"New reservation on {equipment.name}") == 2

    def it_queues_nothing_for_a_manager_with_the_email_off() -> None:
        equipment = _open_tool()
        manager = _member("tool_mgr_quiet")
        EquipmentStaffMembershipFactory(equipment=equipment, member=manager)

        equipment_service.reserve(equipment, _member("tool_booker2"), _at(10), 60)

        assert Notification.objects.filter(user=manager.user, trigger="equipment.reservation_made").exists()
        assert not EventDelivery.objects.filter(
            event_key="equipment.reservation_made", channel=Channel.DIGEST.value
        ).exists()


def describe_the_morning_subject():
    def it_calls_a_morning_of_notices_updates() -> None:
        rows = [EventDelivery(event_key="new_member_joined", title="New member joined", url="/members/", period="a:1")]
        built = digest_context(rows)
        assert built["subject"] == "1 Update Since the Last Email"
        assert built["heads_up_only"] is True

    def it_keeps_action_needed_when_a_request_shares_the_morning() -> None:
        rows = [
            EventDelivery(event_key="new_member_joined", title="New member joined", url="/members/", period="a:1"),
            EventDelivery(event_key="class_change_requested", title="Change", url="/b", period="x:1"),
        ]
        built = digest_context(rows)
        assert built["subject"] == "Action Needed: 2 Requests Awaiting You"
        assert [section["label"] for section in built["sections"]] == [
            get_event("new_member_joined").label,
            get_event("class_change_requested").label,
        ]

    def it_keeps_the_instructor_code_wording_for_a_morning_of_codes_only() -> None:
        rows = [EventDelivery(event_key="discount_code.instructor_made", title="Ana made A1", url="/a", period="d:1")]
        assert digest_context(rows)["subject"] == "1 Discount Code Made or Changed by Instructors"
