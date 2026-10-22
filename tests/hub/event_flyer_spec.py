"""BDD specs for a Community Event's printable flyer (hub_event_flyer): who may see it (public once published, else
only those who may edit the event), what the sheet carries (hero, title, the next date with no dash, the place, the
pitch or the description, the RSVP goal only when set, the QR to the public page with its short URL, the Past Lives
name and address), the Flyer links on the event page's Share & Print card and on each Manage Events row, and the
model's flyer helpers."""

from __future__ import annotations

import io
from datetime import datetime, timedelta

import pytest
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from membership.models import CommunityEvent, FlyerWhen, Member
from tests.membership.factories import (
    CommunityEventFactory,
    GuildFactory,
    LocationFactory,
    MembershipPlanFactory,
)

pytestmark = pytest.mark.django_db

ADDRESS = "2808 SE 9th Ave"


def _flyer(event: CommunityEvent) -> str:
    return reverse("hub_event_flyer", args=[event.pk])


def _user_with_role(username: str, *, fog_role: str = Member.FogRole.MEMBER) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="pass")
    member = user.member
    member.fog_role = fog_role
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()
    return user


def _signed_in(username: str, *, fog_role: str = Member.FogRole.MEMBER) -> tuple[Client, User]:
    user = _user_with_role(username, fog_role=fog_role)
    client = Client()
    client.login(username=username, password="pass")
    return client, user


def _png() -> SimpleUploadedFile:
    buffer = io.BytesIO()
    Image.new("RGB", (1200, 600), (9, 46, 76)).save(buffer, format="PNG")
    return SimpleUploadedFile("hero.png", buffer.getvalue(), content_type="image/png")


def _portland(year: int, month: int, day: int, hour: int) -> datetime:
    return timezone.make_aware(datetime(year, month, day, hour, 0))


def describe_who_may_see_it():
    def it_is_public_for_a_published_event(client: Client):
        event = CommunityEventFactory(community=True, title="Winter Open House")

        response = client.get(_flyer(event))

        assert response.status_code == 200
        assert '<h1 class="pl-evfl__title" data-evfl-title>Winter Open House</h1>' in response.content.decode()

    def it_404s_an_unpublished_event_for_a_stranger(client: Client):
        event = CommunityEventFactory(pending=True)

        assert client.get(_flyer(event)).status_code == 404

    def it_404s_an_unpublished_event_for_a_member_who_cannot_edit_it():
        event = CommunityEventFactory(pending=True)
        client, _ = _signed_in("flyer_plain")

        assert client.get(_flyer(event)).status_code == 404

    def it_shows_an_unpublished_event_to_someone_who_may_edit_it():
        client, user = _signed_in("flyer_lead")
        event = CommunityEventFactory(guild=GuildFactory(guild_lead=user.member), title="Forge Night")
        event.moderation_state = CommunityEvent.ModerationState.DRAFT
        event.save(update_fields=["moderation_state"])

        response = client.get(_flyer(event))

        body = response.content.decode()
        assert response.status_code == 200
        assert "Forge Night" in body
        assert "data-evfl-unpublished" in body
        assert "data-evfl-event-link" not in body  # the event page is a 404 until it is published

    def it_shows_an_admin_a_pending_proposal():
        client, _ = _signed_in("flyer_admin", fog_role=Member.FogRole.ADMIN)
        event = CommunityEventFactory(pending=True)

        assert client.get(_flyer(event)).status_code == 200

    def it_404s_an_unknown_pk(client: Client):
        assert client.get(reverse("hub_event_flyer", args=[999999])).status_code == 404


def describe_an_ended_event():
    def it_says_a_one_off_has_already_taken_place(client: Client):
        start = timezone.now() - timedelta(days=3)
        event = CommunityEventFactory(community=True, starts_at=start, ends_at=start + timedelta(hours=2))

        body = client.get(_flyer(event)).content.decode()

        assert "data-evfl-past>This event has already taken place.</p>" in body

    def it_has_no_note_for_an_upcoming_event_or_a_series(client: Client):
        start = timezone.now() - timedelta(days=30)
        series = CommunityEventFactory(
            community=True,
            starts_at=start,
            ends_at=start + timedelta(hours=2),
            recurrence=CommunityEvent.Recurrence.WEEKLY,
        )

        assert "data-evfl-past" not in client.get(_flyer(CommunityEventFactory(community=True))).content.decode()
        assert "data-evfl-past" not in client.get(_flyer(series)).content.decode()


def describe_the_sheet():
    def it_carries_the_title_date_place_pitch_goal_qr_and_address(client: Client):
        event = CommunityEventFactory(
            community=True,
            title="Holiday Bizarre",
            starts_at=timezone.now() + timedelta(days=10),
            location="The big room",
            area=LocationFactory(name="Main Floor"),
            pitch="Handmade gifts, wine and music.",
            rsvp_goal=150,
        )

        body = client.get(_flyer(event)).content.decode()

        when = event.flyer_when
        assert f'<p class="pl-evfl__date">{when.date}</p>' in body
        assert f"{when.time}</p>" in body
        assert "data-evfl-where>Main Floor · The big room</p>" in body
        assert "data-evfl-pitch>Handmade gifts, wine and music.</p>" in body
        assert 'data-evfl-goal="150">Help us reach 150 people</p>' in body
        assert '<div class="pl-evfl__qr"><svg' in body
        assert f"data-evfl-url>{event.short_url}</p>" in body
        assert f'<p class="pl-evfl__venue-line">{ADDRESS}</p>' in body
        assert "data-evfl-print>Print / Save as PDF</button>" in body
        assert f'href="{reverse("hub_event_detail", args=[event.pk])}"' in body

    def it_puts_the_hero_on_top_when_there_is_a_photo(client: Client):
        event = CommunityEventFactory(community=True, photo=_png())

        body = client.get(_flyer(event)).content.decode()

        assert f'<img class="pl-evfl__hero" src="{event.hero_image_url}" alt=""' in body
        assert "pl-evfl__sheet--plain" not in body

    def it_leaves_the_hero_out_without_a_photo(client: Client):
        body = client.get(_flyer(CommunityEventFactory(community=True))).content.decode()

        assert "data-evfl-hero" not in body
        assert "pl-evfl__sheet--plain" in body

    def it_falls_back_to_the_description_without_a_pitch(client: Client):
        event = CommunityEventFactory(community=True, description="<p>Bring a <strong>friend</strong> along.</p>")

        body = client.get(_flyer(event)).content.decode()

        assert "data-evfl-pitch>Bring a friend along.</p>" in body

    def it_leaves_out_the_goal_place_and_pitch_when_the_event_has_none(client: Client):
        body = client.get(_flyer(CommunityEventFactory(community=True))).content.decode()

        assert "data-evfl-goal" not in body
        assert "data-evfl-where" not in body
        assert "data-evfl-pitch" not in body

    def it_says_one_person_for_a_goal_of_one(client: Client):
        body = client.get(_flyer(CommunityEventFactory(community=True, rsvp_goal=1))).content.decode()

        assert 'data-evfl-goal="1">Help us reach 1 person</p>' in body

    def it_names_a_series_repeat(client: Client):
        event = CommunityEventFactory(community=True, recurrence=CommunityEvent.Recurrence.WEEKLY)

        body = client.get(_flyer(event)).content.decode()

        assert "data-evfl-cadence>Every week</span>" in body


def describe_flyer_when():
    def it_spells_out_a_one_off_with_no_dash(settings):
        settings.TIME_ZONE = "America/Los_Angeles"
        start = _portland(2030, 12, 7, 17)
        event = CommunityEventFactory(community=True, starts_at=start, ends_at=start + timedelta(hours=4))

        assert event.flyer_when == FlyerWhen(date="Saturday, December 7", time="5:00 PM to 9:00 PM", cadence="")

    def it_names_the_next_date_of_a_series_not_its_first():
        first = timezone.localtime(timezone.now() - timedelta(days=21)).replace(hour=18, minute=0, second=0)
        event = CommunityEventFactory(
            community=True,
            starts_at=first,
            ends_at=first + timedelta(hours=2),
            recurrence=CommunityEvent.Recurrence.WEEKLY,
        )

        when = event.flyer_when

        upcoming = timezone.localtime(event.next_occurrence_start())
        assert upcoming > timezone.now() - timedelta(hours=2)
        assert when.date == upcoming.strftime("%A, %B %-d")
        assert when.time == "6:00 PM to 8:00 PM"
        assert when.cadence == "Every week"


def describe_the_url_helpers():
    def it_drops_the_scheme_for_the_short_url(settings):
        settings.MEMBER_BASE_URL = "https://members.pastlives.space"
        event = CommunityEventFactory(community=True)

        assert event.short_url == f"members.pastlives.space/events/{event.pk}/"

    def it_drops_a_plain_http_scheme_too(settings):
        settings.MEMBER_BASE_URL = "http://pastlives.test"
        event = CommunityEventFactory(community=True)

        assert event.short_url == f"pastlives.test/events/{event.pk}/"

    def it_is_published_only_when_live():
        assert CommunityEventFactory(community=True).is_published
        assert not CommunityEventFactory(pending=True).is_published


def describe_the_flyer_links():
    def it_puts_a_flyer_link_on_the_event_pages_share_and_print_card():
        client, user = _signed_in("flyer_card_lead")
        event = CommunityEventFactory(guild=GuildFactory(guild_lead=user.member))

        body = client.get(reverse("hub_event_detail", args=[event.pk])).content.decode()

        assert f'href="{_flyer(event)}" target="_blank" rel="noopener" data-qr-share-flyer>Flyer</a>' in body

    def it_leaves_the_card_and_its_flyer_link_off_for_a_visitor(client: Client):
        event = CommunityEventFactory(community=True)

        body = client.get(reverse("hub_event_detail", args=[event.pk])).content.decode()

        assert "data-qr-share-flyer" not in body

    def it_puts_a_flyer_link_on_each_manage_events_row():
        client, _ = _signed_in("flyer_list_admin", fog_role=Member.FogRole.ADMIN)
        live = CommunityEventFactory(community=True)
        pending = CommunityEventFactory(pending=True)

        body = client.get(reverse("hub_admin_events")).content.decode()

        for event in (live, pending):
            assert f'href="{_flyer(event)}" target="_blank" rel="noopener" data-event-flyer>Flyer</a>' in body
