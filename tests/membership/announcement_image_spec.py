"""An announcement's one image (#1064), at the model: stored, shrunk, sent, and absent as before.

Covers the draft's save (a fresh upload re-encoded as a JPEG under the long edge cap, the file a
replaced or removed image leaves taken out of storage, a bookkeeping save left alone), the image's
absolute URL, an unsaved preview or test draft's image, the branded email HTML (above the message,
width capped, the title as alt text, byte for byte unchanged with no image), the Discord message
and embed payload, and a guild send handing its stored file to the guild post. Uploads go to the
throwaway MEDIA_ROOT the root conftest sets, never object storage.
"""

from __future__ import annotations

import io

import pytest
from django.conf import settings
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from core.events.channels import Message
from core.events.discord import build_embed_payload
from membership.models import AnnouncementDraft, GuildAnnouncement, build_announcement_email_html
from tests.membership.factories import AnnouncementDraftFactory, GuildFactory, MembershipPlanFactory

pytestmark = pytest.mark.django_db


def _upload(name: str = "zine-call.png", size: tuple[int, int] = (40, 20), fmt: str = "PNG") -> SimpleUploadedFile:
    buffer = io.BytesIO()
    Image.new("RGB", size, "teal").save(buffer, format=fmt)
    return SimpleUploadedFile(name, buffer.getvalue(), content_type=f"image/{fmt.lower()}")


def _guild_draft(**fields) -> AnnouncementDraft:
    MembershipPlanFactory()
    guild = GuildFactory(name="Zine Guild")
    defaults = {
        "audience": AnnouncementDraft.Audience.GUILD,
        "guild": guild,
        "title": "Call for zine pages",
        "body": "<p>Send us a page.</p>",
        "discord_channel": "none",
    }
    defaults.update(fields)
    return AnnouncementDraftFactory(**defaults)


def describe_saving_a_draft_with_an_image():
    def it_stores_a_fresh_upload_as_a_jpeg_under_the_announcements_folder():
        draft = _guild_draft()
        draft.image = _upload()
        draft.save()
        draft.refresh_from_db()
        assert draft.image.name.startswith("announcements/")
        assert draft.image.name.endswith(".jpg")
        assert default_storage.exists(draft.image.name)

    def it_shrinks_an_upload_wider_than_the_cap(settings):
        settings.IMAGE_MAX_LONG_EDGE_GALLERY = 50
        draft = _guild_draft()
        draft.image = _upload(size=(200, 100))
        draft.save()
        with default_storage.open(draft.image.name) as stored:
            assert Image.open(stored).size == (50, 25)

    def it_removes_the_old_file_when_the_image_is_replaced():
        draft = _guild_draft()
        draft.image = _upload("first.png")
        draft.save()
        first = draft.image.name
        draft.image = _upload("second.png")
        draft.save()
        assert not default_storage.exists(first)
        assert default_storage.exists(draft.image.name)

    def it_removes_the_file_when_the_image_is_taken_off():
        draft = _guild_draft()
        draft.image = _upload()
        draft.save()
        name = draft.image.name
        draft.image = None
        draft.save()
        draft.refresh_from_db()
        assert not draft.image
        assert not default_storage.exists(name)

    def it_keeps_a_file_another_draft_still_names():
        draft = _guild_draft()
        draft.image = _upload()
        draft.save()
        twin = AnnouncementDraftFactory(audience=AnnouncementDraft.Audience.SITE, title="Twin", body="<p>x</p>")
        twin.image = draft.image.name
        twin.save()
        draft.image = None
        draft.save()
        assert default_storage.exists(twin.image.name)

    def it_leaves_the_image_alone_on_a_save_that_names_other_fields():
        draft = _guild_draft()
        draft.image = _upload()
        draft.save()
        name = draft.image.name
        draft.send_error = "boom"
        draft.save(update_fields=["send_error"])
        draft.refresh_from_db()
        assert draft.image.name == name
        assert default_storage.exists(name)


def describe_image_url():
    def it_is_blank_with_no_image():
        assert AnnouncementDraft(title="x").image_url == ""

    def it_makes_a_stored_path_absolute_on_the_members_host():
        draft = _guild_draft()
        draft.image = _upload()
        draft.save()
        base = settings.MEMBER_BASE_URL.rstrip("/")
        assert draft.image_url == f"{base}{draft.image.url}"
        assert draft.image_url.startswith(("http://", "https://"))

    def it_keeps_an_absolute_storage_url(monkeypatch):
        draft = _guild_draft()
        draft.image = _upload()
        draft.save()
        monkeypatch.setattr(draft.image.storage, "url", lambda name: f"https://media.example.com/{name}")
        assert draft.image_url == f"https://media.example.com/{draft.image.name}"

    def it_answers_with_the_preview_url_on_an_unsaved_preview_draft():
        draft = AnnouncementDraft(title="x")
        draft.preview_image_url = "data:image/jpeg;base64,AAAA"
        assert draft.image_url == "data:image/jpeg;base64,AAAA"


def describe_show_unsaved_image():
    def it_carries_a_preview_in_the_page_and_stores_nothing():
        draft = AnnouncementDraft(title="x")
        draft.show_unsaved_image(_upload(), store=False)
        assert draft.image_url.startswith("data:image/jpeg;base64,")
        assert not draft.image

    def it_stores_the_file_for_a_test_email_without_saving_the_draft():
        draft = AnnouncementDraft(title="x")
        draft.show_unsaved_image(_upload(), store=True)
        assert draft.pk is None
        assert draft.image.name.startswith("announcements/")
        assert default_storage.exists(draft.image.name)
        assert draft.image_url.startswith(settings.MEMBER_BASE_URL.rstrip("/"))


def describe_the_email_html():
    def it_shows_the_image_above_the_message_width_capped_with_the_title_as_alt():
        html = build_announcement_email_html(
            "Call for zine pages", "<p>Send us a page.</p>", image_url="https://media.example.com/a.jpg"
        )
        image = html.index("data-announcement-image")
        assert image < html.index("Send us a page.")
        assert 'src="https://media.example.com/a.jpg"' in html
        assert 'alt="Call for zine pages"' in html
        assert 'width="530"' in html
        assert "max-width:530px" in html

    def it_escapes_the_title_in_the_alt_text():
        html = build_announcement_email_html(
            'Pages "due" <Friday>', "<p>x</p>", image_url="https://m.example.com/a.jpg"
        )
        assert 'alt="Pages &quot;due&quot; &lt;Friday&gt;"' in html

    def it_is_unchanged_with_no_image():
        before = build_announcement_email_html("Call for zine pages", "<p>Send us a page.</p>", sender="Quinn")
        assert (
            build_announcement_email_html("Call for zine pages", "<p>Send us a page.</p>", sender="Quinn", image_url="")
            == before
        )
        assert "data-announcement-image" not in before
        assert "max-width:530px" not in before

    def it_carries_the_drafts_image_in_its_email_message():
        draft = _guild_draft()
        draft.image = _upload()
        draft.save()
        message = draft.build_email_message("https://members.example.com/")
        assert f'src="{draft.image_url}"' in message.html_body
        assert "data-announcement-image" in message.html_body

    def it_carries_the_image_for_a_class_audience_too():
        from classes.factories import ClassOfferingFactory

        offering = ClassOfferingFactory(title="Bookbinding")
        draft = AnnouncementDraftFactory(
            audience=AnnouncementDraft.Audience.CLASS, class_offering=offering, title="Class news", body="<p>Hi</p>"
        )
        draft.image = _upload()
        draft.save()
        assert "data-announcement-image" in draft.build_email_message("https://members.example.com/").html_body

    def it_leaves_a_draft_with_no_image_without_one():
        draft = _guild_draft()
        assert "data-announcement-image" not in draft.build_email_message("https://members.example.com/").html_body


def describe_the_discord_message():
    def it_carries_the_image_on_the_message_and_into_the_embed():
        draft = _guild_draft()
        draft.image = _upload()
        draft.save()
        message = draft.build_discord_message("https://members.example.com/")
        assert message.discord_image_url == draft.image_url
        embed = build_embed_payload(message)["embeds"][0]  # type: ignore[index]
        assert embed["image"] == {"url": draft.image_url}

    def it_carries_the_image_on_a_results_announcement_too():
        from tests.membership.factories import FundingSnapshotFactory

        snapshot = FundingSnapshotFactory(
            cycle_label="October 2026",
            results={"votes_cast": 1, "results": [{"guild_name": "Zine Guild", "funding": "5.00", "share_pct": 100.0}]},
        )
        draft = AnnouncementDraftFactory(
            audience=AnnouncementDraft.Audience.SITE, funding_snapshot=snapshot, title="R", body="<p>R</p>"
        )
        draft.image = _upload()
        draft.save()
        assert draft.build_discord_message("https://members.example.com/").discord_image_url == draft.image_url

    def it_posts_the_embed_it_always_did_with_no_image():
        draft = _guild_draft()
        message = draft.build_discord_message("https://members.example.com/")
        assert message.discord_image_url == ""
        assert "image" not in build_embed_payload(message)["embeds"][0]  # type: ignore[index, operator]


def describe_build_embed_payload_image():
    def it_adds_the_image_only_when_set():
        with_image = build_embed_payload(Message(title="T", body="B", discord_image_url="https://m.example.com/a.jpg"))
        assert with_image["embeds"][0]["image"] == {"url": "https://m.example.com/a.jpg"}  # type: ignore[index]
        assert build_embed_payload(Message(title="T", body="B")) == {
            "embeds": [{"title": "T", "description": "B", "color": with_image["embeds"][0]["color"]}]  # type: ignore[index]
        }


def describe_a_guild_send():
    def it_hands_the_stored_file_to_the_guild_post():
        draft = _guild_draft(push_enabled=False, send_email=False, discord_enabled=False)
        draft.image = _upload()
        draft.save()
        draft.send()
        post = GuildAnnouncement.objects.get(guild=draft.guild)
        assert post.image.name == draft.image.name
        assert default_storage.exists(post.image.name)

    def it_posts_with_no_image_when_the_draft_has_none():
        draft = _guild_draft(push_enabled=False, send_email=False, discord_enabled=False)
        draft.send()
        assert not GuildAnnouncement.objects.get(guild=draft.guild).image
