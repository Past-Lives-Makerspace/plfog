"""BDD specs for #894 part 4: two FYI staff notices wait for the 8 AM staff digest.

A new member signing up and an equipment booking tell staff what already happened; nothing
decides them. Their email waits for the 8 AM digest, one line per signup or booking under
the event's own heading, and every queued line goes out. The bell, push and the
#reservations Discord post stay instant. The Admins' class registration notice was the
third until Felix made it instant again (2026-11-17); classes/spec/emails_notification_spec.py
covers it.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import cast
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
from core.events.registry import _DIGEST_EMAIL_EVENTS, Channel, EmailDelivery, get_event
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

NOTICES = ("new_member_joined", "equipment.reservation_made")


@pytest.fixture(autouse=True)
def _outbox() -> None:
    mail.outbox = []


# A request that asks a staff member to act; the digest counts it as one awaiting them.
ASK = "instructor_application_received"


def _member(username: str, *, fog_role: str = Member.FogRole.MEMBER) -> Member:
    """An active member with a login and an email."""
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="x")
    member = user.member
    member.status = Member.Status.ACTIVE
    member.fog_role = fog_role
    member.save(update_fields=["status", "fog_role"])
    return member


def _user(member: Member) -> User:
    """The member's login (every member here is made with one)."""
    assert member.user is not None
    return member.user


def _email_on(member: Member, event_key: str) -> None:
    NotificationPreference.objects.update_or_create(
        user=_user(member), event_key=event_key, channel=Channel.EMAIL.value, defaults={"enabled": True}
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

    def it_gives_every_digest_event_a_line_to_show() -> None:
        """A digest line falls back to the bell's line, so every digest event declares a bell.

        An email-only event (emit_flat_email) has no line to show and must stay instant.
        """
        no_bell = {key for key in _DIGEST_EMAIL_EVENTS if not get_event(key).has_channel(Channel.IN_APP)}
        assert no_bell == set()

    def it_gives_every_digest_event_its_own_heading() -> None:
        """digest_context keys sections by label, so a shared label would drop one event's lines."""
        labels = [get_event(key).label for key in _DIGEST_EMAIL_EVENTS]
        assert len(labels) == len(set(labels))

    def it_shows_the_8_am_digest_pill_on_the_emails_tab() -> None:
        rows = {row.key: row for _category, group in build_email_catalogue() for row in group}
        for key in NOTICES:
            assert rows[key].delivery_label == "8 AM digest"

    def it_shows_the_class_registration_notice_as_instant_again() -> None:
        """Felix's call, 2026-11-17: the Admins hear about each class signup at once."""
        rows = {row.key: row for _category, group in build_email_catalogue() for row in group}
        assert get_event("class_registration_admin_notice").email_delivery is EmailDelivery.INSTANT
        assert not resolvers.has_open_check("class_registration_admin_notice")
        assert rows["class_registration_admin_notice"].delivery_label == "Instant"


def describe_new_member_joined():
    def it_rings_the_bell_at_once_and_mails_nothing_until_morning() -> None:
        admin = _member("fogadmin", fog_role=Member.FogRole.ADMIN)
        _email_on(admin, "new_member_joined")
        mail.outbox = []

        _sign_up("join1")

        assert Notification.objects.filter(user=_user(admin), trigger="new_member_joined").count() == 1
        assert [m for m in mail.outbox if m.to == [_user(admin).email]] == []

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
        (digest,) = _morning(_user(admin).email)
        assert digest.subject == "2 Updates Since the Last Email"
        assert "Here is what happened since the last email. Nothing here needs you to act." in digest.body
        assert digest.body.count("NEW MEMBER JOINED") == 1
        assert "join_a just signed up." in digest.body
        assert "join_b just signed up." in digest.body

    def it_still_pushes_at_once() -> None:
        admin = _member("fogadmin3", fog_role=Member.FogRole.ADMIN)
        NotificationPreference.objects.create(
            user=_user(admin), event_key="new_member_joined", channel=Channel.PUSH.value, enabled=True
        )
        with patch.object(PushAdapter, "deliver", return_value=True) as push:
            _sign_up("join_push")
        assert [c.args[0] for c in push.call_args_list] == [_user(admin)]

    def it_leaves_out_an_admin_who_switched_the_email_off_before_morning() -> None:
        admin = _member("fogadmin4", fog_role=Member.FogRole.ADMIN)
        _email_on(admin, "new_member_joined")
        _sign_up("join_off")
        NotificationPreference.objects.filter(user=_user(admin), event_key="new_member_joined").update(enabled=False)

        assert _morning(_user(admin).email) == []


def _open_tool() -> Equipment:
    equipment = cast("Equipment", EquipmentFactory())
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
        assert Notification.objects.filter(user=_user(manager), trigger="equipment.reservation_made").count() == 2
        assert [m for m in mail.outbox if m.to == [_user(manager).email]] == []

        (digest,) = _morning(_user(manager).email)
        assert digest.subject == "2 Updates Since the Last Email"
        assert digest.body.count("NEW EQUIPMENT RESERVATION") == 1
        assert digest.body.count(f"New reservation on {equipment.name}") == 2

    def it_queues_nothing_for_a_manager_with_the_email_off() -> None:
        equipment = _open_tool()
        manager = _member("tool_mgr_quiet")
        EquipmentStaffMembershipFactory(equipment=equipment, member=manager)

        equipment_service.reserve(equipment, _member("tool_booker2"), _at(10), 60)

        assert Notification.objects.filter(user=_user(manager), trigger="equipment.reservation_made").exists()
        assert not EventDelivery.objects.filter(
            event_key="equipment.reservation_made", channel=Channel.DIGEST.value
        ).exists()


def describe_the_morning_subject():
    def it_calls_a_morning_of_notices_updates() -> None:
        rows = [EventDelivery(event_key="new_member_joined", title="New member joined", url="/members/", period="a:1")]
        built = digest_context(rows)
        assert built["subject"] == "1 Update Since the Last Email"
        assert built["heads_up_only"] is True

    def it_counts_only_requests_as_action_needed_and_adds_the_updates() -> None:
        rows = [
            EventDelivery(event_key="new_member_joined", title="New member joined", url="/members/", period="a:1"),
            EventDelivery(event_key=ASK, title="Ask", url="/b", period="x:1"),
        ]
        built = digest_context(rows)
        assert built["subject"] == "Action Needed: 1 Request Awaiting You, plus 1 Update"
        assert built["intro"] == (
            "Here is what is waiting on you this morning, then what happened since the last email."
        )
        assert built["heads_up_only"] is False

    def it_says_eleven_signups_are_updates_beside_one_class_review() -> None:
        rows = [
            EventDelivery(event_key="new_member_joined", title=f"N{n}", url="/members/", period=f"signup:{n}")
            for n in range(11)
        ]
        rows.append(EventDelivery(event_key="class_review_requested", title="Review", url="/r/1", period="v:1"))
        assert digest_context(rows)["subject"] == "Action Needed: 1 Class Awaiting Review, plus 11 Updates"

    def it_pluralizes_both_sides() -> None:
        rows = [
            EventDelivery(event_key=ASK, title="C1", url="/b1", period="x:1"),
            EventDelivery(event_key=ASK, title="C2", url="/b2", period="x:2"),
            EventDelivery(event_key="new_member_joined", title="N", url="/members/", period="signup:1"),
            EventDelivery(event_key="new_member_joined", title="N", url="/members/", period="signup:2"),
        ]
        assert digest_context(rows)["subject"] == "Action Needed: 2 Requests Awaiting You, plus 2 Updates"

    def it_keeps_the_plain_action_needed_wording_with_no_updates() -> None:
        rows = [EventDelivery(event_key=ASK, title="Ask", url="/b", period="x:1")]
        built = digest_context(rows)
        assert built["subject"] == "Action Needed: 1 Request Awaiting You"
        assert cast("str", built["intro"]).startswith("Here is everything still waiting on you this morning.")

    def it_keeps_the_instructor_code_wording_for_a_morning_of_codes_only() -> None:
        rows = [EventDelivery(event_key="discount_code.instructor_made", title="Ana made A1", url="/a", period="d:1")]
        assert digest_context(rows)["subject"] == "1 Discount Code Made or Changed by Instructors"


def describe_the_morning_order():
    def it_lists_every_request_section_before_any_update_section_in_arrival_order() -> None:
        rows = [
            EventDelivery(event_key="new_member_joined", title="N", url="/members/", period="signup:1"),
            EventDelivery(event_key=ASK, title="C", url="/b", period="x:1"),
            EventDelivery(event_key="equipment.reservation_made", title="E", url="/e/", period="reservation:1:made"),
            EventDelivery(event_key="space.lease_requested", title="S", url="/s#1", period="spacereq:1:submitted"),
            EventDelivery(event_key="new_member_joined", title="N2", url="/members/", period="signup:2"),
        ]
        sections = cast("list[dict[str, str]]", digest_context(rows)["sections"])
        labels = [section["label"] for section in sections]
        assert labels == [
            get_event(ASK).label,
            get_event("space.lease_requested").label,
            get_event("new_member_joined").label,
            get_event("equipment.reservation_made").label,
        ]


def _queued(member: Member, event_key: str, *, title: str, url: str, period: str) -> EventDelivery:
    """A PENDING 8 AM line for ``member``, as the digest adapter stores one."""
    return EventDelivery.objects.create(
        event_key=event_key,
        target_ref=f"user:{_user(member).pk}",
        channel=Channel.DIGEST.value,
        period=period,
        status=EventDelivery.Status.PENDING,
        title=title,
        body="",
        url=url,
    )


def describe_a_line_queued_before_its_event_went_instant():
    """Felix made the Admins' registration notice instant on 2026-11-17; prod still held
    PENDING digest lines for it. They go out once more as updates, and nothing breaks."""

    LEFTOVER = "class_registration_admin_notice"

    def it_sends_the_leftover_as_an_update_beside_a_real_request() -> None:
        admin = _member("leftover_admin", fog_role=Member.FogRole.ADMIN)
        _email_on(admin, ASK)
        applicant = _member("leftover_applicant")
        Member.objects.filter(pk=applicant.pk).update(teaching_applied_at=timezone.now())
        _queued(admin, LEFTOVER, title="Alex Doe registered for Pottery", url="/classes/pottery/", period="reg:7:a")
        _queued(admin, ASK, title="Change to Pottery", url="/b", period=f"member:{applicant.pk}:applied")

        (digest,) = _morning(_user(admin).email)

        assert digest.subject == "Action Needed: 1 Request Awaiting You, plus 1 Update"
        assert "Change to Pottery" in digest.body
        assert "Alex Doe registered for Pottery" in digest.body
        assert digest.body.index("Change to Pottery") < digest.body.index("Alex Doe registered for Pottery")
        assert set(EventDelivery.objects.filter(channel=Channel.DIGEST.value).values_list("status", flat=True)) == {
            EventDelivery.Status.SENT
        }

    def it_calls_a_morning_of_only_leftovers_updates() -> None:
        admin = _member("leftover_admin2", fog_role=Member.FogRole.ADMIN)
        _queued(admin, LEFTOVER, title="Alex Doe registered for Pottery", url="/classes/pottery/", period="reg:7:a")
        _queued(admin, LEFTOVER, title="Sam Park registered for Pottery", url="/classes/pottery/", period="reg:8:a")

        (digest,) = _morning(_user(admin).email)

        assert digest.subject == "2 Updates Since the Last Email"
        assert "Sam Park registered for Pottery" in digest.body

    def it_includes_every_leftover_line_and_never_judges_it_decided() -> None:
        rows = [EventDelivery(event_key=LEFTOVER, url="/c/", period="reg:1:a")]
        assert resolvers.is_leftover(LEFTOVER)
        assert resolvers.open_request_urls(LEFTOVER, rows) == {"/c/"}
        assert resolvers.is_always_included(LEFTOVER)

    def it_still_fails_loudly_for_a_digest_event_with_no_check() -> None:
        checks = {key: check for key, check in resolvers._OPEN_CHECKS.items() if key != ASK}
        with patch.dict(resolvers._OPEN_CHECKS, checks, clear=True), pytest.raises(KeyError):
            resolvers.open_request_urls(ASK, [])
