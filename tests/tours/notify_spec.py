"""The facility tour emails (#948): the calendar invite, the reminder, and who hears about a booking."""

from __future__ import annotations

from datetime import date, timedelta
from io import StringIO
from typing import Any
from unittest.mock import patch

import pytest
from django.core import mail
from django.core.management import call_command

from core.events.resolvers import tour_guides
from core.events.scheduler import run_due
from core.models import SiteConfiguration
from membership.models import AdminCapability, Member
from tests.membership.factories import MemberFactory
from tests.tours.conftest import signed_in
from tests.tours.factories import TourBookingFactory, local
from tours import signals
from tours.models import TourBooking
from tours.notify import (
    announce,
    build_ics,
    guide_first_name,
    support_email,
    tell_guide_booked,
    tour_reminder_occurrences,
)

pytestmark = pytest.mark.django_db

TUESDAY = date(2030, 1, 8)
FOUR = local(TUESDAY, 16)


def _visitor_mail(address: str = "sam@example.com") -> list[Any]:
    return [message for message in mail.outbox if message.to == [address]]


@pytest.fixture
def booking(guide: Member) -> TourBooking:
    return TourBookingFactory(guide=guide, starts_at=FOUR, party_size=1, visitor_email="sam@example.com")


def describe_build_ics():
    def it_keeps_the_uid_and_carries_the_sequence(booking: TourBooking):
        booking.sequence = 3
        invite = build_ics(booking).decode()
        assert f"UID:tour-{booking.pk}@pastlives" in invite
        assert "SEQUENCE:3" in invite
        assert "METHOD:REQUEST" in invite and "STATUS:CONFIRMED" in invite
        assert "DTSTART:20300109T000000Z" in invite
        assert "2808 SE 9th Ave" in invite
        assert "for 1 person." in invite


def describe_reminders():
    def it_reminds_once_24_hours_before(booking: TourBooking):
        tick = FOUR - timedelta(hours=24)
        assert run_due(tour_reminder_occurrences(tick), now=tick) == 1
        assert run_due(tour_reminder_occurrences(tick), now=tick) == 0
        reminders = _visitor_mail()
        assert [message.subject for message in reminders] == ["Reminder: your Past Lives tour is Tue, Jan 8 at 4:00 PM"]
        assert "your tour of Past Lives Makerspace is tomorrow!" in reminders[0].body

    def it_waits_until_the_tick_24_hours_before(booking: TourBooking):
        early = FOUR - timedelta(hours=25)
        assert run_due(tour_reminder_occurrences(early), now=early) == 0
        assert not mail.outbox

    def it_never_reminds_about_a_cancelled_tour(booking: TourBooking):
        booking.status = TourBooking.Status.CANCELLED
        booking.save()
        tick = FOUR - timedelta(hours=24)
        assert list(tour_reminder_occurrences(tick)) == []

    def it_reminds_again_for_a_moved_tours_new_time(booking: TourBooking):
        tick = FOUR - timedelta(hours=24)
        run_due(tour_reminder_occurrences(tick), now=tick)
        booking.starts_at = FOUR + timedelta(days=1)
        booking.ends_at = booking.starts_at + timedelta(hours=1)
        booking.save()
        later = tick + timedelta(days=1)
        assert run_due(tour_reminder_occurrences(later), now=later) == 1

    def it_runs_from_the_scheduled_job():
        out = StringIO()
        call_command("send_tour_reminders", stdout=out)
        assert "No tour reminder due this tick." in out.getvalue()
        with patch("tours.management.commands.send_tour_reminders.run_sources", return_value=2):
            call_command("send_tour_reminders", stdout=out)
        assert "Sent 2 tour reminder(s)." in out.getvalue()


def describe_who_hears():
    def it_tells_the_tours_guide(guide: Member):
        assert [user for user, _reason in tour_guides({"guide": guide})] == ([guide.user] if guide.user else [])

    def it_tells_the_admins_not_the_guides_when_the_tour_has_no_guide(guide: Member):
        signed_in(guide)
        guide.refresh_from_db()
        admin = MemberFactory(fog_role=Member.FogRole.ADMIN)
        signed_in(admin)
        admin.refresh_from_db()
        assert [user for user, _reason in tour_guides({"guide": None})] == [admin.user]

    def it_emails_the_admins_about_an_unassigned_booking_and_its_changes(guide: Member):
        signed_in(guide)
        guide.refresh_from_db()
        admin = MemberFactory(fog_role=Member.FogRole.ADMIN)
        signed_in(admin)
        admin.refresh_from_db()
        unassigned = TourBookingFactory(guide=None, starts_at=FOUR)
        tell_guide_booked(unassigned, None)
        announce(unassigned, signals.CANCELLED)
        assert [message.to for message in mail.outbox if message.to != [unassigned.visitor_email]] == [
            [admin.user.email],
            [admin.user.email],
        ]

    def it_tells_a_guide_once_about_a_move_within_their_tours(guide: Member, booking: TourBooking):
        signed_in(guide)
        guide.refresh_from_db()
        announce(booking, signals.MOVED, previous_guide=guide, previous_starts_at=FOUR - timedelta(hours=1))
        assert len([message for message in mail.outbox if message.to == [guide.user.email]]) == 1


def describe_small_helpers():
    def it_reads_the_guides_first_name():
        assert guide_first_name(None) == ""
        assert guide_first_name(MemberFactory(full_legal_name="Alex Kim", preferred_name="")) == "Alex"

    def it_falls_back_to_the_house_address_with_no_support_email():
        config = SiteConfiguration.load()
        config.org_support_email = ""
        config.save()
        assert support_email() == "info@pastlives.space"
        config.org_support_email = "tours@example.com"
        config.save()
        assert support_email() == "tours@example.com"


def describe_the_guides_notice_preference():
    def it_offers_tour_guides_and_admins_the_rows(guide: Member):
        from core.events.settings_matrix import _eligible_for, _staff_profile
        from core.events.registry import Recipients

        signed_in(guide)
        guide.refresh_from_db()
        plain = MemberFactory()
        signed_in(plain)
        plain.refresh_from_db()
        admin = MemberFactory(fog_role=Member.FogRole.ADMIN)
        signed_in(admin)
        admin.refresh_from_db()
        assert _eligible_for(Recipients.TOUR_GUIDES, _staff_profile(guide.user))
        assert _eligible_for(Recipients.TOUR_GUIDES, _staff_profile(admin.user))
        assert not _eligible_for(Recipients.TOUR_GUIDES, _staff_profile(plain.user))
        assert AdminCapability.Capability.TOUR_GUIDE.label == "Tour Guide"
