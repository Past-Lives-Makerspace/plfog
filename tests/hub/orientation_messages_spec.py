"""Specs for an orientation booking's messages on the hub (the shared thread, core.threads).

The member's booking page, the message endpoint for both sides and its refusals, the request
page's Messages card, the Messages fold on the member's card, the Bookings tab's Messages items,
and the messages that ride on a move, a proposal and a request's emails. Names and message text
are factory strings no changelog could contain (STANDARDS.md section 8).
"""

from __future__ import annotations

from datetime import timedelta
from io import BytesIO
from typing import Any

import pytest
from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from membership import orientations
from membership.models import Member, OrientationBooking
from tests.membership.factories import (
    GuildFactory,
    GuildOrientationSettingsFactory,
    MembershipPlanFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
    OrientationTypeFactory,
)

pytestmark = pytest.mark.django_db


def _user(username: str, name: str) -> Member:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass", email=f"{username}@example.com")
    member = user.member
    member.full_legal_name = name
    member.save(update_fields=["full_legal_name"])
    return member


def _world() -> tuple[Member, OrientationBooking]:
    """A guild lead and a requested booking on their guild by a member with an account."""
    lead = _user("thr_lead", "Lead Quillfeather")
    guild = GuildFactory(guild_lead=lead, name="Zzmossbank Guild")
    GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
    orientation_type = OrientationTypeFactory(guild=guild, name="Zzlathe Basics")
    booking = OrientationBookingFactory(
        slot=OrientationSlotFactory(guild=guild, orientation_type=orientation_type),
        member=_user("thr_member", "Orla Thistlewood"),
    )
    return lead, booking


def _photo() -> SimpleUploadedFile:
    buf = BytesIO()
    Image.new("RGB", (1, 1)).save(buf, format="PNG")
    return SimpleUploadedFile("shot.png", buf.getvalue(), content_type="image/png")


def _post(client: Client, booking: OrientationBooking, **data: Any) -> Any:
    return client.post(reverse("hub_orientation_message", args=[booking.pk]), {"body": "Zzhello there", **data})


def _toasts(response: Any) -> list[str]:
    return [str(message) for message in get_messages(response.wsgi_request)]


def describe_the_member_booking_page():
    def it_shows_the_booking_its_thread_and_a_box_to_write(client: Client):
        lead, booking = _world()
        booking.write_message(author=lead.user, body="Zzbring closed shoes")
        client.force_login(booking.member.user)

        response = client.get(reverse("hub_orientation_booking", args=[booking.pk]))

        content = response.content.decode()
        assert response.status_code == 200
        assert f'data-thread="booking-{booking.pk}"' in content
        assert "Zzbring closed shoes" in content
        assert reverse("hub_orientation_message", args=[booking.pk]) in content
        assert "data-thread-photos" in content
        assert "Write to the Orientors" in content

    def it_says_so_when_there_are_no_messages(client: Client):
        _lead, booking = _world()
        client.force_login(booking.member.user)
        content = client.get(reverse("hub_orientation_booking", args=[booking.pk])).content.decode()
        assert "data-thread-empty" in content

    def it_sends_someone_who_runs_the_booking_to_the_request_page(client: Client):
        lead, booking = _world()
        client.force_login(lead.user)
        response = client.get(reverse("hub_orientation_booking", args=[booking.pk]))
        assert response["Location"] == f"{reverse('hub_orientation_respond', args=[booking.pk])}#messages"

    def it_hides_the_booking_from_anyone_else(client: Client):
        _lead, booking = _world()
        client.force_login(_user("thr_stranger", "Zzstranger").user)
        assert client.get(reverse("hub_orientation_booking", args=[booking.pk])).status_code == 404


def describe_posting_a_message():
    def it_posts_the_members_message_tells_the_orientors_and_reopens_the_thread(client: Client):
        lead, booking = _world()
        client.force_login(booking.member.user)

        response = _post(client, booking, photos=[_photo()])

        [message] = booking.thread()
        assert (message.body, message.is_from_staff, len(message.thread_photos)) == ("Zzhello there", False, 1)
        assert response["Location"] == f"/orientation/bookings/{booking.pk}/?messages={booking.pk}"
        assert _toasts(response) == ["Sent. The Orientors for Zzmossbank Guild were notified."]
        assert [m.to for m in mail.outbox] == [[lead.user.email]]

    def it_lands_on_next_with_the_thread_named_in_its_query(client: Client):
        _lead, booking = _world()
        client.force_login(booking.member.user)
        response = _post(client, booking, next=f"/orientations/?view=list&messages=999#orientation-type-{booking.pk}")
        assert response["Location"] == (f"/orientations/?view=list&messages={booking.pk}#orientation-type-{booking.pk}")

    def it_ignores_a_next_off_the_site(client: Client):
        _lead, booking = _world()
        client.force_login(booking.member.user)
        response = _post(client, booking, next="https://evil.example/")
        assert response["Location"] == f"/orientation/bookings/{booking.pk}/?messages={booking.pk}"

    def it_posts_the_leaders_message_and_tells_the_member(client: Client):
        lead, booking = _world()
        client.force_login(lead.user)

        response = _post(client, booking)

        [message] = booking.thread()
        assert (message.is_from_staff, message.author_id) == (True, lead.user_id)
        assert response["Location"] == (f"/orientation/bookings/{booking.pk}/respond/?messages={booking.pk}#messages")
        assert _toasts(response) == ["Sent. Orla Thistlewood was notified."]
        assert [m.to for m in mail.outbox] == [[booking.member.user.email]]

    def it_refuses_anyone_else_with_a_404(client: Client):
        _lead, booking = _world()
        client.force_login(_user("thr_outsider", "Zzoutsider").user)
        assert _post(client, booking).status_code == 404
        assert booking.thread() == []

    def it_keeps_a_members_empty_message_on_their_page_with_the_error(client: Client):
        _lead, booking = _world()
        client.force_login(booking.member.user)

        response = _post(client, booking, body="")

        assert response.status_code == 200
        assert response.templates[0].name == "hub/orientation_booking.html"
        assert response.context["message_form"].errors["body"]
        assert booking.thread() == []

    def it_keeps_a_leaders_refused_message_on_the_request_page(client: Client):
        lead, booking = _world()
        client.force_login(lead.user)

        response = _post(client, booking, photos=[SimpleUploadedFile("note.txt", b"not an image")])

        assert response.status_code == 200
        assert response.templates[0].name == "hub/orientation_respond.html"
        assert "Zzhello there" in response.content.decode()
        assert booking.thread() == []

    def it_takes_only_a_post(client: Client):
        _lead, booking = _world()
        client.force_login(booking.member.user)
        assert client.get(reverse("hub_orientation_message", args=[booking.pk])).status_code == 405


def describe_the_request_page():
    def it_shows_the_messages_card_with_the_thread_and_a_box_naming_the_member(client: Client):
        lead, booking = _world()
        booking.write_message(author=booking.member.user, body="Zzcan we do six")
        client.force_login(lead.user)

        content = client.get(reverse("hub_orientation_respond", args=[booking.pk])).content.decode()

        assert 'id="messages"' in content
        assert "Zzcan we do six" in content
        assert "Send to Orla Thistlewood" in content
        assert "Note:" not in content


def describe_the_member_card():
    def it_folds_the_messages_under_the_live_booking_with_a_count(client: Client):
        lead, booking = _world()
        booking.write_message(author=lead.user, body="Zzsee you saturday")
        client.force_login(booking.member.user)

        content = client.get(reverse("hub_guild_detail", args=[booking.guild.slug])).content.decode()

        assert f'data-thread-open="{booking.pk}"' in content
        assert "Messages (1)" in content
        assert "Zzsee you saturday" in content
        assert f'id="id_booking_{booking.pk}_body"' in content


def describe_the_bookings_tab_menu():
    def it_offers_messages_on_the_leaders_row_and_the_members_own_row(client: Client):
        lead, booking = _world()
        client.force_login(lead.user)
        staff = client.get(reverse("hub_orientations_bookings")).content.decode()
        assert f"{reverse('hub_orientation_respond', args=[booking.pk])}?messages={booking.pk}#messages" in staff

        client.force_login(booking.member.user)
        own = client.get(reverse("hub_orientations_bookings")).content.decode()
        assert f'href="{reverse("hub_orientation_booking", args=[booking.pk])}"' in own


def describe_messages_that_ride_on_another_notice():
    def it_posts_a_move_message_once_and_puts_it_in_the_moved_email(client: Client):
        lead, booking = _world()
        client.force_login(lead.user)
        day = timezone.localdate(timezone.now() + timedelta(days=6)).isoformat()

        client.post(
            reverse("hub_orientation_move", args=[booking.pk]),
            {"date": day, "start_time": "14:00", "mode": "now", "message": "Zzthe lathe is out Tuesday"},
        )

        [message] = OrientationBooking.objects.get(pk=booking.pk).thread()
        assert (message.body, message.author_id) == ("Zzthe lathe is out Tuesday", lead.user_id)
        [email] = mail.outbox
        assert "Zzthe lathe is out Tuesday" in email.body
        assert "Zzthe lathe is out Tuesday" in email.alternatives[0][0]
        assert f"/orientation/bookings/{booking.pk}/" in email.body

    def it_puts_a_proposal_message_in_the_proposal_email(client: Client):
        lead, booking = _world()
        client.force_login(lead.user)
        day = timezone.localdate(timezone.now() + timedelta(days=6)).isoformat()

        client.post(
            reverse("hub_orientation_move", args=[booking.pk]),
            {"date": day, "start_time": "14:00", "mode": "ask", "message": "Zzdoes this work"},
        )

        assert [m.body for m in OrientationBooking.objects.get(pk=booking.pk).thread()] == ["Zzdoes this work"]
        [email] = mail.outbox
        assert "Zzdoes this work" in email.body
        assert "Zzdoes this work" in email.alternatives[0][0]

    def it_sends_no_message_when_the_move_has_none(client: Client):
        lead, booking = _world()
        client.force_login(lead.user)
        day = timezone.localdate(timezone.now() + timedelta(days=6)).isoformat()
        client.post(
            reverse("hub_orientation_move", args=[booking.pk]), {"date": day, "start_time": "14:00", "mode": "ask"}
        )
        assert OrientationBooking.objects.get(pk=booking.pk).thread() == []

    def it_shows_the_requests_note_in_the_leaders_email():
        _lead, booking = _world()
        booking.write_message(author=booking.member.user, body="Zzfirst timer")
        mail.outbox.clear()

        orientations._emit_lead_request(booking)

        [email] = mail.outbox
        assert "Note: Zzfirst timer" in email.body
        assert "Zzfirst timer" in email.alternatives[0][0]

    def it_signs_a_decline_note_with_the_decliners_name():
        lead, booking = _world()

        orientations.decline_orientation(booking, note="Zzfull this month", actor=lead.user)

        email = next(m for m in mail.outbox if m.to == [booking.member.user.email])
        assert "A note from Lead Quillfeather" in email.body
        assert "A note from Lead Quillfeather" in email.alternatives[0][0]
        assert "Zzfull this month" in email.body
