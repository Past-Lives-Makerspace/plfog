"""Announcements keep their paragraph breaks off email too (issue #728).

The composer stores Quill HTML. Every channel that cannot carry HTML (the Discord embed and its
preview card, the bell notification, the guild page post, the email's text part) gets the body
as lines: a blank line between paragraphs and one ``- `` line per bullet, never one long line.
"""

from __future__ import annotations

from pathlib import Path
from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from factory.django import mute_signals

from core.events.discord import build_embed_payload
from core.models import Notification, SiteConfiguration
from membership.models import AnnouncementDraft, GuildAnnouncement
from tests.membership.factories import GuildFactory, GuildMembershipFactory, MemberFactory, MembershipPlanFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def post_embed() -> Iterator[MagicMock]:
    """Every Discord post goes to this mock, never the network."""
    with patch("core.events.discord.post_embed", return_value=True) as mock_post:
        yield mock_post


_THREE_PARAGRAPHS = "<p>First the news.</p><p>Then the details.</p><p>Last, the ask.</p>"
_THREE_AS_LINES = "First the news.\n\nThen the details.\n\nLast, the ask."
_BULLETS = '<p>Bring:</p><ol><li data-list="bullet">gloves</li><li data-list="bullet">an apron</li></ol><p>Thanks</p>'
_BULLETS_AS_LINES = "Bring:\n\n- gloves\n- an apron\n\nThanks"


def _author() -> User:
    MembershipPlanFactory()
    return User.objects.create_user(username="author", email="author@x.com", password="pw")


def _member_user(username: str, *, guild=None) -> User:
    """An active member's signed-in user: a real announcement recipient."""
    member = MemberFactory()
    with mute_signals(post_save):
        user = User.objects.create_user(
            username=username, email=f"{username}@x.com", password="pw12345!", last_login=timezone.now()
        )
    member.user = user
    member.save(update_fields=["user"])
    if guild is not None:
        GuildMembershipFactory(guild=guild, member=member)
    return user


def _site_draft(body: str, **fields: object) -> AnnouncementDraft:
    config = SiteConfiguration.load()
    config.discord_general_webhook_url = "https://d/gen"
    config.save()
    return AnnouncementDraft.objects.create(
        author=_author(),
        audience=AnnouncementDraft.Audience.SITE,
        title="News",
        body=body,
        discord_channel=GuildAnnouncement.DiscordChannel.GENERAL,
        **fields,
    )


def _posted_description(draft: AnnouncementDraft, post_embed: MagicMock) -> str:
    """The embed description the send posts to Discord."""
    draft.send()
    embeds = build_embed_payload(post_embed.call_args.args[1])["embeds"]
    return str(embeds[0]["description"])  # type: ignore[index]


def describe_an_announcement_with_paragraphs():
    def it_posts_to_discord_with_a_blank_line_between_paragraphs(post_embed: MagicMock):
        assert _posted_description(_site_draft(_THREE_PARAGRAPHS), post_embed) == _THREE_AS_LINES

    def it_posts_each_bullet_as_its_own_dash_line(post_embed: MagicMock):
        assert _posted_description(_site_draft(_BULLETS), post_embed) == _BULLETS_AS_LINES

    def it_shows_the_same_breaks_in_the_composers_discord_preview():
        from hub.views import _announcement_previews

        previews = _announcement_previews(_site_draft(_THREE_PARAGRAPHS))
        assert str(previews["discord_description_html"]) == _THREE_AS_LINES

    def it_writes_the_bell_notification_with_the_paragraphs():
        recipient = _member_user("recip")
        _site_draft(_THREE_PARAGRAPHS).send()
        assert Notification.objects.get(user=recipient, trigger="site_announcement").body == _THREE_AS_LINES

    def it_keeps_the_paragraphs_in_the_email_text_part_and_leaves_the_html_part_rich(mailoutbox):
        _member_user("recip")
        _site_draft(_THREE_PARAGRAPHS).send()
        email = mailoutbox[0]
        assert _THREE_AS_LINES in email.body
        html = email.alternatives[0][0]
        assert html.count(">First the news.</p>") == 1
        assert html.count(">Then the details.</p>") == 1

    def it_stores_the_guild_post_with_the_paragraphs_and_the_guild_page_shows_them():
        guild = GuildFactory()
        viewer = _member_user("viewer", guild=guild)
        AnnouncementDraft.objects.create(
            author=_author(),
            audience=AnnouncementDraft.Audience.GUILD,
            guild=guild,
            title="Forge night",
            body=_THREE_PARAGRAPHS,
            discord_channel=GuildAnnouncement.DiscordChannel.NONE,
        ).send()
        assert guild.announcements.published().get().body == _THREE_AS_LINES
        client = Client()
        client.force_login(viewer)
        content = client.get(reverse("hub_guild_detail", args=[guild.slug])).content.decode()
        assert "<p>First the news.</p><p>Then the details.</p><p>Last, the ask.</p>" in content

    def it_sends_the_paragraphs_in_the_roster_notice_for_a_class():
        from classes.factories import ClassOfferingFactory, RegistrationFactory
        from classes.models import Registration

        offering = ClassOfferingFactory()
        recipient = _member_user("student")
        RegistrationFactory(class_offering=offering, member=recipient.member, status=Registration.Status.CONFIRMED)
        AnnouncementDraft.objects.create(
            author=_author(),
            audience=AnnouncementDraft.Audience.CLASS,
            class_offering=offering,
            title="Moved",
            body=_THREE_PARAGRAPHS,
        ).send()
        assert Notification.objects.get(user=recipient, trigger="class_announcement").body == _THREE_AS_LINES

    def it_keeps_the_phone_push_to_one_line():
        draft = _site_draft(_THREE_PARAGRAPHS)
        assert draft.build_push_message("https://x/").body == "First the news. Then the details. Last, the ask."


def describe_the_channel_limits():
    def it_cuts_a_long_message_to_discords_4096_character_description(post_embed: MagicMock):
        paragraph = "<p>" + "word " * 200 + "</p>"
        description = _posted_description(_site_draft(paragraph * 6), post_embed)
        assert len(description) == 4096
        assert description.endswith("…")
        assert "\n\n" in description

    def it_leaves_a_message_under_the_discord_limit_whole():
        draft = _site_draft(_THREE_PARAGRAPHS)
        assert draft.build_discord_message("https://x/").body == _THREE_AS_LINES

    def it_cuts_the_bell_notification_to_500_characters():
        recipient = _member_user("recip")
        _site_draft("<p>" + "word " * 80 + "</p>" + "<p>" + "more " * 80 + "</p>").send()
        body = Notification.objects.get(user=recipient, trigger="site_announcement").body
        assert len(body) == 500
        assert "\n\n" in body


def describe_the_notifications_page():
    def it_renders_the_stored_line_breaks_inside_the_row_body():
        user = _member_user("reader")
        Notification.objects.create(user=user, trigger="site_announcement", title="News", body=_THREE_AS_LINES)
        client = Client()
        client.force_login(user)
        content = client.get(reverse("notification_list")).content.decode()
        assert f'<span class="pl-note__body">{_THREE_AS_LINES}</span>' in content

    def it_shows_the_line_breaks_through_a_white_space_rule():
        css = (Path(__file__).resolve().parents[2] / "static/css/hub.css").read_text()
        rule = next(line for line in css.splitlines() if line.startswith(".pl-note__body {"))
        assert "white-space: pre-line" in rule


# What the composer's Shift+Enter saves (#1348): a soft break inside the first paragraph, as Quill
# writes it, then a second paragraph from a plain Enter.
_SOFT_BREAK_FROM_EDITOR = '<p>Past Lives Makerspace<br class="pl-softbreak">2808 SE 9th Ave</p><p>See you there.</p>'
_SOFT_BREAK = "<p>Past Lives Makerspace<br>2808 SE 9th Ave</p><p>See you there.</p>"
_SOFT_BREAK_AS_LINES = "Past Lives Makerspace\n2808 SE 9th Ave\n\nSee you there."


def describe_an_announcement_with_a_soft_line_break():
    def it_saves_the_break_as_a_plain_br_inside_the_paragraph():
        from core.html_sanitize import clean_rich_html

        assert clean_rich_html(_SOFT_BREAK_FROM_EDITOR) == _SOFT_BREAK

    def it_posts_to_discord_with_the_two_lines_tight_and_a_blank_line_before_the_next_paragraph(post_embed: MagicMock):
        assert _posted_description(_site_draft(_SOFT_BREAK), post_embed) == _SOFT_BREAK_AS_LINES

    def it_shows_the_same_lines_in_the_composers_discord_preview():
        from hub.views import _announcement_previews

        previews = _announcement_previews(_site_draft(_SOFT_BREAK))
        assert str(previews["discord_description_html"]) == _SOFT_BREAK_AS_LINES

    def it_writes_the_bell_notification_with_the_two_lines_tight():
        recipient = _member_user("recip")
        _site_draft(_SOFT_BREAK).send()
        assert Notification.objects.get(user=recipient, trigger="site_announcement").body == _SOFT_BREAK_AS_LINES

    def it_keeps_the_br_inside_the_paragraph_in_the_email(mailoutbox):
        _member_user("recip")
        _site_draft(_SOFT_BREAK).send()
        email = mailoutbox[0]
        assert _SOFT_BREAK_AS_LINES in email.body
        assert ">Past Lives Makerspace<br>2808 SE 9th Ave</p>" in email.alternatives[0][0]

    def it_shows_the_guild_page_post_with_the_two_lines_in_one_paragraph():
        guild = GuildFactory()
        viewer = _member_user("viewer", guild=guild)
        AnnouncementDraft.objects.create(
            author=_author(),
            audience=AnnouncementDraft.Audience.GUILD,
            guild=guild,
            title="Forge night",
            body=_SOFT_BREAK,
            discord_channel=GuildAnnouncement.DiscordChannel.NONE,
        ).send()
        assert guild.announcements.published().get().body == _SOFT_BREAK_AS_LINES
        client = Client()
        client.force_login(viewer)
        content = client.get(reverse("hub_guild_detail", args=[guild.slug])).content.decode()
        assert "<p>Past Lives Makerspace<br>2808 SE 9th Ave</p><p>See you there.</p>" in content

    def it_joins_the_two_lines_with_a_space_in_the_phone_push():
        draft = _site_draft(_SOFT_BREAK)
        assert draft.build_push_message("https://x/").body == "Past Lives Makerspace 2808 SE 9th Ave See you there."
