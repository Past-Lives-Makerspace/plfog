"""The Tour Follow Up email (#1345): Send Follow Up on the Past tab, its record, Tour Settings and the automatic send."""

from __future__ import annotations

import json
from datetime import timedelta
from io import StringIO
from typing import Any
from unittest.mock import patch

import pytest
from django.contrib.messages import get_messages
from django.core import mail
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from core.models import TransactionalEmailLog
from core.scheduled_jobs import SCHEDULED_JOBS
from membership.models import Member
from tests.tours.conftest import make_guide, signed_in
from tests.tours.factories import TourBookingFactory
from tours.forms import FacilityTourSettingsForm
from tours.models import DEFAULT_FOLLOW_UP_ALSO_TO, FacilityTourSettings, TourBooking
from tours.notify import (
    NEWSLETTER_URL,
    FollowUpNotSentError,
    follow_up_recipients,
    follow_up_signer,
    send_due_follow_ups,
    send_follow_up,
)

pytestmark = pytest.mark.django_db

PAST = reverse("tours:past")
HTMX = {"HTTP_HX_REQUEST": "true"}


def _url(booking: TourBooking) -> str:
    return reverse("tours:booking_follow_up", args=[booking.pk])


def _past(guide: Member | None, hours_ago: float = 24, **kwargs: Any) -> TourBooking:
    """A tour that ended ``hours_ago`` hours ago (an hour long)."""
    ends = timezone.now() - timedelta(hours=hours_ago)
    kwargs.setdefault("visitor_name", "Sam Rivera")
    kwargs.setdefault("visitor_email", "sam@example.com")
    return TourBookingFactory(guide=guide, starts_at=ends - timedelta(hours=1), ends_at=ends, **kwargs)


def _toast(response: Any) -> dict[str, str]:
    return json.loads(response["HX-Trigger"])["showToast"]


def _auto(hours: int, since: Any) -> None:
    """Tour Settings sends the follow up ``hours`` after each tour, automatic sending on since ``since``."""
    tour_settings = FacilityTourSettings.load()
    tour_settings.follow_up_after_hours = hours
    tour_settings.save()
    FacilityTourSettings.objects.filter(pk=1).update(follow_up_auto_since=since)


@pytest.fixture
def dayzie(db) -> Member:
    return make_guide(full_legal_name="Dayzie Day", preferred_name="Dayzie")


def describe_the_past_tab_menu():
    def it_offers_send_follow_up_to_the_tours_guide(dayzie: Member):
        booking = _past(dayzie)
        content = signed_in(dayzie).get(PAST).content.decode()
        assert f'data-tour-follow-up="{booking.pk}"' in content

    def it_offers_it_to_an_admin_on_any_tour(admin: Member, dayzie: Member):
        booking = _past(dayzie, status=TourBooking.Status.ATTENDED)
        assert f'data-tour-follow-up="{booking.pk}"' in signed_in(admin).get(PAST).content.decode()

    def it_hides_it_from_another_guide(dayzie: Member):
        booking = _past(dayzie)
        other = make_guide(full_legal_name="Otto Other")
        assert f'data-tour-follow-up="{booking.pk}"' not in signed_in(other).get(PAST).content.decode()

    def it_hides_it_on_a_no_show(dayzie: Member):
        booking = _past(dayzie, status=TourBooking.Status.NO_SHOW)
        content = signed_in(dayzie).get(PAST).content.decode()
        assert f'data-tour-follow-up="{booking.pk}"' not in content
        assert f"tour-follow-up-{booking.pk}" not in content

    def it_offers_send_again_behind_a_confirm_once_sent(dayzie: Member):
        booking = _past(dayzie, follow_up_sent_at=timezone.now(), follow_up_sent_by=dayzie)
        content = signed_in(dayzie).get(PAST).content.decode()
        assert f'data-tour-follow-up-again="{booking.pk}"' in content
        assert f'data-tour-follow-up="{booking.pk}"' not in content
        assert f"$dispatch('open-confirm', 'tour-follow-up-{booking.pk}')" in content
        assert "Send the Follow Up Again?" in content

    def it_renders_the_confirm_before_the_first_send_so_the_swapped_row_can_open_it(dayzie: Member):
        booking = _past(dayzie)
        content = signed_in(dayzie).get(PAST).content.decode()
        assert f'data-tour-follow-up="{booking.pk}"' in content
        assert "Send the Follow Up Again?" in content


def describe_send_follow_up_view():
    def it_emails_the_guest_and_also_send_to_on_one_to_line(dayzie: Member):
        booking = _past(dayzie, visitor_name="Sam Rivera")
        response = signed_in(dayzie).post(_url(booking), **HTMX)
        assert response.status_code == 200
        assert len(mail.outbox) == 1
        sent = mail.outbox[0]
        assert sent.to == ["sam@example.com", DEFAULT_FOLLOW_UP_ALSO_TO]
        assert sent.subject == "Tour Follow Up - Sam Rivera"

    def it_greets_the_guest_by_first_name_and_signs_as_the_guide(dayzie: Member):
        booking = _past(dayzie, visitor_name="Sam Rivera")
        signed_in(dayzie).post(_url(booking), **HTMX)
        dayzie.refresh_from_db()
        sent = mail.outbox[0]
        html = sent.alternatives[0][0]
        for body in (sent.body, html):
            assert "Hi Sam," in body
            assert "It was a sincere pleasure having you by the shop." in body
            assert "my contact info is below." in body
            assert NEWSLETTER_URL in body
            assert "Dayzie" in body
            assert dayzie.primary_email in body
            assert "Morlock" not in body
            assert "741-5459" not in body
            assert "Founder" not in body
        assert f'href="mailto:{dayzie.primary_email}"' in html

    def it_records_the_send_and_swaps_the_row_with_a_toast(dayzie: Member):
        booking = _past(dayzie, visitor_name="Sam Rivera")
        response = signed_in(dayzie).post(_url(booking), **HTMX)
        assert _toast(response) == {"message": "Follow up sent to Sam Rivera.", "type": "success"}
        booking.refresh_from_db()
        assert booking.follow_up_sent_at is not None
        assert (booking.follow_up_sent_by, booking.follow_up_automatic) == (dayzie, False)
        content = response.content.decode()
        assert content.strip().startswith("<tr") and "data-tour-follow-up-sent" in content
        day = f"{timezone.localtime(booking.follow_up_sent_at):%b} {timezone.localtime(booking.follow_up_sent_at).day}"
        assert f"Follow up sent {day} by {dayzie.staff_label}" in content
        assert f'data-tour-follow-up-again="{booking.pk}"' in content

    def it_sends_again_on_the_confirmed_post(dayzie: Member):
        booking = _past(dayzie, follow_up_sent_at=timezone.now() - timedelta(days=1), follow_up_automatic=True)
        signed_in(dayzie).post(_url(booking), {"again": "1"}, **HTMX)
        assert len(mail.outbox) == 1
        booking.refresh_from_db()
        assert (booking.follow_up_sent_by, booking.follow_up_automatic) == (dayzie, False)

    def it_sends_nothing_from_a_stale_page_and_shows_the_row_as_it_is(dayzie: Member):
        sent_at = timezone.now() - timedelta(days=1)
        booking = _past(dayzie, follow_up_sent_at=sent_at, follow_up_automatic=True)
        response = signed_in(dayzie).post(_url(booking), **HTMX)
        assert mail.outbox == []
        day = f"{timezone.localtime(sent_at):%b} {timezone.localtime(sent_at).day}"
        assert _toast(response) == {"message": f"The follow up already went on {day}.", "type": "info"}
        content = response.content.decode()
        assert f"Follow up sent automatically {day}" in content
        assert f'data-tour-follow-up-again="{booking.pk}"' in content
        booking.refresh_from_db()
        assert (booking.follow_up_sent_at, booking.follow_up_automatic) == (sent_at, True)

    def it_sends_once_on_a_double_click(dayzie: Member):
        booking = _past(dayzie)
        client = signed_in(dayzie)
        client.post(_url(booking), **HTMX)
        client.post(_url(booking), **HTMX)
        assert len(mail.outbox) == 1

    def it_flashes_info_on_a_plain_stale_post(dayzie: Member):
        booking = _past(dayzie, follow_up_sent_at=timezone.now(), follow_up_sent_by=dayzie)
        response = signed_in(dayzie).post(_url(booking))
        levels = [m.level_tag for m in get_messages(response.wsgi_request)]
        assert levels == ["info"]

    def it_disables_the_button_while_sending_and_posts_again_from_the_confirm(dayzie: Member):
        _past(dayzie)
        sent = _past(dayzie, follow_up_sent_at=timezone.now(), follow_up_sent_by=dayzie)
        content = signed_in(dayzie).get(PAST).content.decode()
        assert 'hx-disabled-elt="find button"' in content
        assert """hx-vals='{"again": "1"}'""" in content
        assert f'hx-post="{_url(sent)}"' in content

    def it_signs_a_tour_with_no_guide_as_whoever_sends_it(admin: Member):
        booking = _past(None)
        signed_in(admin).post(_url(booking), **HTMX)
        admin.refresh_from_db()
        assert "Ada Admin" in mail.outbox[0].body
        assert admin.primary_email in mail.outbox[0].body

    def it_redirects_a_plain_post_to_the_past_tab_with_a_flash(dayzie: Member):
        booking = _past(dayzie, visitor_name="Pia Park")
        response = signed_in(dayzie).post(_url(booking))
        assert (response.status_code, response["Location"]) == (302, PAST)
        assert [str(m) for m in get_messages(response.wsgi_request)] == ["Follow up sent to Pia Park."]

    def it_refuses_another_guides_crafted_post(dayzie: Member):
        booking = _past(dayzie)
        other = make_guide(full_legal_name="Otto Other")
        assert signed_in(other).post(_url(booking), **HTMX).status_code == 403
        assert mail.outbox == []

    def it_refuses_a_no_show_and_a_tour_that_has_not_started(dayzie: Member):
        no_show = _past(dayzie, status=TourBooking.Status.NO_SHOW)
        upcoming = _past(dayzie, hours_ago=-48)
        client = signed_in(dayzie)
        assert client.post(_url(no_show), **HTMX).status_code == 400
        assert client.post(_url(upcoming), **HTMX).status_code == 400
        assert mail.outbox == []

    def it_records_nothing_and_says_so_when_the_send_fails(dayzie: Member):
        booking = _past(dayzie)
        with patch("core.email._deliver", side_effect=OSError("smtp down")):
            response = signed_in(dayzie).post(_url(booking), **HTMX)
        assert _toast(response) == {"message": "The follow up did not send. Try again in a moment.", "type": "error"}
        booking.refresh_from_db()
        assert (booking.follow_up_sent_at, booking.follow_up_sent_by, booking.follow_up_automatic) == (
            None,
            None,
            False,
        )
        assert TransactionalEmailLog.objects.get().status == TransactionalEmailLog.Status.FAILED

    def it_leaves_the_row_unsent_when_the_send_raises(dayzie: Member):
        booking = _past(dayzie)
        with patch("tours.notify.email_shell_message", side_effect=RuntimeError("broken template")):
            with pytest.raises(RuntimeError):
                send_follow_up(booking, sender=dayzie)
        booking.refresh_from_db()
        assert (booking.follow_up_sent_at, booking.follow_up_sent_by) == (None, None)

    def it_keeps_the_earlier_record_when_a_send_again_fails(dayzie: Member):
        sent_at = timezone.now() - timedelta(days=1)
        booking = _past(dayzie, follow_up_sent_at=sent_at, follow_up_automatic=True)
        with patch("core.email._deliver", side_effect=OSError("smtp down")):
            with pytest.raises(FollowUpNotSentError):
                send_follow_up(booking, sender=dayzie, again=True)
        booking.refresh_from_db()
        assert (booking.follow_up_sent_at, booking.follow_up_automatic) == (sent_at, True)


def describe_signer():
    def it_signs_an_automatic_send_with_no_guide_as_past_lives():
        assert follow_up_signer(_past(None), None) == ("Past Lives", "")

    def it_signs_with_the_name_alone_when_the_guide_has_no_email(dayzie: Member):
        dayzie._pre_signup_email = ""
        dayzie.save(update_fields=["_pre_signup_email"])
        booking = _past(dayzie)
        send_follow_up(booking, sender=dayzie)
        html = mail.outbox[0].alternatives[0][0]
        assert "Dayzie" in html and "mailto:" not in html


def describe_recipients():
    def it_sends_to_the_guest_alone_when_also_send_to_is_blank(dayzie: Member):
        FacilityTourSettings.objects.update_or_create(pk=1, defaults={"follow_up_also_to": ""})
        assert follow_up_recipients(_past(dayzie)) == ["sam@example.com"]

    def it_lists_an_address_once_when_the_guest_is_also_send_to(dayzie: Member):
        FacilityTourSettings.objects.update_or_create(pk=1, defaults={"follow_up_also_to": "SAM@example.com"})
        assert follow_up_recipients(_past(dayzie)) == ["sam@example.com"]


def describe_follow_up_label():
    def it_is_blank_until_sent(dayzie: Member):
        booking = _past(dayzie)
        assert (booking.follow_up_label, booking.follow_up_day) == ("", "")

    def it_says_automatically_for_the_schedulers_send(dayzie: Member):
        booking = _past(dayzie, follow_up_sent_at=timezone.now(), follow_up_automatic=True)
        assert booking.follow_up_label.startswith("Follow up sent automatically ")

    def it_names_no_one_when_the_sender_is_gone(dayzie: Member):
        booking = _past(dayzie, follow_up_sent_at=timezone.now())
        assert booking.follow_up_label.startswith("Follow up sent ")
        assert " by " not in booking.follow_up_label


def describe_tour_settings():
    def it_defaults_to_off_and_to_sushuma():
        settings_row = FacilityTourSettings.load()
        assert (settings_row.follow_up_after_hours, settings_row.follow_up_also_to) == (None, DEFAULT_FOLLOW_UP_ALSO_TO)
        assert settings_row.follow_up_delay is None

    def it_starts_the_automatic_clock_when_the_hours_are_set_and_stops_it_when_blanked():
        tour_settings = FacilityTourSettings.load()
        assert tour_settings.follow_up_auto_since is None
        tour_settings.follow_up_after_hours = 2
        tour_settings.save()
        started = FacilityTourSettings.load().follow_up_auto_since
        assert started is not None
        tour_settings.follow_up_after_hours = 4
        tour_settings.save()
        assert FacilityTourSettings.load().follow_up_auto_since == started
        tour_settings.follow_up_after_hours = None
        tour_settings.save()
        assert FacilityTourSettings.load().follow_up_auto_since is None

    def it_saves_the_hours_and_the_address():
        form = FacilityTourSettingsForm(
            data={
                "accepting_bookings": "on",
                "booking_horizon_days": "30",
                "min_notice_hours": "24",
                "cancel_cutoff_hours": "24",
                "default_tour_minutes": "60",
                "default_seats": "6",
                "follow_up_after_hours": "0",
                "follow_up_also_to": "front@pastlives.space",
            },
            instance=FacilityTourSettings.load(),
        )
        assert form.is_valid(), form.errors
        form.save()
        saved = FacilityTourSettings.load()
        assert (saved.follow_up_after_hours, saved.follow_up_also_to) == (0, "front@pastlives.space")
        assert saved.follow_up_delay == timedelta(0)

    def it_refuses_over_two_weeks():
        form = FacilityTourSettingsForm(
            data={"follow_up_after_hours": "337", "follow_up_also_to": ""}, instance=FacilityTourSettings.load()
        )
        assert not form.is_valid()
        assert form.errors["follow_up_after_hours"] == ["Use 0 to 336 hours."]

    def it_shows_the_follow_up_card_to_an_admin(admin: Member):
        content = signed_in(admin).get(reverse("tours:settings")).content.decode()
        assert 'data-tour-settings-follow-up data-autosave-key="follow_up"' in content
        assert 'name="follow_up_after_hours"' in content and 'name="follow_up_also_to"' in content


def describe_automatic_send():
    @pytest.fixture
    def two_hours() -> None:
        _auto(2, timezone.now() - timedelta(days=3))

    def it_sends_nothing_while_off(dayzie: Member):
        _past(dayzie, hours_ago=5)
        assert send_due_follow_ups(timezone.now()) == 0
        assert mail.outbox == []

    def it_sends_once_to_attended_and_not_marked_tours_owed(dayzie: Member, two_hours: None):
        now = timezone.now()
        owed = [
            _past(dayzie, hours_ago=2.5, status=TourBooking.Status.ATTENDED, visitor_email="a@example.com"),
            _past(dayzie, hours_ago=2.5, visitor_email="b@example.com"),
        ]
        assert send_due_follow_ups(now) == 2
        assert sorted(message.to[0] for message in mail.outbox) == ["a@example.com", "b@example.com"]
        for booking in owed:
            booking.refresh_from_db()
            assert (booking.follow_up_automatic, booking.follow_up_sent_by) == (True, None)
        assert send_due_follow_ups(now) == 0
        assert len(mail.outbox) == 2

    def it_skips_no_shows_cancels_sent_tours_and_tours_not_yet_owed(dayzie: Member, two_hours: None):
        _past(dayzie, hours_ago=3, status=TourBooking.Status.NO_SHOW)
        _past(dayzie, hours_ago=3, status=TourBooking.Status.CANCELLED)
        _past(dayzie, hours_ago=3, follow_up_sent_at=timezone.now(), follow_up_sent_by=dayzie)
        _past(dayzie, hours_ago=1.9)
        assert send_due_follow_ups(timezone.now()) == 0
        assert mail.outbox == []

    def it_never_sends_before_the_tour_ends_with_zero_hours(dayzie: Member):
        _auto(0, timezone.now() - timedelta(days=1))
        booking = _past(dayzie, hours_ago=-5 / 60)
        assert send_due_follow_ups(timezone.now()) == 0
        assert send_due_follow_ups(booking.ends_at) == 1

    def it_catches_up_a_tour_a_missed_tick_skipped(dayzie: Member, two_hours: None):
        booking = _past(dayzie, hours_ago=2.5)
        assert send_due_follow_ups(timezone.now() - timedelta(hours=1)) == 0
        assert send_due_follow_ups(timezone.now()) == 1
        booking.refresh_from_db()
        assert booking.follow_up_automatic is True

    def it_never_sends_a_tour_that_ended_before_it_was_turned_on(dayzie: Member):
        _past(dayzie, hours_ago=30)
        _auto(2, timezone.now() - timedelta(hours=24))
        assert send_due_follow_ups(timezone.now()) == 0
        assert mail.outbox == []

    def it_signs_as_past_lives_with_no_guide(dayzie: Member, two_hours: None):
        _past(None, hours_ago=2.5)
        send_due_follow_ups(timezone.now())
        assert "\nPast Lives\n" in mail.outbox[0].body

    def it_leaves_the_row_unsent_when_a_send_fails(dayzie: Member, two_hours: None):
        booking = _past(dayzie, hours_ago=2.5)
        with patch("core.email._deliver", side_effect=OSError("smtp down")):
            assert send_due_follow_ups(timezone.now()) == 0
        booking.refresh_from_db()
        assert (booking.follow_up_sent_at, booking.follow_up_automatic) == (None, False)
        assert send_due_follow_ups(timezone.now()) == 1

    def it_leaves_the_row_unsent_and_raises_on_an_unexpected_error(dayzie: Member, two_hours: None):
        booking = _past(dayzie, hours_ago=2.5)
        with patch("tours.notify.email_shell_message", side_effect=RuntimeError("broken template")):
            with pytest.raises(RuntimeError):
                send_due_follow_ups(timezone.now())
        booking.refresh_from_db()
        assert booking.follow_up_sent_at is None

    def it_skips_a_tour_claimed_first(dayzie: Member, two_hours: None):
        booking = _past(dayzie, hours_ago=2.5)
        with patch.object(TourBooking, "claim_follow_up", return_value=False):
            assert send_due_follow_ups(timezone.now()) == 0
        assert mail.outbox == []
        assert booking.claim_follow_up(timezone.now(), None) is True
        assert booking.claim_follow_up(timezone.now(), dayzie) is False

    def it_never_hands_back_a_later_send(dayzie: Member):
        booking = _past(dayzie, follow_up_sent_at=timezone.now(), follow_up_sent_by=dayzie)
        booking.release_follow_up(timezone.now() - timedelta(minutes=5))
        booking.refresh_from_db()
        assert booking.follow_up_sent_at is not None


def describe_send_tour_follow_ups_command():
    def it_reports_what_it_sent(dayzie: Member):
        _auto(2, timezone.now() - timedelta(days=1))
        _past(dayzie, hours_ago=2.5)
        out = StringIO()
        call_command("send_tour_follow_ups", stdout=out)
        assert "Sent 1 tour follow up(s)." in out.getvalue()

    def it_says_when_nothing_is_due():
        out = StringIO()
        call_command("send_tour_follow_ups", stdout=out)
        assert "No tour follow up due this tick." in out.getvalue()

    def it_is_a_registered_job():
        job = next(job for job in SCHEDULED_JOBS if job.key == "send_tour_follow_ups")
        assert job.command == "send_tour_follow_ups"


def describe_failed_send_error():
    def it_raises_when_staging_holds_every_recipient_back(dayzie: Member):
        held = TransactionalEmailLog(status=TransactionalEmailLog.Status.SUPPRESSED, error_message="")
        with patch("tours.notify.send_email", return_value=held):
            with pytest.raises(FollowUpNotSentError):
                send_follow_up(_past(dayzie), sender=dayzie)
