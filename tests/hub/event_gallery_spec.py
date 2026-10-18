"""BDD specs for an event's gallery (#831, part 3): the five routes behind the gallery manager, the
create-mode files that ride the composers, who may change the photos and what the composers render."""

from __future__ import annotations

import json
from io import BytesIO

import pytest
from django.contrib.auth.models import User
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import resolve, reverse
from PIL import Image

from core.models import SiteConfiguration
from hub.event_media_views import event_image_delete, event_image_url_base
from membership.models import CommunityEvent, EventImage, Member
from tests.membership.factories import CommunityEventFactory, EventImageFactory, GuildFactory, MembershipPlanFactory

pytestmark = pytest.mark.django_db


def _user_with_role(username: str, *, fog_role: str = Member.FogRole.MEMBER) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    member = user.member
    member.fog_role = fog_role
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()
    return user


def _admin(client: Client, username: str) -> User:
    user = _user_with_role(username, fog_role=Member.FogRole.ADMIN)
    client.login(username=username, password="pass")
    return user


def _png(name: str = "shot.png", *, width: int = 4, height: int = 4) -> SimpleUploadedFile:
    buf = BytesIO()
    Image.new("RGB", (width, height), (10, 20, 30)).save(buf, "PNG")
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


def _json_post(client: Client, url: str, body: object) -> object:
    return client.post(url, data=json.dumps(body), content_type="application/json")


def describe_the_per_image_url_base():
    def it_resolves_back_to_the_delete_route():
        # ``removesuffix`` is a silent no-op if the route verb ever changes, so assert the round trip.
        image = EventImageFactory()
        match = resolve(f"{event_image_url_base()}{image.pk}/delete/")
        assert match.func is event_image_delete
        assert match.kwargs == {"pk": image.pk}


def describe_the_upload_route():
    def it_adds_a_photo_after_the_existing_ones(client: Client):
        _admin(client, "gal_admin1")
        event = CommunityEventFactory(community=True)
        EventImageFactory(event=event, sort_order=3)
        resp = client.post(reverse("hub_event_image_upload", args=[event.pk]), {"image": _png()})
        assert resp.status_code == 200, resp.content
        data = resp.json()
        image = EventImage.objects.get(pk=data["id"])
        assert data == {"id": image.pk, "url": image.image.url, "alt_text": "", "sort_order": 4}
        assert image.image.name.startswith("events/images/")
        assert image.event == event

    def it_refuses_the_eleventh_photo(client: Client):
        _admin(client, "gal_admin2")
        event = CommunityEventFactory(community=True)
        for order in range(10):
            EventImageFactory(event=event, sort_order=order)
        resp = client.post(reverse("hub_event_image_upload", args=[event.pk]), {"image": _png()})
        assert resp.status_code == 400
        assert resp.json() == {"error": "An event can have at most 10 photos."}
        assert event.gallery_images.count() == 10

    def it_refuses_a_post_without_a_file(client: Client):
        _admin(client, "gal_admin3")
        event = CommunityEventFactory(community=True)
        resp = client.post(reverse("hub_event_image_upload", args=[event.pk]))
        assert resp.status_code == 400
        assert resp.json() == {"error": "No file provided."}

    def it_refuses_a_file_that_is_not_an_image(client: Client):
        _admin(client, "gal_admin4")
        event = CommunityEventFactory(community=True)
        notes = SimpleUploadedFile("notes.txt", b"not a picture", content_type="text/plain")
        resp = client.post(reverse("hub_event_image_upload", args=[event.pk]), {"image": notes})
        assert resp.status_code == 400
        assert "not a photo" in resp.json()["error"]
        assert event.gallery_images.count() == 0

    def it_refuses_an_oversize_file(client: Client, settings):
        settings.MAX_UPLOAD_IMAGE_BYTES = 10
        _admin(client, "gal_admin5")
        event = CommunityEventFactory(community=True)
        resp = client.post(reverse("hub_event_image_upload", args=[event.pk]), {"image": _png()})
        assert resp.status_code == 400
        assert "MB or smaller" in resp.json()["error"]

    def it_403s_a_member_who_may_not_edit_the_event(client: Client):
        _user_with_role("gal_stranger")
        event = CommunityEventFactory(community=True)
        client.login(username="gal_stranger", password="pass")
        resp = client.post(reverse("hub_event_image_upload", args=[event.pk]), {"image": _png()})
        assert resp.status_code == 403
        assert event.gallery_images.count() == 0

    def it_lets_the_author_of_a_pending_proposal_upload(client: Client):
        user = _user_with_role("gal_proposer")
        event = CommunityEventFactory(pending=True, community=True, submitted_by=user)
        client.login(username="gal_proposer", password="pass")
        resp = client.post(reverse("hub_event_image_upload", args=[event.pk]), {"image": _png()})
        assert resp.status_code == 200
        assert event.gallery_images.count() == 1

    def it_needs_a_post(client: Client):
        _admin(client, "gal_admin6")
        event = CommunityEventFactory(community=True)
        assert client.get(reverse("hub_event_image_upload", args=[event.pk])).status_code == 405

    def it_404s_an_unknown_event(client: Client):
        _admin(client, "gal_admin7")
        assert client.post(reverse("hub_event_image_upload", args=[999999]), {"image": _png()}).status_code == 404


def describe_the_reorder_route():
    def it_rewrites_the_order_from_the_ids(client: Client):
        _admin(client, "order_admin1")
        event = CommunityEventFactory(community=True)
        first, second, third = (EventImageFactory(event=event, sort_order=n) for n in range(3))
        resp = _json_post(
            client, reverse("hub_event_image_reorder", args=[event.pk]), {"order": [third.pk, first.pk, second.pk]}
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        assert list(event.gallery_images.values_list("pk", flat=True)) == [third.pk, first.pk, second.pk]

    def it_skips_ids_that_are_not_whole_numbers_or_not_its_own(client: Client):
        _admin(client, "order_admin2")
        event = CommunityEventFactory(community=True)
        mine = EventImageFactory(event=event, sort_order=5)
        other = EventImageFactory(sort_order=0)
        body = {"order": [other.pk, True, "7", None, mine.pk]}
        assert _json_post(client, reverse("hub_event_image_reorder", args=[event.pk]), body).status_code == 200
        mine.refresh_from_db()
        other.refresh_from_db()
        assert mine.sort_order == 4
        assert other.sort_order == 0

    def it_refuses_a_body_that_is_not_json(client: Client):
        _admin(client, "order_admin3")
        event = CommunityEventFactory(community=True)
        resp = client.post(
            reverse("hub_event_image_reorder", args=[event.pk]), data="nope", content_type="application/json"
        )
        assert resp.status_code == 400
        assert resp.json() == {"error": "Invalid payload."}

    def it_refuses_a_body_without_an_order(client: Client):
        _admin(client, "order_admin4")
        event = CommunityEventFactory(community=True)
        assert _json_post(client, reverse("hub_event_image_reorder", args=[event.pk]), {"ids": []}).status_code == 400

    def it_refuses_an_order_that_is_not_a_list(client: Client):
        _admin(client, "order_admin5")
        event = CommunityEventFactory(community=True)
        assert _json_post(client, reverse("hub_event_image_reorder", args=[event.pk]), {"order": 3}).status_code == 400

    def it_403s_a_stranger(client: Client):
        _user_with_role("order_stranger")
        event = CommunityEventFactory(community=True)
        client.login(username="order_stranger", password="pass")
        assert _json_post(client, reverse("hub_event_image_reorder", args=[event.pk]), {"order": []}).status_code == 403


def describe_the_delete_route():
    def it_drops_the_row_and_the_file(client: Client):
        _admin(client, "del_admin1")
        image = EventImageFactory(event=CommunityEventFactory(community=True))
        name = image.image.name
        resp = client.post(reverse("hub_event_image_delete", args=[image.pk]))
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        assert not EventImage.objects.filter(pk=image.pk).exists()
        assert not default_storage.exists(name)

    def it_keeps_a_file_another_event_still_shows(client: Client):
        # Duplicate copies the gallery rows with the source's storage keys, so the first delete
        # must leave the file for the other row and only the last one removes it.
        user = _admin(client, "del_admin2")
        source = CommunityEventFactory(community=True)
        source_row = EventImageFactory(event=source)
        copy = source.duplicate(by=user)
        [copy_row] = copy.gallery_images.all()
        name = copy_row.image.name
        assert name == source_row.image.name
        assert client.post(reverse("hub_event_image_delete", args=[copy_row.pk])).status_code == 200
        assert default_storage.exists(name)
        assert client.post(reverse("hub_event_image_delete", args=[source_row.pk])).status_code == 200
        assert not default_storage.exists(name)

    def it_403s_a_stranger(client: Client):
        _user_with_role("del_stranger")
        image = EventImageFactory(event=CommunityEventFactory(community=True))
        client.login(username="del_stranger", password="pass")
        assert client.post(reverse("hub_event_image_delete", args=[image.pk])).status_code == 403
        assert EventImage.objects.filter(pk=image.pk).exists()

    def it_lets_the_author_of_a_pending_proposal_delete(client: Client):
        user = _user_with_role("del_proposer")
        image = EventImageFactory(event=CommunityEventFactory(pending=True, community=True, submitted_by=user))
        client.login(username="del_proposer", password="pass")
        assert client.post(reverse("hub_event_image_delete", args=[image.pk])).status_code == 200

    def it_404s_an_unknown_image(client: Client):
        _admin(client, "del_admin3")
        assert client.post(reverse("hub_event_image_delete", args=[999999])).status_code == 404

    def it_needs_a_post(client: Client):
        _admin(client, "del_admin4")
        image = EventImageFactory(event=CommunityEventFactory(community=True))
        assert client.get(reverse("hub_event_image_delete", args=[image.pk])).status_code == 405


def describe_the_alt_text_route():
    def it_stores_the_text_cut_to_the_column(client: Client):
        _admin(client, "alt_admin1")
        image = EventImageFactory(event=CommunityEventFactory(community=True))
        resp = _json_post(client, reverse("hub_event_image_alt", args=[image.pk]), {"alt_text": "Loom at work"})
        assert resp.status_code == 200
        image.refresh_from_db()
        assert image.alt_text == "Loom at work"
        _json_post(client, reverse("hub_event_image_alt", args=[image.pk]), {"alt_text": "x" * 300})
        image.refresh_from_db()
        assert len(image.alt_text) == 255

    def it_refuses_a_value_that_is_not_a_string(client: Client):
        _admin(client, "alt_admin2")
        image = EventImageFactory(event=CommunityEventFactory(community=True))
        assert _json_post(client, reverse("hub_event_image_alt", args=[image.pk]), {"alt_text": 7}).status_code == 400

    def it_refuses_a_body_without_the_text(client: Client):
        _admin(client, "alt_admin3")
        image = EventImageFactory(event=CommunityEventFactory(community=True))
        resp = client.post(reverse("hub_event_image_alt", args=[image.pk]), data="{", content_type="application/json")
        assert resp.status_code == 400
        assert _json_post(client, reverse("hub_event_image_alt", args=[image.pk]), {"text": "x"}).status_code == 400


def describe_the_focus_route():
    def it_stores_the_point_and_answers_the_position(client: Client):
        _admin(client, "focus_admin1")
        image = EventImageFactory(event=CommunityEventFactory(community=True))
        resp = _json_post(client, reverse("hub_event_image_focus", args=[image.pk]), {"x": 20, "y": 80})
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "position": "20% 80%"}
        image.refresh_from_db()
        assert (image.focus_x, image.focus_y) == (20, 80)

    def it_resets_to_the_centre_on_nulls(client: Client):
        _admin(client, "focus_admin2")
        image = EventImageFactory(event=CommunityEventFactory(community=True), focus_x=20, focus_y=80)
        resp = _json_post(client, reverse("hub_event_image_focus", args=[image.pk]), {"x": None, "y": None})
        assert resp.json() == {"ok": True, "position": "50% 50%"}
        image.refresh_from_db()
        assert (image.focus_x, image.focus_y) == (None, None)

    def it_refuses_a_point_off_the_photo(client: Client):
        _admin(client, "focus_admin3")
        image = EventImageFactory(event=CommunityEventFactory(community=True))
        resp = _json_post(client, reverse("hub_event_image_focus", args=[image.pk]), {"x": 120, "y": 0})
        assert resp.status_code == 400
        assert "0 to 100" in resp.json()["error"]


def describe_create_mode():
    def it_stores_the_files_posted_with_the_admin_composer(client: Client):
        _admin(client, "create_admin1")
        data = _payload()
        data["gallery_images"] = [_png("a.png"), _png("b.png")]
        resp = client.post(reverse("hub_event_add"), data=data)
        assert resp.status_code == 302
        event = CommunityEvent.objects.get(title="Zine Night")
        rows = list(event.gallery_images.all())
        assert [row.sort_order for row in rows] == [0, 1]
        assert all(row.image.name.startswith("events/images/") for row in rows)

    def it_stores_them_from_the_propose_page(client: Client):
        config = SiteConfiguration.load()
        config.member_event_policy = SiteConfiguration.MemberEventPolicy.APPROVAL
        config.save()
        _user_with_role("create_member")
        client.login(username="create_member", password="pass")
        data = _payload()
        data["gallery_images"] = [_png("a.png")]
        assert client.post(reverse("hub_propose_event"), data=data).status_code == 302
        assert CommunityEvent.objects.get(title="Zine Night").gallery_images.count() == 1

    def it_stores_them_from_the_guild_composer(client: Client):
        user = _user_with_role("create_lead")
        guild = GuildFactory(guild_lead=user.member)
        client.login(username="create_lead", password="pass")
        data = _payload()
        data["gallery_images"] = [_png("a.png")]
        assert client.post(reverse("hub_guild_event_add", args=[guild.pk]), data=data).status_code == 302
        assert CommunityEvent.objects.get(title="Zine Night").gallery_images.count() == 1

    def it_refuses_a_batch_over_the_cap_before_saving_anything(client: Client):
        _admin(client, "create_admin2")
        data = _payload()
        data["gallery_images"] = [_png(f"{n}.png") for n in range(11)]
        resp = client.post(reverse("hub_event_add"), data=data)
        assert resp.status_code == 200
        assert "An event can have at most 10 photos." in resp.content.decode()
        assert not CommunityEvent.objects.filter(title="Zine Night").exists()

    def it_refuses_a_file_that_is_not_an_image_before_saving_anything(client: Client):
        _admin(client, "create_admin3")
        data = _payload()
        data["gallery_images"] = [SimpleUploadedFile("notes.txt", b"not a picture", content_type="text/plain")]
        resp = client.post(reverse("hub_event_add"), data=data)
        assert resp.status_code == 200
        assert "not a photo" in resp.content.decode()
        assert not CommunityEvent.objects.filter(title="Zine Night").exists()

    def it_counts_a_saved_events_photos_against_the_cap(client: Client):
        # The manager posts straight to the upload route once saved, but a posted file still lands.
        _admin(client, "create_admin4")
        event = CommunityEventFactory(community=True)
        for order in range(9):
            EventImageFactory(event=event, sort_order=order)
        data = _payload(title=event.title)
        data["gallery_images"] = [_png("a.png"), _png("b.png")]
        resp = client.post(reverse("hub_event_edit", args=[event.pk]), data=data)
        assert resp.status_code == 200
        assert "An event can have at most 10 photos." in resp.content.decode()
        data["gallery_images"] = [_png("a.png")]
        assert client.post(reverse("hub_event_edit", args=[event.pk]), data=data).status_code == 302
        assert event.gallery_images.count() == 10
        assert event.gallery_images.order_by("-sort_order").first().sort_order == 9


def describe_the_composer():
    def it_renders_the_manager_for_a_saved_event(client: Client):
        _admin(client, "render_admin1")
        event = CommunityEventFactory(community=True)
        EventImageFactory(event=event, alt_text="Loom")
        html = client.get(reverse("hub_event_edit", args=[event.pk])).content.decode()
        assert 'id="gallery-manager"' in html
        assert f'data-upload-url="{reverse("hub_event_image_upload", args=[event.pk])}"' in html
        assert f'data-reorder-url="{reverse("hub_event_image_reorder", args=[event.pk])}"' in html
        assert f'data-image-url-base="{event_image_url_base()}"' in html
        assert 'value="Loom"' in html
        assert "on your event page" in html

    def it_renders_the_create_mode_input_for_a_new_event(client: Client):
        _admin(client, "render_admin2")
        html = client.get(reverse("hub_event_add")).content.decode()
        assert 'id="gallery-create"' in html
        assert 'name="gallery_images"' in html

    def it_renders_the_create_mode_input_on_the_propose_page_and_the_guild_composer(client: Client):
        config = SiteConfiguration.load()
        config.member_event_policy = SiteConfiguration.MemberEventPolicy.APPROVAL
        config.save()
        user = _user_with_role("render_lead")
        guild = GuildFactory(guild_lead=user.member)
        client.login(username="render_lead", password="pass")
        assert 'name="gallery_images"' in client.get(reverse("hub_propose_event")).content.decode()
        assert 'name="gallery_images"' in client.get(reverse("hub_guild_event_add", args=[guild.pk])).content.decode()
