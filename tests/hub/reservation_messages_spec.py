"""Specs for an equipment reservation's messages on the hub (the shared thread, #974).

The Messages modal's fragment and the send endpoint for both sides and their refusals, the
Messages items and buttons on every list that shows reservations, the New message cue, and the
modal reopening on ``?messages=res-<pk>``. Names and message text are factory strings no
changelog could contain (STANDARDS.md section 8).
"""

from __future__ import annotations

from datetime import timedelta
from io import BytesIO

import pytest
from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.http import HttpResponse
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from core.models import Notification
from membership.models import EquipmentReservation, EquipmentReservationMessageRead, Member
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentReservationFactory,
    EquipmentStaffMembershipFactory,
    MembershipPlanFactory,
)

pytestmark = pytest.mark.django_db

HX = {"HTTP_HX_REQUEST": "true"}


def _member(username: str, name: str) -> Member:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass", email=f"{username}@example.com")
    member = user.member
    member.status = Member.Status.ACTIVE
    member.full_legal_name = name
    member.save(update_fields=["status", "full_legal_name"])
    return member


def _login(client: Client, member: Member) -> None:
    assert member.user is not None
    client.force_login(member.user)


def _world(*, status: str = EquipmentReservation.Status.PENDING_APPROVAL) -> tuple[Member, EquipmentReservation]:
    """A manager of an approval tool and a reservation on it by another member."""
    equipment = EquipmentFactory(name="Zzbandsaw Prime", requires_approval=True)
    manager = _member("rmsg_mgr", "Sami Ironwood")
    EquipmentStaffMembershipFactory(equipment=equipment, member=manager)
    starts = timezone.now() + timedelta(days=2)
    reservation = EquipmentReservationFactory(
        equipment=equipment,
        member=_member("rmsg_bk", "Robin Valewright"),
        starts_at=starts,
        ends_at=starts + timedelta(hours=1),
        status=status,
        purpose="Zzsign blanks",
    )
    return manager, reservation


def _photo() -> SimpleUploadedFile:
    buf = BytesIO()
    Image.new("RGB", (1, 1)).save(buf, format="PNG")
    return SimpleUploadedFile("shot.png", buf.getvalue(), content_type="image/png")


def _messages(response: HttpResponse) -> list[str]:
    return [str(message) for message in get_messages(response.wsgi_request)]  # type: ignore[attr-defined]


def _fragment_url(reservation: EquipmentReservation, next_url: str = "") -> str:
    url = reverse("hub_equipment_reservation_messages", args=[reservation.pk])
    return f"{url}?next={next_url}" if next_url else url


def _send_url(reservation: EquipmentReservation) -> str:
    return reverse("hub_equipment_reservation_message", args=[reservation.pk])


def describe_the_messages_fragment():
    def it_shows_the_member_their_thread_and_a_box_to_the_managers(client: Client):
        _manager, reservation = _world()
        _login(client, reservation.member)
        content = client.get(_fragment_url(reservation, "/equipment/?view=bookings"), **HX).content.decode()
        assert f'data-reservation-thread="{reservation.pk}"' in content
        assert "Zzbandsaw Prime" in content
        assert "Zzsign blanks" in content
        assert "No messages yet. Ask the managers anything about this reservation." in content
        assert "Write to the managers" in content
        assert "The managers of the Zzbandsaw Prime get a notification." in content
        assert _send_url(reservation) in content
        assert 'name="next" value="/equipment/?view=bookings"' in content
        assert "Robin Valewright" not in content  # the member's own view names nobody

    def it_shows_a_manager_the_members_name_and_who_hears_them(client: Client):
        manager, reservation = _world()
        reservation.write_message(author=reservation.member.user, body="Zzwhich blade?")
        _login(client, manager)
        content = client.get(_fragment_url(reservation), **HX).content.decode()
        assert "Zzbandsaw Prime</strong> · Robin Valewright" in content
        assert "Zzwhich blade?" in content
        assert "<strong>Robin Valewright</strong>" in content  # never "You" for someone else's message
        assert "<strong>You</strong>" not in content
        assert "Write to Robin Valewright" in content
        assert "Robin Valewright gets a notification." in content
        assert 'name="next"' not in content

    def it_offers_an_admin_the_thread_on_any_reservation(client: Client):
        _manager, reservation = _world()
        admin = _member("rmsg_admin", "Ada Admin")
        admin.fog_role = Member.FogRole.ADMIN
        admin.save()
        _login(client, admin)
        response = client.get(_fragment_url(reservation), **HX)
        assert response.status_code == 200
        assert "Write to Robin Valewright" in response.content.decode()

    def it_marks_the_thread_read_and_tells_nobody(client: Client):
        manager, reservation = _world()
        reservation.write_message(author=reservation.member.user, body="Zzhi")
        _login(client, manager)
        client.get(_fragment_url(reservation), **HX)
        assert EquipmentReservationMessageRead.objects.filter(reservation=reservation, user=manager.user).exists()
        assert not Notification.objects.exists()

    def it_calls_the_members_own_message_you(client: Client):
        _manager, reservation = _world()
        reservation.write_message(author=reservation.member.user, body="Zzmine")
        _login(client, reservation.member)
        content = client.get(_fragment_url(reservation), **HX).content.decode()
        assert "<strong>You</strong>" in content

    def it_drops_a_next_naming_another_host(client: Client):
        _manager, reservation = _world()
        _login(client, reservation.member)
        content = client.get(_fragment_url(reservation, "https://evil.example/"), **HX).content.decode()
        assert "evil.example" not in content

    def it_sends_a_plain_visit_to_the_viewers_page_with_the_modal_open(client: Client):
        manager, reservation = _world()
        _login(client, reservation.member)
        response = client.get(_fragment_url(reservation))
        assert response.status_code == 302
        assert response["Location"] == reservation.member_messages_url
        client.logout()
        _login(client, manager)
        assert client.get(_fragment_url(reservation))["Location"] == reservation.staff_messages_url

    def it_hides_the_thread_from_an_outsider(client: Client):
        _manager, reservation = _world()
        _login(client, _member("rmsg_out", "Otto Outsider"))
        assert client.get(_fragment_url(reservation), **HX).status_code == 404
        assert client.post(_send_url(reservation), {"body": "Zzsneaky"}).status_code == 404
        assert not reservation.messages.exists()

    def it_has_no_thread_on_a_block(client: Client):
        manager, reservation = _world()
        EquipmentReservation.objects.filter(pk=reservation.pk).update(kind=EquipmentReservation.Kind.BLOCK)
        _login(client, manager)
        assert client.get(_fragment_url(reservation), **HX).status_code == 404
        assert client.post(_send_url(reservation), {"body": "Zzno"}).status_code == 404


def describe_sending():
    def it_posts_the_members_message_with_a_photo_and_names_who_heard(client: Client):
        manager, reservation = _world()
        _login(client, reservation.member)
        response = client.post(
            _send_url(reservation),
            {"body": "Zzis the fence set?", "photos": [_photo()], "next": "/equipment/?view=bookings"},
        )
        assert response.status_code == 302
        assert response["Location"] == f"/equipment/?view=bookings&messages=res-{reservation.pk}"
        assert _messages(response) == ["Sent. The managers of the Zzbandsaw Prime were notified."]
        message = reservation.messages.get()
        assert message.photos.count() == 1
        assert list(Notification.objects.values_list("user_id", flat=True)) == [manager.user.pk]

    def it_lands_a_manager_on_the_manage_tab_by_default(client: Client):
        manager, reservation = _world()
        _login(client, manager)
        response = client.post(_send_url(reservation), {"body": "Zzbring a push stick"})
        assert response["Location"] == reservation.staff_messages_url
        assert _messages(response) == ["Sent. Robin Valewright was notified."]

    def it_ignores_a_next_naming_another_host(client: Client):
        _manager, reservation = _world()
        _login(client, reservation.member)
        response = client.post(_send_url(reservation), {"body": "Zzhi", "next": "https://evil.example/"})
        assert response["Location"] == reservation.member_messages_url

    def it_refuses_a_message_with_no_text_and_reopens_the_thread(client: Client):
        _manager, reservation = _world()
        _login(client, reservation.member)
        response = client.post(_send_url(reservation), {"body": ""})
        assert response["Location"] == reservation.member_messages_url
        assert _messages(response) == ["This field is required."]
        assert not reservation.messages.exists()

    def it_accepts_messages_from_both_sides_while_awaiting_approval(client: Client):
        manager, reservation = _world()
        _login(client, reservation.member)
        client.post(_send_url(reservation), {"body": "Zzquestion"})
        client.logout()
        _login(client, manager)
        client.post(_send_url(reservation), {"body": "Zzanswer"})
        reservation.refresh_from_db()
        assert reservation.status == EquipmentReservation.Status.PENDING_APPROVAL
        assert list(reservation.messages.values_list("body", flat=True)) == ["Zzquestion", "Zzanswer"]


def describe_the_bookings_tab():
    def it_offers_messages_and_a_new_message_cue_on_the_members_row(client: Client):
        manager, reservation = _world()
        reservation.post_message(author=manager.user, body="Zzhello")
        _login(client, reservation.member)
        content = client.get(f"{reverse('hub_equipment_index')}?view=bookings").content.decode()
        assert f'data-reservation-messages="{reservation.pk}"' in content
        assert f'data-thread-unread="res-{reservation.pk}"' in content
        assert 'id="reservation-messages-body"' in content
        assert "data-reservation-thread-autoload" not in content

    def it_clears_the_cue_once_the_thread_was_opened(client: Client):
        manager, reservation = _world()
        reservation.post_message(author=manager.user, body="Zzhello")
        _login(client, reservation.member)
        client.get(_fragment_url(reservation), **HX)
        content = client.get(f"{reverse('hub_equipment_index')}?view=bookings").content.decode()
        assert f'data-thread-unread="res-{reservation.pk}"' not in content

    def it_opens_the_thread_on_arrival_with_its_key(client: Client):
        _manager, reservation = _world()
        _login(client, reservation.member)
        content = client.get(reservation.member_messages_url).content.decode()
        assert "data-reservation-thread-autoload" in content
        assert _fragment_url(reservation) in content

    @pytest.mark.parametrize("key", ["res-abc", "12", "res-999999"])
    def it_opens_nothing_for_a_key_it_cannot_read(client: Client, key: str):
        _manager, reservation = _world()
        _login(client, reservation.member)
        content = client.get(f"{reverse('hub_equipment_index')}?view=bookings&messages={key}").content.decode()
        assert "data-reservation-thread-autoload" not in content

    def it_opens_nothing_for_someone_elses_reservation(client: Client):
        _manager, reservation = _world()
        _login(client, _member("rmsg_nosy", "Nora Nosy"))
        content = client.get(reservation.member_messages_url).content.decode()
        assert "data-reservation-thread-autoload" not in content

    def it_opens_nothing_for_a_blocks_key(client: Client):
        manager, reservation = _world()
        EquipmentReservation.objects.filter(pk=reservation.pk).update(kind=EquipmentReservation.Kind.BLOCK)
        _login(client, manager)
        content = client.get(f"{reverse('hub_manage_bookings')}?messages=res-{reservation.pk}").content.decode()
        assert "data-reservation-thread-autoload" not in content

    def it_offers_messages_on_a_managers_staff_rows(client: Client):
        manager, reservation = _world()
        reservation.post_message(author=reservation.member.user, body="Zzping")
        _login(client, manager)
        content = client.get(f"{reverse('hub_equipment_index')}?view=bookings&show=needs_approval").content.decode()
        assert f'data-reservation-messages="{reservation.pk}"' in content
        assert f'data-thread-unread="res-{reservation.pk}"' in content


def describe_the_manage_page():
    def it_offers_messages_on_waiting_and_upcoming_rows_with_the_cue(client: Client):
        manager, waiting = _world()
        confirmed = EquipmentReservationFactory(
            equipment=waiting.equipment,
            member=waiting.member,
            starts_at=waiting.starts_at + timedelta(hours=3),
            ends_at=waiting.starts_at + timedelta(hours=4),
        )
        confirmed.post_message(author=waiting.member.user, body="Zzon the confirmed one")
        _login(client, manager)
        url = f"{reverse('hub_equipment_manage', args=[waiting.equipment.slug])}?tab=reservations"
        content = client.get(url).content.decode()
        assert f'data-reservation-messages="{waiting.pk}"' in content
        assert f'data-reservation-messages="{confirmed.pk}"' in content
        assert f'data-thread-unread="res-{confirmed.pk}"' in content
        assert f'data-thread-unread="res-{waiting.pk}"' not in content
        assert "data-reservation-thread-autoload" not in content

    def it_opens_the_thread_from_a_message_notice(client: Client):
        manager, reservation = _world()
        _login(client, manager)
        content = client.get(reservation.staff_messages_url).content.decode()
        assert "data-reservation-thread-autoload" in content


def describe_manage_bookings():
    def it_offers_messages_on_the_needs_approval_rows(client: Client):
        manager, reservation = _world()
        _login(client, manager)
        content = client.get(reverse("hub_manage_bookings")).content.decode()
        assert f'data-reservation-messages="{reservation.pk}"' in content
        assert 'id="reservation-messages-body"' in content
        reopened = client.get(f"{reverse('hub_manage_bookings')}?messages=res-{reservation.pk}").content.decode()
        assert "data-reservation-thread-autoload" in reopened


def describe_the_equipment_page():
    @pytest.mark.parametrize(
        "status",
        [
            EquipmentReservation.Status.PENDING_APPROVAL,
            EquipmentReservation.Status.DECLINED,
            EquipmentReservation.Status.CONFIRMED,
        ],
    )
    def it_links_each_of_the_members_reservations_to_its_thread(client: Client, status: str):
        manager, reservation = _world(status=status)
        reservation.post_message(author=manager.user, body="Zzhello there")
        _login(client, reservation.member)
        content = client.get(reverse("hub_equipment_detail", args=[reservation.equipment.slug])).content.decode()
        assert f'data-my-reservation-messages="{reservation.pk}"' in content
        assert reservation.member_messages_url.replace("&", "&amp;") in content
        assert f'data-thread-unread="res-{reservation.pk}"' in content

    def it_links_a_reservation_a_manager_cancelled(client: Client):
        manager, reservation = _world(status=EquipmentReservation.Status.CONFIRMED)
        reservation.cancel(manager, reason="Zzleak in the shop", as_manager=True)
        _login(client, reservation.member)
        content = client.get(reverse("hub_equipment_detail", args=[reservation.equipment.slug])).content.decode()
        assert f'data-my-reservation-messages="{reservation.pk}"' in content


def describe_query_counts():
    """A list's Messages buttons and New message pills cost no query per row: 1 or 6 rows read the same."""

    def _threaded(
        reservation: EquipmentReservation, manager: Member, offset: int, *, member_last: bool
    ) -> EquipmentReservation:
        """Another reservation by the same member on the same tool, with a message from each side.

        ``member_last`` puts the member's message second, so it is unread for the manager; else the manager's is.
        """
        starts = reservation.starts_at + timedelta(hours=2 * offset)
        row = EquipmentReservationFactory(
            equipment=reservation.equipment,
            member=reservation.member,
            starts_at=starts,
            ends_at=starts + timedelta(hours=1),
            status=EquipmentReservation.Status.CONFIRMED,
        )
        assert manager.user is not None
        writers = [manager.user, reservation.member.user] if member_last else [reservation.member.user, manager.user]
        for index, author in enumerate(writers):
            row.post_message(author=author, body=f"Zzmsg {offset} {index}")
        return row

    def _count(client: Client, url: str) -> int:
        with CaptureQueriesContext(connection) as queries:
            assert client.get(url).status_code == 200
        return len(queries.captured_queries)

    def it_keeps_the_manage_reservations_tab_flat(client: Client):
        manager, reservation = _world(status=EquipmentReservation.Status.CONFIRMED)
        _threaded(reservation, manager, 1, member_last=True)
        _login(client, manager)
        url = f"{reverse('hub_equipment_manage', args=[reservation.equipment.slug])}?tab=reservations"
        client.get(url)  # warm the per process caches first
        one = _count(client, url)
        for offset in range(2, 7):
            _threaded(reservation, manager, offset, member_last=True)
        content = client.get(url).content.decode()
        assert content.count('data-reservation-messages="') == 7
        assert content.count('data-thread-unread="res-') == 6
        assert _count(client, url) == one

    def it_keeps_the_members_bookings_tab_flat(client: Client):
        manager, reservation = _world(status=EquipmentReservation.Status.CONFIRMED)
        _threaded(reservation, manager, 1, member_last=False)
        _login(client, reservation.member)
        url = f"{reverse('hub_equipment_index')}?view=bookings"
        client.get(url)  # warm the per process caches first
        one = _count(client, url)
        for offset in range(2, 7):
            _threaded(reservation, manager, offset, member_last=False)
        content = client.get(url).content.decode()
        assert content.count('role="menuitem" data-reservation-messages=') == 7
        assert content.count('data-thread-unread="res-') == 6
        assert _count(client, url) == one
