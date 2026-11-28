"""Waiting orientation requests reach Home, the morning digest and a new Orientor (#1299).

A request waits while it is requested and its time has not passed. On Home the people it was
sent to see it under Needs Attention, and every other staff member of its guild (or of the
guild that owns its equipment, and the item's managers) sees it in the Guild Activity card. The
guild lead and Co-Leads get a line in the next 8 AM staff digest. A new Orientor or item
manager is sent the requests already waiting on the people in their seat.
"""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Any
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape

from core.models import EventDelivery, Notification
from membership import orientations
from membership.models import Equipment, Guild, GuildStaffMembership, Member, OrientationBooking, OrientationSlot
from tests.core.events.digest_support import digest_emails, next_8am
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentStaffMembershipFactory,
    GuildFactory,
    GuildOrientationSettingsFactory,
    GuildStaffMembershipFactory,
    MemberFactory,
    MembershipPlanFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
    OrientationTypeFactory,
)
from tests.tours.conftest import signed_in

pytestmark = pytest.mark.django_db

HOME = reverse("hub_home")
TOKEN_PREFIX = reverse("hub_orientation_action", args=["TOKEN"]).split("TOKEN")[0]
NO_ACT = escape("This is here so you can see it; you don't need to act.")


def _person(username: str, name: str) -> Member:
    """An active member with a login, an email and a name no changelog could hold."""
    MembershipPlanFactory()
    member = User.objects.create_user(username=username, email=f"{username}@example.com").member
    member.full_legal_name = name
    member.status = Member.Status.ACTIVE
    member.save(update_fields=["full_legal_name", "status"])
    return member


def _staff(guild: Guild, member: Member, role: str) -> GuildStaffMembership:
    return GuildStaffMembershipFactory(guild=guild, member=member, role=role)


class Ceramics:
    """A guild with a lead, a Co-Lead, a Treasurer and two Orientors, and a member asking."""

    def __init__(self) -> None:
        self.lead = _person("cx_lead", "Lena Quartzlead")
        self.guild = GuildFactory(name="Zircon Ceramics", guild_lead=self.lead)
        GuildOrientationSettingsFactory(guild=self.guild, is_enabled=True)
        self.co_lead = _person("cx_colead", "Cole Quartzco")
        self.treasurer = _person("cx_treas", "Tess Quartztreas")
        self.olive = _person("cx_olive", "Olive Quartzorient")
        self.otto = _person("cx_otto", "Otto Quartzorient")
        _staff(self.guild, self.co_lead, GuildStaffMembership.Role.CO_LEAD)
        _staff(self.guild, self.treasurer, GuildStaffMembership.Role.TREASURER)
        _staff(self.guild, self.olive, GuildStaffMembership.Role.ORIENTER)
        _staff(self.guild, self.otto, GuildStaffMembership.Role.ORIENTER)
        self.asker = _person("cx_rita", "Rita Quartzasker")

    def request(self, *, asker: Member | None = None, days: int = 2, **slot: Any) -> OrientationBooking:
        start = timezone.now() + timedelta(days=days)
        made = OrientationSlotFactory(
            guild=self.guild, starts_at=start, ends_at=start + timedelta(hours=1), enabled_settings=False, **slot
        )
        return orientations.request_orientation(made, asker or self.asker)


@pytest.fixture
def cx() -> Ceramics:
    return Ceramics()


class Cnc:
    """An item owned by a guild (lead and Treasurer) with two managers, and a member asking."""

    def __init__(self) -> None:
        self.lead = _person("cnc_lead", "Mo Basaltlead")
        self.guild = GuildFactory(name="Basalt Metal", guild_lead=self.lead)
        self.co_lead = _person("cnc_colead", "Cass Basaltco")
        self.treasurer = _person("cnc_treas", "Tia Basalttreas")
        _staff(self.guild, self.co_lead, GuildStaffMembership.Role.CO_LEAD)
        _staff(self.guild, self.treasurer, GuildStaffMembership.Role.TREASURER)
        self.item: Equipment = EquipmentFactory(name="Basalt Router", guild=self.guild)
        self.max = _person("cnc_max", "Max Basaltmanager")
        self.mia = _person("cnc_mia", "Mia Basaltmanager")
        EquipmentStaffMembershipFactory(equipment=self.item, member=self.max)
        EquipmentStaffMembershipFactory(equipment=self.item, member=self.mia)
        self.kind = OrientationTypeFactory(guild=None, equipment=self.item, name="Basalt Router Orientation")
        self.asker = _person("cnc_sam", "Sam Basaltasker")

    def request(self, *, orienter: Member | None = None) -> OrientationBooking:
        start = timezone.now() + timedelta(days=3)
        made = OrientationSlotFactory(
            guild=None,
            orientation_type=self.kind,
            orienter=orienter,
            starts_at=start,
            ends_at=start + timedelta(hours=1),
        )
        return orientations.request_orientation(made, self.asker)


@pytest.fixture
def cnc() -> Cnc:
    return Cnc()


def _home(member: Member) -> str:
    return signed_in(member).get(HOME).content.decode()


def _needs_attention(html: str) -> list[str]:
    if "data-needs-attention-orientations" not in html:
        return []
    group = html.split("data-needs-attention-orientations")[1].split("</div>")[0]
    return re.findall(r'data-orientation-request="(\d+)"', group)


def _guild_activity(html: str) -> list[str]:
    if 'data-block="guild-activity"' not in html:
        return []
    card = html.split('data-block="guild-activity"')[1].split("</section>")[0]
    return re.findall(r'data-guild-activity="(\d+)"', card)


def _row(html: str, booking: OrientationBooking) -> str:
    return html.split(f'data-guild-activity="{booking.pk}"')[1].split("</li>")[0]


def describe_home():
    def describe_needs_attention():
        def it_shows_an_orientor_the_request_sent_to_them_with_a_respond_button(cx: Ceramics):
            booking = cx.request()
            html = _home(cx.olive)

            assert _needs_attention(html) == [str(booking.pk)]
            group = html.split("data-needs-attention-orientations")[1].split("</div>")[0]
            when = timezone.localtime(booking.slot.starts_at)
            assert "Orientation Requests Waiting on You" in group
            assert "Rita Quartzasker · Orientation" in group
            assert f"{when:%a, %b} {when.day} · {when:%-I:%M %p}" in group
            assert "Requested today" in group
            respond = reverse("hub_orientation_respond", args=[booking.pk])
            assert f'href="{respond}" class="pl-btn pl-btn--primary pl-btn--sm" data-orientation-respond' in group
            assert _guild_activity(html) == []

        def it_lists_them_soonest_first(cx: Ceramics):
            later = cx.request(days=5)
            sooner = cx.request(asker=_person("cx_sooner", "Sid Quartzsooner"), days=1)

            assert _needs_attention(_home(cx.otto)) == [str(sooner.pk), str(later.pk)]

        def it_targets_the_discord_dismiss_at_its_own_group_beside_a_request(cx: Ceramics):
            from membership.models import DiscordReconnectNudge

            cx.request()
            DiscordReconnectNudge.objects.create(member=cx.olive)
            html = _home(cx.olive)

            dismiss = html.split("data-discord-reconnect-dismiss")[1].split(">")[0]
            assert 'hx-target="closest .pl-home-group"' in dismiss

    def describe_guild_activity():
        def it_shows_the_lead_a_co_lead_and_a_treasurer_the_request_with_who_was_notified(cx: Ceramics):
            booking = cx.request()
            for member in (cx.lead, cx.co_lead, cx.treasurer):
                html = _home(member)
                assert _guild_activity(html) == [str(booking.pk)], member
                assert _needs_attention(html) == [], member
                row = _row(html, booking)
                assert "Rita Quartzasker · Orientation" in row
                assert "requested today" in row
                assert f"Olive Quartzorient and Otto Quartzorient were notified and should respond. {NO_ACT}" in row
                assert f'href="{reverse("hub_orientation_respond", args=[booking.pk])}"' in row

        def it_places_the_card_right_below_needs_attention(cx: Ceramics):
            theirs = cx.request()
            mine = cx.request(asker=_person("cx_mine", "Mel Quartzmine"), orienter=cx.treasurer)
            html = _home(cx.treasurer)

            assert _needs_attention(html) == [str(mine.pk)]
            assert _guild_activity(html) == [str(theirs.pk)]
            after = html.split('data-block="needs-attention"')[1]
            assert after.index('data-block="guild-activity"') < after.index('data-block="upcoming"')

        def it_names_one_notified_person_in_the_singular(cx: Ceramics):
            booking = cx.request(orienter=cx.olive)
            html = _home(cx.otto)

            assert f"Olive Quartzorient was notified and should respond. {NO_ACT}" in _row(html, booking)

        def it_says_so_when_nobody_could_be_notified(cx: Ceramics):
            booking = cx.request()
            EventDelivery.objects.filter(period=f"booking:{booking.pk}:request").delete()

            assert f"Nobody could be notified yet. {NO_ACT}" in _row(_home(cx.lead), booking)

        def it_says_how_long_ago_it_was_requested(cx: Ceramics):
            booking = cx.request(days=6)
            for days, label in ((1, "requested yesterday"), (3, "requested 3 days ago")):
                OrientationBooking.objects.filter(pk=booking.pk).update(
                    requested_at=timezone.now() - timedelta(days=days)
                )
                assert label in _row(_home(cx.lead), booking)

        def it_is_absent_for_a_member_on_no_staff(cx: Ceramics):
            cx.request()
            html = _home(_person("cx_out", "Uma Outsider"))

            assert 'data-block="guild-activity"' not in html
            assert 'data-block="needs-attention"' not in html

        def it_is_absent_for_staff_when_nothing_waits(cx: Ceramics):
            assert 'data-block="guild-activity"' not in _home(cx.lead)

        def it_leaves_out_the_viewers_own_request(cx: Ceramics):
            cx.request(asker=cx.treasurer)

            assert _guild_activity(_home(cx.treasurer)) == []

    def describe_equipment():
        def it_shows_a_personal_slot_to_who_it_was_sent_to_and_the_rest_of_the_staff_in_guild_activity(cnc: Cnc):
            booking = cnc.request(orienter=cnc.max)
            for member in (cnc.max, cnc.lead):
                html = _home(member)
                assert _needs_attention(html) == [str(booking.pk)], member
                assert _guild_activity(html) == [], member
            for member in (cnc.mia, cnc.co_lead, cnc.treasurer):
                html = _home(member)
                assert _guild_activity(html) == [str(booking.pk)], member
                assert _needs_attention(html) == [], member
            row = _row(_home(cnc.mia), booking)
            assert "Sam Basaltasker · Basalt Router Orientation" in row
            assert f"Max Basaltmanager and Mo Basaltlead were notified and should respond. {NO_ACT}" in row

        def it_shows_a_shared_slot_to_every_manager_and_guild_staffer_it_was_sent_to(cnc: Cnc):
            booking = cnc.request()
            for member in (cnc.max, cnc.mia, cnc.lead, cnc.treasurer):
                assert _needs_attention(_home(member)) == [str(booking.pk)], member

    def describe_what_stops_waiting():
        @pytest.mark.parametrize(
            "status", [OrientationBooking.Status.CONFIRMED, OrientationBooking.Status.DECLINED, "cancelled"]
        )
        def it_drops_an_answered_request_from_both_places(cx: Ceramics, status: str):
            booking = cx.request()
            OrientationBooking.objects.filter(pk=booking.pk).update(status=status)

            assert _needs_attention(_home(cx.olive)) == []
            assert _guild_activity(_home(cx.lead)) == []

        def it_drops_a_request_whose_time_has_passed(cx: Ceramics):
            booking = cx.request()
            OrientationSlot.objects.filter(pk=booking.slot_id).update(starts_at=timezone.now() - timedelta(minutes=5))

            assert _needs_attention(_home(cx.olive)) == []
            assert _guild_activity(_home(cx.lead)) == []

    def describe_side_effects():
        def it_sends_nothing_when_home_is_viewed(cx: Ceramics):
            cx.request()
            mail.outbox = []
            deliveries = EventDelivery.objects.count()
            bells = Notification.objects.count()
            with patch("core.events.emit.emit") as emitted, patch("membership.orientations.emit") as emitted_here:
                for member in (cx.olive, cx.lead, cx.treasurer):
                    _home(member)

            assert mail.outbox == []
            assert emitted.call_count == 0
            assert emitted_here.call_count == 0
            assert (EventDelivery.objects.count(), Notification.objects.count()) == (deliveries, bells)

        def it_reads_a_fixed_number_of_queries_whatever_the_number_of_guilds(cx: Ceramics):
            cx.request()

            def count() -> int:
                client = signed_in(cx.treasurer)
                with CaptureQueriesContext(connection) as ctx:
                    client.get(HOME)
                return len(ctx.captured_queries)

            count()  # the first page load warms per-process caches
            one = count()
            for n in range(3):
                guild = GuildFactory(name=f"Zircon Extra {n}", guild_lead=_person(f"cx_xl{n}", f"Xl Quartz{n}"))
                _staff(guild, cx.treasurer, GuildStaffMembership.Role.TREASURER)
                _staff(guild, _person(f"cx_xo{n}", f"Xo Quartz{n}"), GuildStaffMembership.Role.ORIENTER)
                start = timezone.now() + timedelta(days=2)
                slot = OrientationSlotFactory(guild=guild, starts_at=start, ends_at=start + timedelta(hours=1))
                orientations.request_orientation(slot, _person(f"cx_xr{n}", f"Xr Quartz{n}"))

            assert len(_guild_activity(_home(cx.treasurer))) == 4
            assert count() == one


def describe_the_morning_digest_line():
    def it_reaches_the_lead_and_co_lead_and_nobody_else(cx: Ceramics):
        booking = cx.request()

        for member in (cx.lead, cx.co_lead):
            (digest,) = digest_emails(member.primary_email)
            assert "Orientation request waiting: Rita Quartzasker, Orientation" in digest.body
            assert "Olive Quartzorient and Otto Quartzorient were asked to respond." in digest.body
            assert reverse("hub_orientation_respond", args=[booking.pk]) in digest.body
        for member in (cx.treasurer, cx.olive):
            assert digest_emails(member.primary_email) == []

        assert not Notification.objects.filter(trigger="orientation.waiting_to_lead").exists()

    def it_names_who_was_asked_in_the_singular_and_when_nobody_was(cx: Ceramics):
        cx.request(orienter=cx.olive)
        (digest,) = digest_emails(cx.lead.primary_email)
        assert "Olive Quartzorient was asked to respond." in digest.body

        quiet = OrientationBookingFactory(
            slot=OrientationSlotFactory(guild=cx.guild), member=_person("cx_q", "Quin Quartzq")
        )
        orientations._queue_lead_digest_line(quiet, [])
        line = EventDelivery.objects.get(
            event_key="orientation.waiting_to_lead",
            target_ref=f"user:{cx.lead.user_id}",
            channel="digest",
            period=f"booking:{quiet.pk}:waiting",
        )
        assert line.body.endswith("Nobody could be asked to respond.")

    @pytest.mark.parametrize("answer", ["confirm", "decline"])
    def it_drops_a_request_answered_before_8_am(cx: Ceramics, answer: str):
        booking = cx.request()
        if answer == "confirm":
            orientations.confirm_orientation(booking, oriented_by=cx.olive)
        else:
            orientations.decline_orientation(booking, actor=cx.olive.user)

        assert digest_emails(cx.lead.primary_email) == []

    def it_drops_a_request_whose_time_passed_before_8_am(cx: Ceramics):
        booking = cx.request()
        OrientationSlot.objects.filter(pk=booking.slot_id).update(starts_at=timezone.now() + timedelta(minutes=5))

        with patch("django.utils.timezone.now", return_value=next_8am()):
            assert digest_emails(cx.lead.primary_email) == []

    def it_reaches_the_owning_guilds_lead_and_co_lead_for_an_equipment_request(cnc: Cnc):
        cnc.request()

        for member in (cnc.lead, cnc.co_lead):
            (digest,) = digest_emails(member.primary_email)
            assert "Orientation request waiting: Sam Basaltasker, Basalt Router Orientation" in digest.body
        assert digest_emails(cnc.treasurer.primary_email) == []

    def it_queues_nothing_for_equipment_no_guild_owns(cnc: Cnc):
        cnc.item.guild = None
        cnc.item.save(update_fields=["guild"])
        cnc.request()

        assert not EventDelivery.objects.filter(event_key="orientation.waiting_to_lead").exists()


def _request_emails(member: Member) -> list[Any]:
    return [
        m for m in mail.outbox if m.to == [member.primary_email] and m.subject.startswith("New orientation request")
    ]


def _tokens(message: Any) -> list[str]:
    return re.findall(re.escape(TOKEN_PREFIX) + r"([^/\s\"]+)/", message.body)


def describe_a_new_orientor_inherits_the_waiting_requests():
    def it_sends_a_new_orientor_the_shared_request_with_links_that_credit_them(
        cx: Ceramics, django_capture_on_commit_callbacks: Any
    ):
        booking = cx.request()
        mail.outbox = []
        newcomer = _person("cx_new", "Nia Quartznew")

        with django_capture_on_commit_callbacks(execute=True):
            cx.guild.add_staff(newcomer, role=GuildStaffMembership.Role.ORIENTER)

        (email,) = _request_emails(newcomer)
        tokens = _tokens(email)
        assert tokens
        for token in tokens:
            found, _action, recipient = orientations.read_action_token(token)
            assert (found.pk, recipient) == (booking.pk, newcomer)
            assert orientations.read_action_addressed(token)
        assert Notification.objects.filter(user=newcomer.user, trigger="orientation_requested").count() == 1
        assert [m for m in mail.outbox if m.to != [newcomer.primary_email]] == []

    def it_does_not_send_a_personal_slot_booked_with_another_orientor(
        cx: Ceramics, django_capture_on_commit_callbacks: Any
    ):
        cx.request(orienter=cx.olive)
        newcomer = _person("cx_new2", "Nia Quartznew")

        with django_capture_on_commit_callbacks(execute=True):
            cx.guild.add_staff(newcomer, role=GuildStaffMembership.Role.ORIENTER)

        assert _request_emails(newcomer) == []

    def it_sends_nothing_to_a_new_treasurer(cx: Ceramics, django_capture_on_commit_callbacks: Any):
        cx.request()
        newcomer = _person("cx_new3", "Nia Quartznew")

        with patch("core.tasks.enqueue_on_commit") as enqueued:
            cx.guild.add_staff(newcomer, role=GuildStaffMembership.Role.TREASURER)

        assert _request_emails(newcomer) == []
        assert orientations.send_waiting_requests not in [call.args[0] for call in enqueued.call_args_list]

    def it_sends_the_first_orientor_what_waited_on_the_lead(django_capture_on_commit_callbacks: Any):
        lead = _person("cx_only_lead", "Lou Quartzonly")
        guild = GuildFactory(name="Zircon Lonely", guild_lead=lead)
        start = timezone.now() + timedelta(days=2)
        slot = OrientationSlotFactory(guild=guild, starts_at=start, ends_at=start + timedelta(hours=1))
        orientations.request_orientation(slot, _person("cx_only_ask", "Ada Quartzask"))
        newcomer = _person("cx_only_new", "Nia Quartznew")

        with django_capture_on_commit_callbacks(execute=True):
            guild.add_staff(newcomer, role=GuildStaffMembership.Role.ORIENTER)

        assert len(_request_emails(newcomer)) == 1

    def it_sends_a_staffer_retitled_to_orientor_once_however_often_they_are_retitled(
        cx: Ceramics, django_capture_on_commit_callbacks: Any
    ):
        cx.request()
        row = GuildStaffMembership.objects.get(guild=cx.guild, member=cx.treasurer)
        mail.outbox = []

        with django_capture_on_commit_callbacks(execute=True):
            row.retitle(GuildStaffMembership.Role.ORIENTER, "")
        with django_capture_on_commit_callbacks(execute=True):
            row.retitle(GuildStaffMembership.Role.TREASURER, "")
        with django_capture_on_commit_callbacks(execute=True):
            row.retitle(GuildStaffMembership.Role.ORIENTER, "")
        orientations.send_waiting_requests.call(cx.treasurer.pk, guild_id=cx.guild.pk)

        assert len(_request_emails(cx.treasurer)) == 1
        assert Notification.objects.filter(user=cx.treasurer.user, trigger="orientation_requested").count() == 1

    def it_enqueues_nothing_when_an_orientor_is_renamed_orientor(cx: Ceramics):
        row = GuildStaffMembership.objects.get(guild=cx.guild, member=cx.olive)
        with patch("core.tasks.enqueue_on_commit") as enqueued:
            row.retitle(GuildStaffMembership.Role.ORIENTER, "")

        assert orientations.send_waiting_requests not in [call.args[0] for call in enqueued.call_args_list]

    def it_skips_a_member_with_no_login(cx: Ceramics):
        cx.request()
        mail.outbox = []
        unlinked = MemberFactory(full_legal_name="Nolan Quartznologin")

        orientations.send_waiting_requests.call(unlinked.pk, guild_id=cx.guild.pk)

        assert mail.outbox == []


def describe_a_new_item_manager_inherits_the_waiting_requests():
    def it_sends_a_new_manager_the_shared_request(cnc: Cnc, django_capture_on_commit_callbacks: Any):
        booking = cnc.request()
        mail.outbox = []
        newcomer = _person("cnc_new", "Nico Basaltnew")

        with django_capture_on_commit_callbacks(execute=True):
            EquipmentStaffMembershipFactory(equipment=cnc.item, member=newcomer).inherit_waiting_requests()

        (email,) = _request_emails(newcomer)
        found, _action, recipient = orientations.read_action_token(_tokens(email)[0])
        assert (found.pk, recipient) == (booking.pk, None)
        assert [m for m in mail.outbox if m.to != [newcomer.primary_email]] == []

    def it_does_not_send_a_personal_slot_booked_with_another_manager(cnc: Cnc, django_capture_on_commit_callbacks: Any):
        cnc.request(orienter=cnc.max)
        newcomer = _person("cnc_new2", "Nico Basaltnew")

        with django_capture_on_commit_callbacks(execute=True):
            EquipmentStaffMembershipFactory(equipment=cnc.item, member=newcomer).inherit_waiting_requests()

        assert _request_emails(newcomer) == []

    def it_sends_from_the_manage_pages_staff_tab(cnc: Cnc, django_capture_on_commit_callbacks: Any):
        cnc.request()
        newcomer = _person("cnc_new3", "Nico Basaltnew")
        client = signed_in(cnc.lead)

        with django_capture_on_commit_callbacks(execute=True):
            response = client.post(reverse("hub_equipment_staff_add", args=[cnc.item.slug]), {"member": newcomer.pk})

        assert response.status_code == 302
        assert len(_request_emails(newcomer)) == 1
