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

import json
import re
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
from tests.membership.factories import (
    CommunityEventFactory,
    EventImageFactory,
    GuildFactory,
    MembershipPlanFactory,
    tiny_png_bytes,
)

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

    def it_lets_a_calendar_administrator_clear_it(client: Client):
        # The gate is can_edit_event, which now admits a Calendar Administrator on a site-wide event.
        from membership.models import AdminCapability

        user = _user_with_role("cal_admin_photo")
        user.member.admin_capabilities.create(capability=AdminCapability.Capability.EVENTS_APPROVER)
        event = _with_photo(guild=None, event_type=CommunityEvent.EventType.COMMUNITY)
        client.login(username="cal_admin_photo", password="pass")
        assert client.post(reverse("hub_event_photo_delete", args=[event.pk])).status_code == 302
        event.refresh_from_db()
        assert not event.photo

    def it_still_lands_on_next_when_there_was_no_photo_to_clear(client: Client):
        _user_with_role("admin_nophoto", fog_role=Member.FogRole.ADMIN)
        event = CommunityEventFactory(community=True)
        client.login(username="admin_nophoto", password="pass")
        resp = client.post(reverse("hub_event_photo_delete", args=[event.pk]), {"next": "/calendar/?tab=events"})
        assert resp.status_code == 302
        assert resp["Location"] == "/calendar/?tab=events"

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


# --- The crop box, the cropped copy and the hero upload route (#831, part 3) ---------------

#: Where a cut copy lands; the storage backend may suffix a colliding name.
_COPY_NAME = re.compile(r"^events/hero-crops/[^/]+-crop(_\w+)?\.(jpg|png)$")


def _box_json(x: int, y: int, w: int, h: int) -> str:
    return json.dumps({"x": x, "y": y, "w": w, "h": h})


def _cropped(**kwargs: object) -> CommunityEvent:
    """An event saved in one go with an 800 by 500 photo and a 400 by 225 box, so the copy is cut."""
    return CommunityEventFactory(
        photo=_upload(width=800, height=500),
        hero_crop_x=100,
        hero_crop_y=50,
        hero_crop_w=400,
        hero_crop_h=225,
        **kwargs,
    )


def _copy_size(event: CommunityEvent) -> tuple[int, int]:
    with event.photo_cropped.open("rb") as handle:
        return Image.open(handle).size


def _admin(client: Client, username: str) -> User:
    user = _user_with_role(username, fog_role=Member.FogRole.ADMIN)
    client.login(username=username, password="pass")
    return user


def describe_the_hero_upload_route():
    def it_stores_the_photo_and_drops_the_box_and_the_copy(client: Client):
        _admin(client, "hero_admin1")
        event = _cropped(community=True)
        old_photo, old_copy = event.photo.name, event.photo_cropped.name
        resp = client.post(reverse("hub_event_hero_upload", args=[event.pk]), {"image": _upload("next.png")})
        assert resp.status_code == 200
        event.refresh_from_db()
        assert resp.json() == {"url": event.photo.url}
        assert event.photo.name != old_photo
        assert event.photo.name.startswith("events/photos/")
        assert event.hero_crop_box == (None, None, None, None)
        assert not event.photo_cropped
        assert not default_storage.exists(old_photo)
        assert not default_storage.exists(old_copy)

    def it_drops_the_box_even_when_the_upload_takes_the_old_name_back(client: Client):
        # The replaced file is deleted before the new one lands, so a same named upload can
        # get the old storage key; the route clears the box itself rather than trusting a rename.
        _admin(client, "hero_admin2")
        event = _cropped(community=True)
        resp = client.post(reverse("hub_event_hero_upload", args=[event.pk]), {"image": _upload("flyer.png")})
        assert resp.status_code == 200
        event.refresh_from_db()
        assert event.hero_crop_box == (None, None, None, None)
        assert not event.photo_cropped

    def it_refuses_an_oversize_file(client: Client, settings):
        settings.MAX_UPLOAD_IMAGE_BYTES = 10
        _admin(client, "hero_admin3")
        event = CommunityEventFactory(community=True)
        resp = client.post(reverse("hub_event_hero_upload", args=[event.pk]), {"image": _upload()})
        assert resp.status_code == 400
        assert "MB or smaller" in resp.json()["error"]
        event.refresh_from_db()
        assert not event.photo

    def it_refuses_a_file_that_is_not_an_image(client: Client):
        _admin(client, "hero_admin4")
        event = CommunityEventFactory(community=True)
        notes = SimpleUploadedFile("notes.txt", b"not a picture", content_type="text/plain")
        resp = client.post(reverse("hub_event_hero_upload", args=[event.pk]), {"image": notes})
        assert resp.status_code == 400
        assert "not a photo" in resp.json()["error"]

    def it_refuses_a_post_without_a_file(client: Client):
        _admin(client, "hero_admin5")
        event = CommunityEventFactory(community=True)
        resp = client.post(reverse("hub_event_hero_upload", args=[event.pk]))
        assert resp.status_code == 400
        assert resp.json() == {"error": "No file provided."}

    def it_403s_a_member_who_may_not_edit_the_event(client: Client):
        _user_with_role("hero_stranger")
        event = CommunityEventFactory(community=True)
        client.login(username="hero_stranger", password="pass")
        resp = client.post(reverse("hub_event_hero_upload", args=[event.pk]), {"image": _upload()})
        assert resp.status_code == 403
        event.refresh_from_db()
        assert not event.photo

    def it_lets_the_author_of_a_pending_proposal_upload(client: Client):
        user = _user_with_role("hero_proposer")
        event = CommunityEventFactory(pending=True, community=True, submitted_by=user)
        client.login(username="hero_proposer", password="pass")
        resp = client.post(reverse("hub_event_hero_upload", args=[event.pk]), {"image": _upload()})
        assert resp.status_code == 200
        event.refresh_from_db()
        assert event.photo

    def it_needs_a_post(client: Client):
        _admin(client, "hero_admin6")
        event = CommunityEventFactory(community=True)
        assert client.get(reverse("hub_event_hero_upload", args=[event.pk])).status_code == 405

    def it_404s_an_unknown_event(client: Client):
        _admin(client, "hero_admin7")
        assert client.post(reverse("hub_event_hero_upload", args=[999999]), {"image": _upload()}).status_code == 404


def describe_the_crop_box():
    def it_is_applied_on_create_and_the_copy_is_cut(client: Client):
        _admin(client, "crop_admin1")
        resp = client.post(
            reverse("hub_event_add"),
            data=_payload(photo=_upload(width=800, height=500), hero_crop=_box_json(100, 50, 400, 225)),
        )
        assert resp.status_code == 302
        event = CommunityEvent.objects.get(title="Zine Night")
        assert event.hero_crop_box == (100, 50, 400, 225)
        assert _COPY_NAME.match(event.photo_cropped.name), event.photo_cropped.name
        assert _copy_size(event) == (400, 225)

    def it_is_applied_on_edit(client: Client):
        _admin(client, "crop_admin2")
        event = CommunityEventFactory(community=True, photo=_upload(width=800, height=500))
        assert not event.photo_cropped
        resp = client.post(
            reverse("hub_event_edit", args=[event.pk]),
            data=_payload(title=event.title, hero_crop=_box_json(0, 0, 200, 200)),
        )
        assert resp.status_code == 302
        event.refresh_from_db()
        assert event.hero_crop_box == (0, 0, 200, 200)
        assert _copy_size(event) == (200, 200)

    def it_shrinks_with_a_downsized_upload(client: Client, settings):
        # The browser measured the box on the original; the stored photo is capped, so the box follows.
        settings.IMAGE_MAX_LONG_EDGE_HERO = 200
        _admin(client, "crop_admin3")
        client.post(
            reverse("hub_event_add"),
            data=_payload(photo=_upload(width=800, height=500), hero_crop=_box_json(0, 0, 800, 500)),
        )
        event = CommunityEvent.objects.get(title="Zine Night")
        assert event.hero_crop_box == (0, 0, 200, 125)
        assert _copy_size(event) == (200, 125)

    def it_refuses_a_malformed_box(client: Client):
        _admin(client, "crop_admin4")
        resp = client.post(reverse("hub_event_add"), data=_payload(photo=_upload(), hero_crop="nope"))
        assert resp.status_code == 200
        assert "Crop box is malformed" in resp.content.decode()
        assert not CommunityEvent.objects.filter(title="Zine Night").exists()

    def it_refuses_a_box_that_is_not_a_positive_rectangle(client: Client):
        _admin(client, "crop_admin5")
        resp = client.post(reverse("hub_event_add"), data=_payload(photo=_upload(), hero_crop=_box_json(0, 0, 0, 10)))
        assert resp.status_code == 200
        assert "positive rectangle" in resp.content.decode()

    def it_prefills_the_saved_box_in_the_composer(client: Client):
        _admin(client, "crop_admin6")
        event = _cropped(community=True)
        html = client.get(reverse("hub_event_edit", args=[event.pk])).content.decode()
        assert 'name="hero_crop"' in html
        assert "data-hero-crop-input" in html
        assert "&quot;w&quot;: 400" in html

    def it_prefills_nothing_without_a_box(client: Client):
        _admin(client, "crop_admin7")
        event = CommunityEventFactory(community=True, photo=_upload())
        html = client.get(reverse("hub_event_edit", args=[event.pk])).content.decode()
        assert "data-hero-crop-input" in html
        assert "&quot;w&quot;" not in html

    def it_keeps_the_stored_box_when_nothing_is_posted(client: Client):
        # An untouched cropper posts an empty box; the saved one stands and the copy is left alone.
        _admin(client, "crop_admin8")
        event = _cropped(community=True)
        copy_name = event.photo_cropped.name
        resp = client.post(reverse("hub_event_edit", args=[event.pk]), data=_payload(title="Renamed", hero_crop=""))
        assert resp.status_code == 302
        event.refresh_from_db()
        assert event.title == "Renamed"
        assert event.hero_crop_box == (100, 50, 400, 225)
        assert event.photo_cropped.name == copy_name


def describe_the_cropped_copy():
    def it_fronts_the_event_page_centred_on_itself(client: Client):
        event = _cropped(community=True)
        html = client.get(reverse("hub_event_detail", args=[event.pk])).content.decode()
        assert event.photo_cropped.url in html
        assert 'class="pl-event-detail__hero-img" src="' + event.photo_cropped.url in html
        assert "object-position: 50% 50%" in html

    def it_is_the_photo_itself_without_a_box(client: Client):
        event = CommunityEventFactory(community=True, photo=_upload())
        assert event.hero_image_url == event.photo.url
        assert event.hero_object_position == "50% 50%"
        html = client.get(reverse("hub_event_detail", args=[event.pk])).content.decode()
        assert 'class="pl-event-detail__hero-img" src="' + event.photo.url in html

    def it_is_blank_without_a_photo():
        event = CommunityEventFactory(community=True)
        assert event.hero_image_url == ""
        assert event.hero_object_position == "50% 50%"

    def it_is_cleared_when_the_photo_is_deleted(client: Client):
        # The delete endpoint saves only the photo column; the box and the copy are written with it.
        _admin(client, "copy_admin1")
        event = _cropped(community=True)
        copy_name = event.photo_cropped.name
        assert client.post(reverse("hub_event_photo_delete", args=[event.pk])).status_code == 302
        event.refresh_from_db()
        assert not event.photo
        assert not event.photo_cropped
        assert event.hero_crop_box == (None, None, None, None)
        assert not default_storage.exists(copy_name)

    def it_is_recut_when_the_box_moves_and_the_old_file_deleted():
        event = _cropped(community=True)
        old_copy = event.photo_cropped.name
        event.hero_crop_x, event.hero_crop_y, event.hero_crop_w, event.hero_crop_h = 0, 0, 300, 300
        event.save()
        assert event.photo_cropped.name != old_copy
        assert not default_storage.exists(old_copy)
        assert _copy_size(event) == (300, 300)

    def it_is_dropped_for_a_focal_point():
        event = _cropped(community=True)
        copy_name = event.photo_cropped.name
        event.hero_crop_w = event.hero_crop_h = 0
        event.save()
        assert not event.photo_cropped
        assert not default_storage.exists(copy_name)
        # Without a copy the mixin's answer stands: x and y read as percentages.
        assert event.hero_object_position == "100% 50%"

    def it_keeps_the_box_centre_while_there_is_no_copy():
        # A box with no copy (the copy cleared behind the row's back) still centres the banner
        # on the box through the mixin, read off the photo's own size: (100 + 200) / 800, (50 + 112.5) / 500.
        event = _cropped(community=True)
        CommunityEvent.objects.filter(pk=event.pk).update(photo_cropped="")
        event.refresh_from_db()
        assert event.hero_image_url == event.photo.url
        assert event.hero_object_position == "37.5% 32.5%"

    def it_is_cleared_for_a_box_off_the_photo():
        event = _cropped(community=True)
        event.hero_crop_x = 900
        event.save()
        assert not event.photo_cropped

    def it_is_cleared_when_pillow_cannot_read_the_file(caplog):
        stub = SimpleUploadedFile("stub.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 64, content_type="image/png")
        event = CommunityEventFactory(
            community=True, photo=stub, hero_crop_x=0, hero_crop_y=0, hero_crop_w=10, hero_crop_h=10
        )
        assert not event.photo_cropped
        assert "hero crop skipped for CommunityEvent" in caplog.text

    def it_keeps_a_png_source_as_png():
        # A file put in storage directly is never normalized to JPEG; the cut keeps its format.
        event = CommunityEventFactory(community=True)
        buf = BytesIO()
        Image.new("RGB", (600, 400), "navy").save(buf, format="PNG")
        event.photo.save("raw.png", ContentFile(buf.getvalue()), save=True)
        event.hero_crop_x, event.hero_crop_y, event.hero_crop_w, event.hero_crop_h = 0, 0, 320, 180
        event.save()
        assert event.photo_cropped.name.endswith(".png")
        with event.photo_cropped.open("rb") as handle:
            assert Image.open(handle).format == "PNG"

    def it_is_left_alone_by_an_ordinary_save():
        event = _cropped(community=True)
        copy_name = event.photo_cropped.name
        event.title = "Still the same photo"
        event.save()
        assert event.photo_cropped.name == copy_name
        assert default_storage.exists(copy_name)

    def it_is_skipped_by_a_sync_write():
        # Every Google and Discord push saves a few columns; none of that touches storage.
        event = _cropped(community=True)
        copy_name = event.photo_cropped.name
        event.sync_state = CommunityEvent.SyncState.PENDING
        event.save(update_fields=["sync_state"])
        event.refresh_from_db()
        assert event.photo_cropped.name == copy_name
        assert event.hero_crop_box == (100, 50, 400, 225)


def describe_duplicate():
    def it_copies_the_photo_bytes_and_cuts_its_own_copy():
        user = _user_with_role("dup_admin", fog_role=Member.FogRole.ADMIN)
        source = _cropped(community=True)
        EventImageFactory(event=source, alt_text="Loom", sort_order=1)
        copy = source.duplicate(by=user)
        assert copy.photo.name != source.photo.name
        assert default_storage.exists(copy.photo.name) and default_storage.exists(source.photo.name)
        assert copy.hero_crop_box == source.hero_crop_box
        assert copy.photo_cropped.name != source.photo_cropped.name
        assert default_storage.exists(copy.photo_cropped.name) and default_storage.exists(source.photo_cropped.name)
        assert _copy_size(copy) == (400, 225)
        # The gallery rows come across with the SAME storage key; nothing is read or written.
        [row] = copy.gallery_images.all()
        [source_row] = source.gallery_images.all()
        assert row.image.name == source_row.image.name
        assert (row.alt_text, row.sort_order) == ("Loom", 1)
