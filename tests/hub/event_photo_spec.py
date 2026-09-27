"""BDD specs for an event's photo (part 3 of #505).

The upload itself on each of the three composers, the storage hygiene around replacing and
clearing it, who may clear it, and the two places a member sees it: the event page, where it
becomes the hero behind the title, and the Calendar rows, where it becomes a thumbnail.

The Discord halves live with their own machinery: the #calendar embed in
``tests/membership/event_rsvp_spec.py``'s announcement specs and
``tests/core/events/community_event_copy_spec.py``, and the Events-tab cover image in
``tests/core/integrations/discord_events_spec.py``.
"""

from __future__ import annotations

from datetime import timedelta
from io import BytesIO

import pytest
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from core.models import SiteConfiguration
from membership.models import CommunityEvent, GuildStaffMembership, Member
from tests.membership.factories import CommunityEventFactory, GuildFactory, MembershipPlanFactory, tiny_png_bytes

pytestmark = pytest.mark.django_db


def _user_with_role(username: str, *, fog_role: str = Member.FogRole.MEMBER) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    member = user.member
    member.fog_role = fog_role
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()
    return user


def _upload(name: str = "flyer.png", *, width: int = 800, height: int = 500) -> SimpleUploadedFile:
    """A real PNG of a given size, so the resize-on-save path has something to measure."""
    buf = BytesIO()
    Image.new("RGB", (width, height), "teal").save(buf, format="PNG")
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/png")


def _payload(**overrides: object) -> dict:
    data: dict = {
        "title": "Zine Night",
        "starts_at": "2026-07-11T18:00",
        "ends_at": "2026-07-11T20:00",
        "location": "Print shop",
        "description": "Bring paper.",
        "recurrence": "none",
    }
    data.update(overrides)
    return data


def _with_photo(**kwargs: object) -> CommunityEvent:
    event = CommunityEventFactory(**kwargs)
    event.photo.save("stored.png", ContentFile(tiny_png_bytes()), save=True)
    return event


def describe_uploading_a_photo():
    def it_is_optional_on_every_composer():
        from hub.forms import CommunityEventForm

        assert CommunityEventForm().fields["photo"].required is False

    def it_takes_a_photo_from_the_guild_composer(client: Client):
        user = _user_with_role("lead1")
        guild = GuildFactory(guild_lead=user.member)
        client.login(username="lead1", password="pass")
        resp = client.post(
            reverse("hub_guild_event_add", args=[guild.pk]), data=_payload(photo=_upload()), follow=False
        )
        assert resp.status_code == 302
        event = CommunityEvent.objects.get(title="Zine Night")
        assert event.photo.name.startswith("events/photos/")

    def it_takes_a_photo_from_the_admin_composer(client: Client):
        _user_with_role("admin1", fog_role=Member.FogRole.ADMIN)
        client.login(username="admin1", password="pass")
        resp = client.post(reverse("hub_event_add"), data=_payload(photo=_upload()))
        assert resp.status_code == 302
        assert CommunityEvent.objects.get(title="Zine Night").photo

    def it_takes_a_photo_from_a_members_proposal(client: Client):
        # A plain member is asked neither of #505's questions, but they still get the photo:
        # the picture is not a permission, it is the flyer.
        config = SiteConfiguration.load()
        config.member_event_policy = SiteConfiguration.MemberEventPolicy.APPROVAL
        config.save()
        _user_with_role("m1")
        client.login(username="m1", password="pass")
        resp = client.post(reverse("hub_propose_event"), data=_payload(photo=_upload()))
        assert resp.status_code == 302
        assert CommunityEvent.objects.get(title="Zine Night").photo

    def it_posts_the_composers_as_multipart_so_the_file_actually_arrives(client: Client):
        # Without the form's enctype a chosen file is silently dropped: the page saves, the
        # photo never arrives, and nothing errors. Cheap assertion, expensive bug.
        user = _user_with_role("lead2")
        guild = GuildFactory(guild_lead=user.member)
        client.login(username="lead2", password="pass")
        guild_html = client.get(reverse("hub_guild_event_add", args=[guild.pk])).content.decode()
        propose_html = client.get(reverse("hub_propose_event")).content.decode()
        assert 'enctype="multipart/form-data"' in guild_html
        assert 'enctype="multipart/form-data"' in propose_html

    def it_downscales_a_photo_past_the_hero_ceiling(client: Client, settings):
        settings.IMAGE_MAX_LONG_EDGE_HERO = 200
        user = _user_with_role("lead3")
        guild = GuildFactory(guild_lead=user.member)
        client.login(username="lead3", password="pass")
        client.post(
            reverse("hub_guild_event_add", args=[guild.pk]), data=_payload(photo=_upload(width=900, height=600))
        )
        event = CommunityEvent.objects.get(title="Zine Night")
        assert max(Image.open(event.photo).size) == 200


def describe_replacing_and_clearing():
    def it_deletes_the_file_the_new_photo_replaced(client: Client):
        user = _user_with_role("lead4")
        guild = GuildFactory(guild_lead=user.member)
        event = _with_photo(guild=guild)
        first = event.photo.name
        client.login(username="lead4", password="pass")
        client.post(
            reverse("hub_guild_event_edit", args=[guild.pk, event.pk]),
            data=_payload(title=event.title, photo=_upload("second.png")),
        )
        event.refresh_from_db()
        assert event.photo.name != first
        assert not default_storage.exists(first)

    def it_clears_the_photo_and_removes_the_stored_file(client: Client):
        user = _user_with_role("lead5")
        guild = GuildFactory(guild_lead=user.member)
        event = _with_photo(guild=guild)
        stored = event.photo.name
        client.login(username="lead5", password="pass")
        resp = client.post(reverse("hub_event_photo_delete", args=[event.pk]))
        assert resp.status_code == 302
        event.refresh_from_db()
        assert not event.photo
        assert not default_storage.exists(stored)

    def it_returns_the_author_to_the_page_they_deleted_from(client: Client):
        user = _user_with_role("lead6")
        guild = GuildFactory(guild_lead=user.member)
        event = _with_photo(guild=guild)
        back = reverse("hub_guild_event_edit", args=[guild.pk, event.pk])
        client.login(username="lead6", password="pass")
        resp = client.post(reverse("hub_event_photo_delete", args=[event.pk]), data={"next": back})
        assert resp["Location"] == back

    def it_refuses_an_off_site_next(client: Client):
        user = _user_with_role("lead7")
        guild = GuildFactory(guild_lead=user.member)
        event = _with_photo(guild=guild)
        client.login(username="lead7", password="pass")
        resp = client.post(
            reverse("hub_event_photo_delete", args=[event.pk]), data={"next": "https://evil.example/steal"}
        )
        assert resp["Location"] == reverse("hub_community_calendar") + "?tab=events"

    def it_403s_a_member_who_may_not_edit_the_event(client: Client):
        _user_with_role("stranger")
        event = _with_photo(guild=GuildFactory())
        client.login(username="stranger", password="pass")
        assert client.post(reverse("hub_event_photo_delete", args=[event.pk])).status_code == 403
        event.refresh_from_db()
        assert event.photo  # untouched

    def it_403s_another_guilds_lead(client: Client):
        # The gate is the shared can_edit_event, which scopes a lead to their own guild.
        user = _user_with_role("otherlead")
        GuildFactory(name="Metals", guild_lead=user.member)
        event = _with_photo(guild=GuildFactory(name="Print"))
        client.login(username="otherlead", password="pass")
        assert client.post(reverse("hub_event_photo_delete", args=[event.pk])).status_code == 403

    def it_lets_a_proposer_clear_the_photo_on_their_own_pending_proposal(client: Client):
        user = _user_with_role("proposer")
        event = _with_photo(guild=None, event_type=CommunityEvent.EventType.COMMUNITY)
        event.submitted_by = user
        event.moderation_state = CommunityEvent.ModerationState.PENDING
        event.save(update_fields=["submitted_by", "moderation_state"])
        client.login(username="proposer", password="pass")
        assert client.post(reverse("hub_event_photo_delete", args=[event.pk])).status_code == 302
        event.refresh_from_db()
        assert not event.photo

    def it_403s_a_proposer_once_their_event_is_published(client: Client):
        # propose_event only opens a row still in the review loop, so neither may this.
        user = _user_with_role("proposer2")
        event = _with_photo(guild=None, event_type=CommunityEvent.EventType.COMMUNITY)
        event.submitted_by = user
        event.save(update_fields=["submitted_by"])
        client.login(username="proposer2", password="pass")
        assert client.post(reverse("hub_event_photo_delete", args=[event.pk])).status_code == 403

    def it_needs_a_post(client: Client):
        _user_with_role("admin2", fog_role=Member.FogRole.ADMIN)
        event = _with_photo(guild=GuildFactory())
        client.login(username="admin2", password="pass")
        assert client.get(reverse("hub_event_photo_delete", args=[event.pk])).status_code == 405

    def it_lets_a_guild_staffer_clear_it(client: Client):
        user = _user_with_role("staffer")
        guild = GuildFactory()
        GuildStaffMembership.objects.create(guild=guild, member=user.member, role=GuildStaffMembership.Role.SECRETARY)
        event = _with_photo(guild=guild)
        client.login(username="staffer", password="pass")
        assert client.post(reverse("hub_event_photo_delete", args=[event.pk])).status_code == 302


def describe_the_event_page():
    def it_fronts_the_page_with_the_photo(client: Client):
        event = _with_photo(guild=GuildFactory(name="Print"))
        html = client.get(reverse("hub_event_detail", args=[event.pk])).content.decode()
        assert "pl-event-detail__hero--photo" in html
        assert event.photo.url in html

    def it_reads_as_it_always_did_without_one(client: Client):
        event = CommunityEventFactory(guild=GuildFactory(name="Print"))
        html = client.get(reverse("hub_event_detail", args=[event.pk])).content.decode()
        assert "pl-event-detail__hero--photo" not in html
        assert "pl-event-detail__hero-img" not in html
        assert event.title in html  # the headline still renders, photo or no photo

    def it_keeps_the_photo_out_of_the_accessible_name(client: Client):
        # The title beside it says what the event is, so the picture is decorative.
        event = _with_photo(guild=GuildFactory())
        html = client.get(reverse("hub_event_detail", args=[event.pk])).content.decode()
        assert 'class="pl-event-detail__hero-img" src="' in html
        assert 'alt=""' in html


def describe_the_calendar_rows():
    def _events_tab(client: Client) -> str:
        return client.get(reverse("hub_community_calendar") + "?tab=events").content.decode()

    def it_shows_a_thumbnail_for_an_event_with_a_photo(client: Client):
        _user_with_role("m2")
        event = _with_photo(guild=GuildFactory(), starts_at=timezone.now() + timedelta(days=2))
        event.ends_at = event.starts_at + timedelta(hours=2)
        event.save(update_fields=["ends_at"])
        client.login(username="m2", password="pass")
        html = _events_tab(client)
        assert "pl-calendar-list__thumb" in html
        assert event.photo.url in html

    def it_shows_no_thumbnail_for_an_event_without_one(client: Client):
        _user_with_role("m3")
        CommunityEventFactory(guild=GuildFactory(), starts_at=timezone.now() + timedelta(days=2))
        client.login(username="m3", password="pass")
        assert "pl-calendar-list__thumb" not in _events_tab(client)
