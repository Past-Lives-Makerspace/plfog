"""The composer's image (#1064): attach, keep, remove, refuse, preview, test and show it.

Covers step 1's field (a multipart form, the drop zone, a resumed draft's thumbnail), Save draft
storing the image and swapping the field back with it, a save with no file keeping it, Remove taking
it off and out of storage, a replacement, a file that is not an image or is too large refused with
a plain message and nothing saved or sent, the email preview (the chosen file in the page, the
saved one by its absolute URL, none after Remove, a refusal over a preview without it), Send a test
to me (stored and absolute, or refused with nothing sent), the push test unchanged, a guild send
showing the image on the guild page card and the Past Announcements page, and a send with no image
rendering as before. Uploads land in the throwaway MEDIA_ROOT from the root conftest.
"""

from __future__ import annotations

import io
import json

import pytest
from django.contrib.auth.models import User
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models.signals import post_save
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escapejs
from factory.django import mute_signals
from PIL import Image

from hub.forms import ANNOUNCEMENT_IMAGE_INVALID
from membership.models import AnnouncementDraft, GuildAnnouncement
from tests.membership.announcement_image_spec import _HTML_IDAT, _crafted, _stored_named, _truncated_idat
from tests.membership.factories import GuildFactory, GuildMembershipFactory, MemberFactory, MembershipPlanFactory

pytestmark = pytest.mark.django_db

_BODY = "Send us one page for the winter zine."


def _png(name: str = "zine-call.png", size: tuple[int, int] = (40, 20)) -> SimpleUploadedFile:
    buffer = io.BytesIO()
    Image.new("RGB", size, "teal").save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


def _text_file() -> SimpleUploadedFile:
    return SimpleUploadedFile("notes.png", b"not an image at all", content_type="image/png")


def _login_lead(client: Client, guild) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username="lead", email="lead@x.com", password="p")
    guild.guild_lead = user.member
    guild.save(update_fields=["guild_lead"])
    client.login(username="lead", password="p")
    return user


def _guild_member(guild) -> User:
    member = MemberFactory()
    with mute_signals(post_save):
        user = User.objects.create_user(
            username="reader", email="reader@x.com", password="p", last_login=timezone.now()
        )
    member.user = user
    member.save(update_fields=["user"])
    GuildMembershipFactory(guild=guild, member=member)
    return user


def _zine_guild():
    return GuildFactory(name="Zine Guild")


def _data(guild, **overrides) -> dict:
    data = {
        "audience": f"guild:{guild.pk}",
        "subject": "Call for zine pages",
        "body": f"<p>{_BODY}</p>",
        "send_email": "on",
        "push_enabled": "on",
        "discord_channel": "none",
        "mention": "none",
        "draft_pk": "",
    }
    data.update(overrides)
    return data


def _toast(response) -> dict:
    return json.loads(response["HX-Trigger"])["showToast"]


def _saved_draft(client: Client, guild) -> AnnouncementDraft:
    client.post(reverse("hub_compose_save_draft"), _data(guild, image=_png()))
    return AnnouncementDraft.objects.get(guild=guild)


def _html(message) -> str:
    return message.alternatives[0][0]


def describe_the_compose_step():
    def it_offers_the_image_field_under_the_message_on_a_multipart_form(client: Client):
        guild = _zine_guild()
        _login_lead(client, guild)
        content = client.get(reverse("hub_compose")).content.decode()
        assert 'enctype="multipart/form-data" hx-encoding="multipart/form-data"' in content
        assert 'id="compose-image"' in content
        assert "Image (optional)" in content
        assert 'type="file" name="image" id="id_image" accept="image/*"' in content
        assert content.index('name="body"') < content.index('name="image"')
        # A fresh compose has nothing chosen yet.
        assert "preview: ''," in content

    def it_shows_a_resumed_drafts_saved_image(client: Client):
        guild = _zine_guild()
        _login_lead(client, guild)
        draft = _saved_draft(client, guild)
        content = client.get(reverse("hub_compose_resume", args=[draft.pk])).content.decode()
        assert f"preview: '{escapejs(draft.image.url)}'," in content


def describe_save_draft():
    def it_stores_the_image_and_swaps_the_field_back_with_it(client: Client):
        guild = _zine_guild()
        _login_lead(client, guild)
        response = client.post(reverse("hub_compose_save_draft"), _data(guild, image=_png()))
        assert response.status_code == 200
        draft = AnnouncementDraft.objects.get(guild=guild)
        assert draft.image.name.startswith("announcements/")
        assert default_storage.exists(draft.image.name)
        content = response.content.decode()
        assert 'id="compose-image"' in content and 'hx-swap-oob="true"' in content
        assert f"preview: '{escapejs(draft.image.url)}'," in content

    def it_keeps_the_saved_image_when_no_new_file_is_chosen(client: Client):
        guild = _zine_guild()
        _login_lead(client, guild)
        draft = _saved_draft(client, guild)
        name = draft.image.name
        client.post(reverse("hub_compose_save_draft"), _data(guild, draft_pk=str(draft.pk), body="<p>Edited.</p>"))
        draft.refresh_from_db()
        assert draft.body == "<p>Edited.</p>"
        assert draft.image.name == name

    def it_takes_the_image_off_and_out_of_storage_on_remove(client: Client):
        guild = _zine_guild()
        _login_lead(client, guild)
        draft = _saved_draft(client, guild)
        name = draft.image.name
        client.post(reverse("hub_compose_save_draft"), _data(guild, draft_pk=str(draft.pk), remove_image="1"))
        draft.refresh_from_db()
        assert not draft.image
        assert not default_storage.exists(name)

    def it_replaces_the_saved_image_with_a_new_one(client: Client):
        guild = _zine_guild()
        _login_lead(client, guild)
        draft = _saved_draft(client, guild)
        first = draft.image.name
        client.post(reverse("hub_compose_save_draft"), _data(guild, draft_pk=str(draft.pk), image=_png("second.png")))
        draft.refresh_from_db()
        assert draft.image.name != first
        assert not default_storage.exists(first)

    def it_refuses_a_file_that_is_not_an_image_and_saves_nothing(client: Client):
        guild = _zine_guild()
        _login_lead(client, guild)
        response = client.post(reverse("hub_compose_save_draft"), _data(guild, image=_text_file()))
        assert response.status_code == 204
        assert (_toast(response)["message"], _toast(response)["type"]) == (ANNOUNCEMENT_IMAGE_INVALID, "error")
        assert not AnnouncementDraft.objects.exists()

    def it_refuses_an_image_over_the_size_limit(client: Client, settings):
        settings.MAX_UPLOAD_IMAGE_BYTES = 100
        guild = _zine_guild()
        _login_lead(client, guild)
        response = client.post(reverse("hub_compose_save_draft"), _data(guild, image=_png(size=(400, 400))))
        assert response.status_code == 204
        assert _toast(response)["message"].startswith("Image must be 0.0 MB or smaller")
        assert not AnnouncementDraft.objects.exists()


def describe_sending():
    def it_refuses_a_file_that_is_not_an_image_with_a_plain_message_and_sends_nothing(client: Client, mailoutbox):
        guild = _zine_guild()
        _login_lead(client, guild)
        _guild_member(guild)
        response = client.post(reverse("hub_compose_send"), _data(guild, image=_text_file()))
        assert response.status_code == 200
        assert f"data-compose-image-error>{ANNOUNCEMENT_IMAGE_INVALID}</p>" in response.content.decode()
        assert not AnnouncementDraft.objects.exists()
        assert not GuildAnnouncement.objects.exists()
        assert mailoutbox == []

    def it_shows_no_saved_image_on_a_re_render_after_remove(client: Client):
        guild = _zine_guild()
        _login_lead(client, guild)
        draft = _saved_draft(client, guild)
        response = client.post(
            reverse("hub_compose_send"), _data(guild, draft_pk=str(draft.pk), remove_image="1", body="")
        )
        assert response.status_code == 200
        assert "preview: ''," in response.content.decode()

    def it_shows_the_saved_image_on_a_re_render(client: Client):
        guild = _zine_guild()
        _login_lead(client, guild)
        draft = _saved_draft(client, guild)
        response = client.post(reverse("hub_compose_send"), _data(guild, draft_pk=str(draft.pk), body=""))
        assert f"preview: '{escapejs(draft.image.url)}'," in response.content.decode()

    def it_emails_the_image_and_shows_it_on_the_guild_card_and_past_announcements(client: Client, mailoutbox):
        guild = _zine_guild()
        _login_lead(client, guild)
        reader = _guild_member(guild)
        response = client.post(reverse("hub_compose_send"), _data(guild, image=_png()))
        assert response.status_code == 302
        draft = AnnouncementDraft.objects.get(guild=guild)
        post = GuildAnnouncement.objects.get(guild=guild)
        assert post.image.name == draft.image.name
        [email] = [message for message in mailoutbox if reader.email in message.to]
        assert f'src="{draft.image_url}"' in _html(email)
        assert 'alt="Call for zine pages"' in _html(email)
        card = f'<img class="pl-guild-announcement__image" src="{post.image.url}" alt="Call for zine pages"'
        assert card in client.get(reverse("hub_guild_detail", args=[guild.slug])).content.decode()
        assert card in client.get(reverse("hub_guild_announcements", args=[guild.slug])).content.decode()

    def it_sends_and_shows_as_before_with_no_image(client: Client, mailoutbox):
        guild = _zine_guild()
        _login_lead(client, guild)
        reader = _guild_member(guild)
        client.post(reverse("hub_compose_send"), _data(guild))
        post = GuildAnnouncement.objects.get(guild=guild)
        assert not post.image
        [email] = [message for message in mailoutbox if reader.email in message.to]
        assert "data-announcement-image" not in _html(email)
        page = client.get(reverse("hub_guild_detail", args=[guild.slug])).content.decode()
        assert "pl-guild-announcement__image" not in page
        assert _BODY in page


def describe_the_preview():
    def it_shows_a_just_chosen_file_in_the_page_without_storing_it(client: Client):
        guild = _zine_guild()
        _login_lead(client, guild)
        content = client.post(reverse("hub_compose_preview"), _data(guild, image=_png())).content.decode()
        assert "data:image/jpeg;base64," in content
        assert "data-announcement-image" in content
        assert "data-discord-preview-image" in content
        assert not AnnouncementDraft.objects.exists()

    def it_shows_the_saved_image_by_its_absolute_url(client: Client):
        guild = _zine_guild()
        _login_lead(client, guild)
        draft = _saved_draft(client, guild)
        content = client.post(reverse("hub_compose_preview"), _data(guild, draft_pk=str(draft.pk))).content.decode()
        assert draft.image_url in content
        assert f'src="{draft.image_url}"' in content  # the Discord card, outside the escaped iframe

    def it_shows_no_image_after_remove(client: Client):
        guild = _zine_guild()
        _login_lead(client, guild)
        draft = _saved_draft(client, guild)
        data = _data(guild, draft_pk=str(draft.pk), remove_image="1")
        content = client.post(reverse("hub_compose_preview"), data).content.decode()
        assert "data-announcement-image" not in content
        assert "data-discord-preview-image" not in content

    def it_shows_nothing_extra_with_no_image(client: Client):
        guild = _zine_guild()
        _login_lead(client, guild)
        content = client.post(reverse("hub_compose_preview"), _data(guild)).content.decode()
        assert "data-announcement-image" not in content
        assert "data-compose-preview-image-error" not in content

    def it_names_a_refused_file_over_a_preview_without_it(client: Client):
        guild = _zine_guild()
        _login_lead(client, guild)
        content = client.post(reverse("hub_compose_preview"), _data(guild, image=_text_file())).content.decode()
        assert (
            f"data-compose-preview-image-error>{ANNOUNCEMENT_IMAGE_INVALID} The preview leaves it out.</p>" in content
        )
        assert "data-announcement-image" not in content


def describe_send_a_test_to_me():
    def it_saves_a_just_chosen_file_onto_the_draft_and_mails_it_by_absolute_url(client: Client, mailoutbox):
        guild = _zine_guild()
        lead = _login_lead(client, guild)
        response = client.post(reverse("hub_compose_test"), _data(guild, image=_png()))
        draft = AnnouncementDraft.objects.get(guild=guild)
        assert draft.image.name.startswith("announcements/")
        [email] = [message for message in mailoutbox if lead.email in message.to]
        html = _html(email)
        assert f'src="{draft.image_url}"' in html
        assert "data:image" not in html
        # The field swaps back holding the stored image, and the toast says the draft was saved.
        content = response.content.decode()
        assert 'id="compose-draft-pk"' in content and 'id="compose-image"' in content
        assert response["HX-Replace-Url"] == reverse("hub_compose_resume", args=[draft.pk])
        assert _toast(response)["message"] == f"Test sent to {lead.email}. Your draft is saved with the image."

    def it_leaves_one_stored_file_after_two_presses(client: Client, mailoutbox):
        guild = _zine_guild()
        _login_lead(client, guild)
        client.post(reverse("hub_compose_test"), _data(guild, image=_png("two-presses.png")))
        draft = AnnouncementDraft.objects.get(guild=guild)
        first = draft.image.name
        # The browser's input is empty after the swap, so the second press reuses the stored file.
        client.post(reverse("hub_compose_test"), _data(guild, draft_pk=str(draft.pk)))
        draft.refresh_from_db()
        assert draft.image.name == first
        # Even the same file pressed again replaces rather than adds.
        client.post(reverse("hub_compose_test"), _data(guild, draft_pk=str(draft.pk), image=_png("two-presses.png")))
        draft.refresh_from_db()
        assert _stored_named("two-presses") == [draft.image.name]
        assert len(mailoutbox) == 3

    def it_sends_nothing_when_the_draft_cannot_be_saved(client: Client, mailoutbox):
        guild = _zine_guild()
        _login_lead(client, guild)
        response = client.post(reverse("hub_compose_test"), _data(guild, image=_png(), subject="x" * 201))
        assert response.status_code == 204
        assert _toast(response)["type"] == "error"
        assert mailoutbox == []
        assert not AnnouncementDraft.objects.exists()

    def it_mails_the_saved_image(client: Client, mailoutbox):
        guild = _zine_guild()
        lead = _login_lead(client, guild)
        draft = _saved_draft(client, guild)
        client.post(reverse("hub_compose_test"), _data(guild, draft_pk=str(draft.pk)))
        [email] = [message for message in mailoutbox if lead.email in message.to]
        assert f'src="{draft.image_url}"' in _html(email)

    def it_refuses_a_file_that_is_not_an_image_and_sends_nothing(client: Client, mailoutbox):
        guild = _zine_guild()
        _login_lead(client, guild)
        response = client.post(reverse("hub_compose_test"), _data(guild, image=_text_file()))
        assert response.status_code == 204
        assert (_toast(response)["message"], _toast(response)["type"]) == (ANNOUNCEMENT_IMAGE_INVALID, "error")
        assert mailoutbox == []

    def it_leaves_the_push_test_text_only(client: Client, monkeypatch):
        from core import push_admin

        guild = _zine_guild()
        _login_lead(client, guild)
        seen: dict = {}

        def fake_send(user, **kwargs):
            seen.update(kwargs)
            return push_admin.TestSendResult(delivered=1, attempted=1)

        monkeypatch.setattr(push_admin, "send_test_push", fake_send)
        client.post(reverse("hub_compose_push_test"), _data(guild, image=_png()))
        assert seen["body"] == _BODY


@pytest.mark.parametrize("idat", [_HTML_IDAT, _truncated_idat()], ids=["html-in-idat", "truncated-zlib"])
def describe_a_png_that_passes_verify_but_will_not_decode():
    def it_is_refused_by_save_draft_and_nothing_is_stored(client: Client, idat):
        guild = _zine_guild()
        _login_lead(client, guild)
        response = client.post(reverse("hub_compose_save_draft"), _data(guild, image=_crafted(idat, "evil.png")))
        assert response.status_code == 204
        assert _toast(response)["message"] == ANNOUNCEMENT_IMAGE_INVALID
        assert not AnnouncementDraft.objects.exists()
        assert _stored_named("evil") == []

    def it_is_refused_by_the_preview_with_the_plain_message(client: Client, idat):
        guild = _zine_guild()
        _login_lead(client, guild)
        response = client.post(reverse("hub_compose_preview"), _data(guild, image=_crafted(idat)))
        assert response.status_code == 200
        assert f"data-compose-preview-image-error>{ANNOUNCEMENT_IMAGE_INVALID}" in response.content.decode()

    def it_is_refused_by_send_a_test_and_nothing_is_sent(client: Client, idat, mailoutbox):
        guild = _zine_guild()
        _login_lead(client, guild)
        response = client.post(reverse("hub_compose_test"), _data(guild, image=_crafted(idat, "evil.png")))
        assert response.status_code == 204
        assert _toast(response)["message"] == ANNOUNCEMENT_IMAGE_INVALID
        assert mailoutbox == []
        assert _stored_named("evil") == []


def describe_a_file_named_as_something_other_than_an_image():
    def it_is_refused_even_when_its_bytes_are_a_real_image(client: Client):
        guild = _zine_guild()
        _login_lead(client, guild)
        response = client.post(reverse("hub_compose_save_draft"), _data(guild, image=_png("page.html")))
        assert response.status_code == 204
        assert _toast(response)["message"] == ANNOUNCEMENT_IMAGE_INVALID
        assert not AnnouncementDraft.objects.exists()


def describe_another_guilds_draft_pk():
    def it_carries_no_image_into_the_preview_or_the_test(client: Client, mailoutbox):
        other = GuildFactory(name="Glass Guild")
        foreign = AnnouncementDraft.objects.create(
            audience=AnnouncementDraft.Audience.GUILD, guild=other, title="Glass news", body="<p>x</p>"
        )
        foreign.image = _png("glass-secret.png")
        foreign.save()
        guild = _zine_guild()
        lead = _login_lead(client, guild)
        data = _data(guild, draft_pk=str(foreign.pk))
        preview = client.post(reverse("hub_compose_preview"), data).content.decode()
        assert "data-announcement-image" not in preview
        assert "glass-secret" not in preview
        client.post(reverse("hub_compose_test"), data)
        [email] = [message for message in mailoutbox if lead.email in message.to]
        assert "data-announcement-image" not in _html(email)


def describe_the_push_test_button():
    def it_leaves_the_image_out_of_its_post(client: Client):
        guild = _zine_guild()
        _login_lead(client, guild)
        content = client.get(reverse("hub_compose")).content.decode()
        url = reverse("hub_compose_push_test")
        assert f'hx-post="{url}" hx-include="closest form" hx-params="not image" hx-swap="none"' in content


def describe_a_send_re_rendered_for_another_field():
    def it_asks_for_the_dropped_file_again(client: Client, mailoutbox):
        guild = _zine_guild()
        _login_lead(client, guild)
        response = client.post(reverse("hub_compose_send"), _data(guild, image=_png(), body=""))
        assert response.status_code == 200
        content = response.content.decode()
        assert "data-compose-image-lost>Choose your image again.</p>" in content
        assert mailoutbox == []

    def it_shows_the_refusal_rather_than_asking_again_when_the_file_was_the_problem(client: Client):
        guild = _zine_guild()
        _login_lead(client, guild)
        response = client.post(reverse("hub_compose_send"), _data(guild, image=_text_file(), body=""))
        content = response.content.decode()
        assert "data-compose-image-lost" not in content
        assert f"data-compose-image-error>{ANNOUNCEMENT_IMAGE_INVALID}</p>" in content

    def it_says_nothing_when_no_file_was_chosen(client: Client):
        guild = _zine_guild()
        _login_lead(client, guild)
        response = client.post(reverse("hub_compose_send"), _data(guild, body=""))
        assert "data-compose-image-lost" not in response.content.decode()
