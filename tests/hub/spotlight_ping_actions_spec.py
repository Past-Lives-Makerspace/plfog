"""Edit, Approve and Discard on Spotlight's Waiting for Review rows (#943, part 2).

Approving puts the line live on Home for 7 days and a published announcement on each pinged guild's page, and sends
nothing: no email, push, bell or Discord post. Discarding drops it for good. Only admins reach any of it.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.core import mail
from django.http import HttpResponse
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core.factories import QueuedDiscordMessageFactory
from core.models import QUEUED_MESSAGE_HANDLED, Announcement, QueuedDiscordMessage
from hub.spotlight_views import PING_APPROVED, PING_DISCARDED, PING_SAVED
from membership.models import GuildAnnouncement
from tests.membership.factories import GuildFactory, MemberFactory

pytestmark = pytest.mark.django_db

LINK = "https://discord.com/channels/1/111/900"


@pytest.fixture
def admin(client: Client) -> User:
    user = User.objects.create_superuser(username="pingapprover", password="pass", email="pingapprover@example.com")
    client.login(username="pingapprover", password="pass")
    return user


@pytest.fixture
def member_client(client: Client) -> Client:
    User.objects.create_user(username="pingnobody", password="pass", email="pingnobody@example.com")
    client.login(username="pingnobody", password="pass")
    return client


def _messages(response: HttpResponse) -> list[str]:
    return [str(message) for message in get_messages(response.wsgi_request)]


def _guild_ping() -> QueuedDiscordMessage:
    ping = QueuedDiscordMessageFactory(
        discord_url=LINK,
        original_text="Glaze day is Saturday\nBring your own brushes",
        line="<p>Ceramics: Glaze day is Saturday Bring your own brushes (Jay)</p>",
        sender=MemberFactory(preferred_name="Jay Quorble"),
    )
    ping.guilds.set([GuildFactory(name="Zorb Ceramics Guild"), GuildFactory(name="Zorb Jewelry Guild")])
    return ping


def describe_approve():
    def it_puts_the_line_live_for_seven_days_and_on_each_guild_page(client: Client, admin: User):
        ping = _guild_ping()

        response = client.post(reverse("hub_admin_ping_approve", args=[ping.pk]))

        assert response.status_code == 302
        assert _messages(response) == [PING_APPROVED]
        announcement = Announcement.objects.get()
        assert announcement.body == ping.line
        assert announcement.created_by == admin
        assert announcement.status(timezone.now()) == Announcement.Status.LIVE
        assert announcement.show_until - announcement.show_from == timedelta(days=7)
        assert list(Announcement.objects.live(timezone.now())) == [announcement]
        posts = list(GuildAnnouncement.objects.order_by("guild__name"))
        assert [post.guild.name for post in posts] == ["Zorb Ceramics Guild", "Zorb Jewelry Guild"]
        for post in posts:
            assert post.title == "Glaze day is Saturday"
            assert post.body == f"Bring your own brushes\n\nPosted in Discord by Jay Quorble: {LINK}"
            assert post.author == ping.sender.user
            assert post.send_email is False
            assert post.discord_channel == GuildAnnouncement.DiscordChannel.NONE
            assert post.moderation_state == GuildAnnouncement.ModerationState.PUBLISHED
            assert post.expires_at is None
            assert post.reviewed_by == admin
        ping.refresh_from_db()
        assert ping.state == QueuedDiscordMessage.State.APPROVED
        assert ping.announcement == announcement
        assert set(ping.guild_announcements.all()) == set(posts)
        assert mail.outbox == []

    def it_rings_no_bell_and_queues_no_push(client: Client, admin: User):
        from core.models import EventDelivery, Notification

        ping = _guild_ping()

        client.post(reverse("hub_admin_ping_approve", args=[ping.pk]))

        assert Notification.objects.count() == 0
        assert EventDelivery.objects.count() == 0

    def it_puts_an_everyone_ping_on_home_alone(client: Client, admin: User):
        ping = QueuedDiscordMessageFactory()

        client.post(reverse("hub_admin_ping_approve", args=[ping.pk]))

        assert Announcement.objects.count() == 1
        assert GuildAnnouncement.objects.count() == 0

    def it_credits_an_unlinked_sender_by_discord_name_and_has_no_author(client: Client, admin: User):
        ping = QueuedDiscordMessageFactory(original_text="Kiln open")
        ping.guilds.set([GuildFactory(name="Zorb Glass Guild")])

        client.post(reverse("hub_admin_ping_approve", args=[ping.pk]))

        post = GuildAnnouncement.objects.get()
        assert post.author is None
        assert post.body.endswith(f"Posted in Discord by Discord Dee: {ping.discord_url}")

    def it_approves_only_once(client: Client, admin: User):
        ping = _guild_ping()

        client.post(reverse("hub_admin_ping_approve", args=[ping.pk]))
        second = client.post(reverse("hub_admin_ping_approve", args=[ping.pk]))

        assert _messages(second)[-1] == "That Discord message was already approved or discarded."
        assert Announcement.objects.count() == 1
        assert GuildAnnouncement.objects.count() == 2

    def it_refuses_a_discarded_message(client: Client, admin: User):
        ping = QueuedDiscordMessageFactory(state=QueuedDiscordMessage.State.DISCARDED)

        client.post(reverse("hub_admin_ping_approve", args=[ping.pk]))

        assert Announcement.objects.count() == 0

    def it_answers_404_for_no_such_message(client: Client, admin: User):
        assert client.post(reverse("hub_admin_ping_approve", args=[9999])).status_code == 404


def describe_discard():
    def it_drops_it_from_the_queue_for_good(client: Client, admin: User):
        ping = QueuedDiscordMessageFactory(original_text="Zorbleflux discard me")

        response = client.post(reverse("hub_admin_ping_discard", args=[ping.pk]))

        assert _messages(response) == [PING_DISCARDED]
        ping.refresh_from_db()
        assert ping.state == QueuedDiscordMessage.State.DISCARDED
        assert "Zorbleflux discard me" not in client.get(reverse("hub_admin_spotlight")).content.decode()
        assert Announcement.objects.count() == 0

    def it_refuses_one_already_handled(client: Client, admin: User):
        ping = QueuedDiscordMessageFactory(state=QueuedDiscordMessage.State.APPROVED)

        response = client.post(reverse("hub_admin_ping_discard", args=[ping.pk]))

        assert _messages(response) == ["That Discord message was already approved or discarded."]
        ping.refresh_from_db()
        assert ping.state == QueuedDiscordMessage.State.APPROVED

    def it_answers_404_for_no_such_message(client: Client, admin: User):
        assert client.post(reverse("hub_admin_ping_discard", args=[9999])).status_code == 404

    def it_puts_a_confirm_modal_behind_each_rows_discard(client: Client, admin: User):
        ping = QueuedDiscordMessageFactory()

        html = client.get(reverse("hub_admin_spotlight")).content.decode()

        assert f"open-confirm', 'discard-ping-{ping.pk}'" in html
        assert reverse("hub_admin_ping_discard", args=[ping.pk]) in html
        assert f'action="{reverse("hub_admin_ping_approve", args=[ping.pk])}"' in html
        assert f'href="{reverse("hub_admin_ping_edit", args=[ping.pk])}"' in html


def describe_edit():
    def it_opens_the_spotlight_editor_on_the_line(client: Client, admin: User):
        ping = QueuedDiscordMessageFactory(line="<p>Zorblax drafted line (Dee)</p>")

        html = client.get(reverse("hub_admin_ping_edit", args=[ping.pk])).content.decode()

        assert "Edit Waiting Message" in html
        assert "Zorblax drafted line (Dee)" in html
        assert "data-announcement-chars" in html
        assert 'name="show_from_date"' not in html

    def it_saves_the_line_and_keeps_it_waiting(client: Client, admin: User):
        ping = QueuedDiscordMessageFactory()

        response = client.post(
            reverse("hub_admin_ping_edit", args=[ping.pk]), {"body": "<p><strong>Tidied</strong> line</p>"}
        )

        assert response.status_code == 302
        assert _messages(response) == [PING_SAVED]
        ping.refresh_from_db()
        assert ping.line == "<p><strong>Tidied</strong> line</p>"
        assert ping.state == QueuedDiscordMessage.State.WAITING
        assert Announcement.objects.count() == 0

    def it_refuses_a_line_over_160_characters(client: Client, admin: User):
        ping = QueuedDiscordMessageFactory()
        before = ping.line

        response = client.post(reverse("hub_admin_ping_edit", args=[ping.pk]), {"body": f"<p>{'x' * 161}</p>"})

        assert response.status_code == 200
        assert "161" in response.content.decode()
        ping.refresh_from_db()
        assert ping.line == before

    def it_answers_404_for_a_message_no_longer_waiting(client: Client, admin: User):
        ping = QueuedDiscordMessageFactory(state=QueuedDiscordMessage.State.DISCARDED)

        assert client.get(reverse("hub_admin_ping_edit", args=[ping.pk])).status_code == 404

    def it_refuses_a_save_when_another_admin_handled_it_meanwhile(client: Client, admin: User):
        ping = QueuedDiscordMessageFactory()
        assert client.get(reverse("hub_admin_ping_edit", args=[ping.pk])).status_code == 200
        QueuedDiscordMessage.discard(ping.pk)

        response = client.post(reverse("hub_admin_ping_edit", args=[ping.pk]), {"body": "<p>New</p>"})

        assert _messages(response) == [QUEUED_MESSAGE_HANDLED]
        ping.refresh_from_db()
        assert ping.line != "<p>New</p>"


def describe_permissions():
    @pytest.mark.parametrize("name", ["hub_admin_ping_edit", "hub_admin_ping_approve", "hub_admin_ping_discard"])
    def it_refuses_everyone_but_admins(member_client: Client, name: str):
        ping = QueuedDiscordMessageFactory()

        assert member_client.post(reverse(name, args=[ping.pk])).status_code == 403
        ping.refresh_from_db()
        assert ping.state == QueuedDiscordMessage.State.WAITING


def describe_guild_body():
    def it_leaves_out_a_first_line_the_headline_already_shows():
        ping = QueuedDiscordMessageFactory.build(original_text="Glaze day\nBring brushes\nAnd aprons")

        assert ping.guild_body.startswith("Bring brushes\nAnd aprons\n\nPosted in Discord by")

    def it_keeps_a_one_line_message_whole():
        ping = QueuedDiscordMessageFactory.build(original_text="Kiln open tonight")

        assert ping.guild_body.startswith("Kiln open tonight\n\nPosted in Discord by Discord Dee: ")

    def it_keeps_the_whole_text_when_the_headline_was_cut():
        long_line = "word " * 30
        ping = QueuedDiscordMessageFactory.build(original_text=f"{long_line}\nsecond")

        assert ping.guild_body.startswith(f"{long_line}\nsecond\n\n")


def describe_guild_title():
    def it_cuts_a_long_first_line_at_a_word():
        ping = QueuedDiscordMessageFactory.build(original_text=f"{'word ' * 30}\nsecond")

        assert ping.guild_title.endswith("word...")
        assert len(ping.guild_title) <= 80

    def it_cuts_one_long_word_where_it_must():
        ping = QueuedDiscordMessageFactory.build(original_text="z" * 200)

        assert ping.guild_title == f"{'z' * 77}..."


def describe_the_guild_page_link():
    def it_makes_the_discord_link_clickable(client: Client, admin: User):
        ping = QueuedDiscordMessageFactory(original_text="Zorb kiln news")
        guild = GuildFactory(name="Zorb Glass Guild")
        ping.guilds.set([guild])
        client.post(reverse("hub_admin_ping_approve", args=[ping.pk]))

        html = client.get(reverse("hub_guild_detail", args=[guild.slug])).content.decode()

        assert f'<a href="{ping.discord_url}"' in html
