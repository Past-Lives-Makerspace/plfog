"""BDD specs for the Guest list tab on Manage Events (#831, part 4): the gate and the tab link,
the roster rows and stat chips, search, a flat query count, the check-in toggle, Add a guest
(over htmx and as a plain POST), Remove and the Eventbrite row it refuses, the CSV and the
update emails list."""

from __future__ import annotations

import csv
import io
import json
from unittest.mock import patch

import pytest
from allauth.account.models import EmailAddress
from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from hub.event_exports import EVENT_GUESTS_CSV_HEADERS
from membership.models import AdminCapability, CommunityEvent, EventRSVP, Member
from tests.membership.factories import (
    CommunityEventFactory,
    EventRSVPFactory,
    MembershipPlanFactory,
)

pytestmark = pytest.mark.django_db

Source = EventRSVP.Source
HX = {"HTTP_HX_REQUEST": "true"}


def _user_with_role(username: str, *, fog_role: str = Member.FogRole.MEMBER) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="pass")
    member = user.member
    member.fog_role = fog_role
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()
    return user


def _admin(client: Client, username: str = "guests_admin") -> User:
    user = _user_with_role(username, fog_role=Member.FogRole.ADMIN)
    client.login(username=username, password="pass")
    return user


def _approver(client: Client, username: str = "guests_approver") -> User:
    user = _user_with_role(username)
    user.member.admin_capabilities.create(capability=AdminCapability.Capability.EVENTS_APPROVER)
    client.login(username=username, password="pass")
    return user


def _url(event: CommunityEvent) -> str:
    return reverse("hub_admin_event_guests", args=[event.pk])


def _guest(event: CommunityEvent, **kwargs) -> EventRSVP:
    return EventRSVPFactory(event=event, guest=True, **kwargs)


def _member_row(event: CommunityEvent, name: str = "Sam K", email: str = "sam@example.com", **kwargs) -> EventRSVP:
    """A member's RSVP whose member has a linked user and a primary address (what the Email column reads)."""
    user = User.objects.create_user(username=email, email=email)
    member = user.member
    member.full_legal_name = name
    member.save(update_fields=["full_legal_name"])
    # The user signal already provisions the address; make sure it is the primary one.
    EmailAddress.objects.update_or_create(user=user, email=email, defaults={"primary": True, "verified": True})
    return EventRSVPFactory(event=event, member=member, **kwargs)


def _body(client: Client, event: CommunityEvent, query: str = "") -> str:
    return client.get(_url(event) + query).content.decode()


def describe_the_gate():
    def it_refuses_a_plain_member(client: Client):
        _user_with_role("plain_guests")
        client.login(username="plain_guests", password="pass")
        assert client.get(_url(CommunityEventFactory(community=True))).status_code == 403

    def it_opens_for_a_calendar_administrator(client: Client):
        _approver(client)
        assert client.get(_url(CommunityEventFactory(community=True))).status_code == 200

    def it_404s_studio_hours(client: Client):
        _admin(client)
        assert client.get(_url(CommunityEventFactory(studio_hours=True))).status_code == 404

    def it_links_the_tab_from_the_overview_and_marks_it_active(client: Client):
        _admin(client)
        event = CommunityEventFactory(community=True)
        overview = client.get(reverse("hub_admin_event", args=[event.pk])).content.decode()
        assert f'href="{_url(event)}" class="vote-tab" data-guests-tab>Guest list</a>' in overview
        assert 'class="vote-tab vote-tab--active" aria-current="page" data-overview-tab' in overview
        guests = _body(client, event)
        assert 'class="vote-tab vote-tab--active" aria-current="page" data-guests-tab' in guests
        assert 'class="vote-tab" data-overview-tab' in guests


def describe_the_roster():
    def it_lists_members_and_guests_with_their_door_and_contact(client: Client):
        MembershipPlanFactory()
        _admin(client)
        event = CommunityEventFactory(community=True)
        member_rsvp = _member_row(event, party_size=2)
        guest = _guest(
            event,
            name="Lee P",
            email="lee@example.com",
            party_size=3,
            wants_updates=True,
            note="Plus one",
            source=Source.STAFF,
        )
        body = _body(client, event)
        assert f'data-guest-row="{member_rsvp.pk}" data-guest-source="button"' in body
        assert "Sam K" in body and ">Member</span>" in body and "sam@example.com" in body
        assert 'data-guest-party="2"' in body
        assert f'data-guest-row="{guest.pk}" data-guest-source="staff"' in body
        assert "Lee P" in body and "Added by staff" in body and "lee@example.com" in body
        assert 'data-guest-party="3"' in body and "data-guest-updates" in body and "Plus one" in body
        assert "data-rsvp-count>5 going" in body

    def it_counts_the_stat_chips_by_door(client: Client):
        _admin(client)
        event = CommunityEventFactory(community=True)
        EventRSVPFactory(event=event)
        EventRSVPFactory(event=event, source=Source.HUB)
        _guest(event)
        _guest(event, source=Source.STAFF)
        _guest(event, source=Source.EVENTBRITE, eventbrite_order_id="order-1")
        checked = _guest(event)
        checked.toggle_check_in()
        body = _body(client, event)
        assert 'data-guest-stat="members">Members <strong>2</strong>' in body
        assert 'data-guest-stat="site">Signed up here <strong>2</strong>' in body
        assert 'data-guest-stat="staff">Added by staff <strong>1</strong>' in body
        assert 'data-guest-stat="eventbrite">Eventbrite <strong>1</strong>' in body
        assert 'data-guest-stat="checked_in">Checked in <strong>1</strong>' in body

    def it_searches_guest_names_emails_and_member_names(client: Client):
        _admin(client)
        event = CommunityEventFactory(community=True)
        member_rsvp = _member_row(event, name="Zzytrix Quon", email="zz@example.com")
        guest = _guest(event, name="Lee P", email="lee@example.com")
        other = _guest(event, name="Nobody Else", email="nobody@example.com")
        rows = (member_rsvp, guest, other)

        def _listed(query: str) -> set[int]:
            body = _body(client, event, query)
            return {row.pk for row in rows if f'data-guest-row="{row.pk}"' in body}

        assert _listed("?q=lee") == {guest.pk}
        assert _listed("?q=nobody@") == {other.pk}
        assert _listed("?q=zzytrix") == {member_rsvp.pk}
        # A member's email is not on the row and is not searched; the guests' are.
        assert _listed("?q=example.com") == {guest.pk, other.pk}

    def it_shows_an_empty_state(client: Client):
        _admin(client)
        event = CommunityEventFactory(community=True)
        assert "data-guests-empty" in _body(client, event)
        _guest(event, name="Lee P")
        assert 'No one matches "zzz"' in _body(client, event, "?q=zzz")

    def it_offers_remove_to_every_row_but_eventbrite(client: Client):
        _admin(client)
        event = CommunityEventFactory(community=True)
        guest = _guest(event)
        order = _guest(event, source=Source.EVENTBRITE, eventbrite_order_id="order-1")
        body = _body(client, event)
        assert body.count("data-guest-remove") == 1
        assert f"'remove-guest-{guest.pk}'" in body
        assert f"'remove-guest-{order.pk}'" not in body
        assert f'action="{reverse("hub_admin_event_guest_remove", args=[event.pk, guest.pk])}"' in body
        assert reverse("hub_admin_event_guest_remove", args=[event.pk, order.pk]) not in body
        # Check in stays available on every row, the Eventbrite one included.
        assert body.count("data-guest-check-in") == 2

    def it_stays_flat_as_rows_grow(client: Client):
        _admin(client)
        event = CommunityEventFactory(community=True)

        def _rows(n: int) -> None:
            for i in range(n):
                _member_row(event, name=f"Member {i}_{n}", email=f"m{i}_{n}@example.com")
                _guest(event)

        _rows(2)
        client.get(_url(event))  # the viewer's first hub page load creates their billing tab
        with CaptureQueriesContext(connection) as small:
            assert len(client.get(_url(event)).context["page"]) == 4
        _rows(4)
        with CaptureQueriesContext(connection) as large:
            assert len(client.get(_url(event)).context["page"]) == 12
        assert len(large.captured_queries) == len(small.captured_queries)


def describe_check_in():
    def _checkin_url(event: CommunityEvent, rsvp: EventRSVP) -> str:
        return reverse("hub_admin_event_guest_checkin", args=[event.pk, rsvp.pk])

    def it_toggles_over_htmx_and_returns_the_row_with_a_toast(client: Client):
        _admin(client)
        event = CommunityEventFactory(community=True)
        guest = _guest(event, name="Lee P")
        resp = client.post(_checkin_url(event, guest), **HX)
        assert resp.status_code == 200
        body = resp.content.decode()
        assert body.lstrip().startswith("<tr") and f'data-guest-row="{guest.pk}"' in body
        assert "data-guest-checked-in" in body and "pl-guest-row--checked-in" in body
        assert json.loads(resp["HX-Trigger"])["showToast"]["message"] == "Lee P checked in."
        guest.refresh_from_db()
        assert guest.checked_in_at is not None
        resp = client.post(_checkin_url(event, guest), **HX)
        assert "data-guest-check-in" in resp.content.decode()
        assert json.loads(resp["HX-Trigger"])["showToast"]["message"] == "Lee P is no longer checked in."
        guest.refresh_from_db()
        assert guest.checked_in_at is None

    def it_redirects_with_a_message_on_a_plain_post(client: Client):
        _approver(client)
        event = CommunityEventFactory(community=True)
        guest = _guest(event, name="Lee P")
        resp = client.post(_checkin_url(event, guest))
        assert resp.status_code == 302 and resp["Location"] == _url(event)
        assert [m.message for m in get_messages(resp.wsgi_request)] == ["Lee P checked in."]

    def it_404s_a_row_from_another_event(client: Client):
        _admin(client)
        event = CommunityEventFactory(community=True)
        stray = _guest(CommunityEventFactory(community=True))
        assert client.post(_checkin_url(event, stray), **HX).status_code == 404

    def it_refuses_a_get(client: Client):
        _admin(client)
        event = CommunityEventFactory(community=True)
        assert client.get(_checkin_url(event, _guest(event))).status_code == 405


def describe_add_a_guest():
    def _add_url(event: CommunityEvent) -> str:
        return reverse("hub_admin_event_guest_add", args=[event.pk])

    _GOOD = {"name": "Lee P", "email": "Lee@Example.com", "party_size": "3", "note": "Plus one"}

    def it_renders_the_modal_form(client: Client):
        _admin(client)
        event = CommunityEventFactory(community=True)
        body = _body(client, event)
        assert 'id="add-guest-body"' in body and "data-add-guest" in body
        assert f'hx-post="{reverse("hub_admin_event_guest_add", args=[event.pk])}" hx-target="#add-guest-body"' in body

    def it_adds_a_staff_row_and_sends_the_modal_back_to_the_tab(client: Client):
        _admin(client)
        event = CommunityEventFactory(community=True)
        with patch.object(CommunityEvent, "refresh_discord_announcement") as refresh:
            resp = client.post(reverse("hub_admin_event_guest_add", args=[event.pk]), dict(_GOOD), **HX)
        assert resp.status_code == 204
        assert resp["HX-Redirect"] == _url(event)
        assert refresh.called
        rsvp = event.rsvps.get()
        assert (rsvp.name, rsvp.email, rsvp.party_size, rsvp.note) == ("Lee P", "lee@example.com", 3, "Plus one")
        assert rsvp.source == Source.STAFF and rsvp.is_guest
        assert [m.message for m in get_messages(resp.wsgi_request)] == ["Added Lee P."]

    def it_returns_the_form_with_errors_into_the_modal(client: Client):
        _admin(client)
        event = CommunityEventFactory(community=True)
        resp = client.post(reverse("hub_admin_event_guest_add", args=[event.pk]), {**_GOOD, "email": "nope"}, **HX)
        assert resp.status_code == 200
        body = resp.content.decode()
        assert body.lstrip().startswith("<form") and "data-add-guest-form" in body and "pl-field-error" in body
        assert not event.rsvps.exists()

    def it_redirects_on_a_plain_post(client: Client):
        _approver(client)
        event = CommunityEventFactory(community=True)
        with patch.object(CommunityEvent, "refresh_discord_announcement"):
            resp = client.post(reverse("hub_admin_event_guest_add", args=[event.pk]), dict(_GOOD))
        assert resp.status_code == 302 and resp["Location"] == _url(event)
        assert event.rsvps.filter(email="lee@example.com").exists()

    def it_re_renders_the_tab_on_a_plain_invalid_post(client: Client):
        _admin(client)
        event = CommunityEventFactory(community=True)
        resp = client.post(reverse("hub_admin_event_guest_add", args=[event.pk]), {**_GOOD, "name": ""})
        assert resp.status_code == 200
        body = resp.content.decode()
        assert "data-guest-list" in body and "pl-field-error" in body
        assert not event.rsvps.exists()

    def it_updates_a_guest_who_signed_up_on_the_page(client: Client):
        _admin(client)
        event = CommunityEventFactory(community=True)
        existing = _guest(event, email="lee@example.com", party_size=1)
        with patch.object(CommunityEvent, "refresh_discord_announcement"):
            client.post(reverse("hub_admin_event_guest_add", args=[event.pk]), dict(_GOOD), **HX)
        rsvp = event.rsvps.get()
        assert rsvp.pk == existing.pk
        assert (rsvp.party_size, rsvp.note, rsvp.source) == (3, "Plus one", Source.SITE)


def describe_remove():
    def _remove_url(event: CommunityEvent, rsvp: EventRSVP) -> str:
        return reverse("hub_admin_event_guest_remove", args=[event.pk, rsvp.pk])

    def it_takes_the_row_off_and_redirects_with_a_message(client: Client):
        _admin(client)
        event = CommunityEventFactory(community=True)
        guest = _guest(event, name="Lee P")
        with patch.object(CommunityEvent, "refresh_discord_announcement") as refresh:
            resp = client.post(_remove_url(event, guest))
        assert resp.status_code == 302 and resp["Location"] == _url(event)
        assert refresh.called
        assert not event.rsvps.exists()
        assert [m.message for m in get_messages(resp.wsgi_request)] == ["Removed Lee P."]

    def it_refuses_an_eventbrite_row(client: Client):
        _admin(client)
        event = CommunityEventFactory(community=True)
        order = _guest(event, source=Source.EVENTBRITE, eventbrite_order_id="order-1")
        resp = client.post(_remove_url(event, order))
        assert resp.status_code == 302
        assert event.rsvps.filter(pk=order.pk).exists()
        assert any("Eventbrite" in m.message for m in get_messages(resp.wsgi_request))

    def it_404s_a_row_from_another_event(client: Client):
        _admin(client)
        event = CommunityEventFactory(community=True)
        stray = _guest(CommunityEventFactory(community=True))
        assert client.post(_remove_url(event, stray)).status_code == 404

    def it_refuses_a_get(client: Client):
        _admin(client)
        event = CommunityEventFactory(community=True)
        assert client.get(_remove_url(event, _guest(event))).status_code == 405


def describe_the_csv():
    def _csv_url(event: CommunityEvent) -> str:
        return reverse("hub_admin_event_guests_csv", args=[event.pk])

    def _rows(resp) -> list[list[str]]:
        return list(csv.reader(io.StringIO(b"".join(resp.streaming_content).decode())))

    def it_streams_every_row_with_the_door_the_contact_and_the_check_in(client: Client):
        _admin(client)
        event = CommunityEventFactory(community=True)
        _member_row(event, party_size=2)
        guest = _guest(event, name="Lee P", email="lee@example.com", phone="503", party_size=3, wants_updates=True)
        guest.note = "Plus one"
        guest.save(update_fields=["note"])
        guest.toggle_check_in()
        resp = client.get(_csv_url(event))
        assert resp.status_code == 200
        assert resp["Content-Type"].startswith("text/csv")
        assert resp["Content-Disposition"].startswith(f'attachment; filename="event-{event.pk}-guests-')
        rows = _rows(resp)
        assert rows[0] == EVENT_GUESTS_CSV_HEADERS
        assert len(rows) == 3
        member_line = next(row for row in rows[1:] if row[0] == "Sam K")
        assert member_line[1:6] == ["2", "Discord RSVP button", "sam@example.com", "", ""]
        assert member_line[6] == ""
        guest_line = next(row for row in rows[1:] if row[0] == "Lee P")
        assert guest_line[1:6] == ["3", "Event page, no account", "lee@example.com", "503", "Yes"]
        assert guest_line[6] and guest_line[7] == "Plus one" and guest_line[8]

    def it_is_gated_like_the_tab(client: Client):
        _user_with_role("plain_csv")
        client.login(username="plain_csv", password="pass")
        assert client.get(_csv_url(CommunityEventFactory(community=True))).status_code == 403

    def it_links_the_download_from_the_tab(client: Client):
        _admin(client)
        event = CommunityEventFactory(community=True)
        assert f'href="{_csv_url(event)}" hx-boost="false" data-pl-download>Download CSV</a>' in _body(client, event)


def describe_update_emails():
    def it_lists_the_opted_in_emails_sorted_in_the_clipboard_source(client: Client):
        _admin(client)
        event = CommunityEventFactory(community=True)
        _guest(event, email="zed@example.com", wants_updates=True)
        _guest(event, email="amy@example.com", wants_updates=True)
        _guest(event, email="quiet@example.com", wants_updates=False)
        _member_row(event)
        body = _body(client, event)
        assert "data-update-emails>amy@example.com, zed@example.com</textarea>" in body
        assert "data-copy-update-emails>Copy update emails (2)</button>" in body

    def it_disables_the_button_with_nobody_opted_in(client: Client):
        _admin(client)
        event = CommunityEventFactory(community=True)
        _guest(event, wants_updates=False)
        body = _body(client, event)
        assert "data-update-emails></textarea>" in body
        assert "data-copy-update-emails disabled>Copy update emails (0)</button>" in body
