"""Admin Tools > Spotlight's Waiting for Review section and the Admin Tools count (#943, part 1)."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from core.factories import QueuedDiscordMessageFactory
from core.models import QueuedDiscordMessage
from tests.membership.factories import GuildFactory, MemberFactory

pytestmark = pytest.mark.django_db

PAGE = reverse("hub_admin_spotlight")
TOOLS = reverse("hub_admin_tools")


@pytest.fixture
def admin_client(client: Client) -> Client:
    User.objects.create_superuser(username="pingadmin", password="pass", email="pingadmin@example.com")
    client.login(username="pingadmin", password="pass")
    return client


@pytest.fixture
def member_client(client: Client) -> Client:
    User.objects.create_user(username="pingmember", password="pass", email="pingmember@example.com")
    client.login(username="pingmember", password="pass")
    return client


def _queue(html: str) -> str:
    start = html.index("data-ping-queue")
    return html[start : html.index("</section>", start)]


def describe_the_queue_section():
    def it_is_absent_when_nothing_waits(admin_client: Client):
        QueuedDiscordMessageFactory(state=QueuedDiscordMessage.State.DISCARDED)

        assert "data-ping-queue" not in admin_client.get(PAGE).content.decode()

    def it_shows_each_waiting_row_with_its_line_text_guild_sender_and_link(admin_client: Client):
        guild = GuildFactory(name="Zorblax Ceramics Guild")
        ping = QueuedDiscordMessageFactory(
            discord_message_id="777",
            original_text="Wash your space\nbefore you leave",
            line='<p>Zorblax Ceramics: Wash <a href="https://discord.com/channels/1/111/777">[...]</a> (Jay)</p>',
            sender=MemberFactory(preferred_name="Jay Quorble"),
        )
        ping.guilds.set([guild])
        QueuedDiscordMessageFactory(original_text="Glorp everyone")

        queue = _queue(admin_client.get(PAGE).content.decode())

        assert '<span class="hub-badge">2</span>' in queue
        assert f'data-ping-row="{ping.pk}"' in queue
        assert "Zorblax Ceramics: Wash" in queue
        assert "Wash your space<br>before you leave" in queue
        assert "data-ping-guild>Zorblax Ceramics Guild</span>" in queue
        assert "data-ping-guild>@everyone</span>" in queue
        assert "data-ping-sender>Sent by Jay Quorble</span>" in queue
        assert "data-ping-sender>Sent by Discord Dee</span>" in queue
        assert 'href="https://discord.com/channels/1/111/777" class="pl-ping-queue__link"' in queue

    def it_leaves_out_approved_and_discarded_rows(admin_client: Client):
        QueuedDiscordMessageFactory(original_text="Glorp waiting")
        QueuedDiscordMessageFactory(original_text="Glorp approved", state=QueuedDiscordMessage.State.APPROVED)

        queue = _queue(admin_client.get(PAGE).content.decode())

        assert "Glorp waiting" in queue
        assert "Glorp approved" not in queue

    def it_refuses_everyone_but_admins(member_client: Client):
        QueuedDiscordMessageFactory()

        assert member_client.get(PAGE).status_code == 403


def describe_the_admin_tools_card():
    def it_shows_how_many_wait(admin_client: Client):
        QueuedDiscordMessageFactory.create_batch(2)
        QueuedDiscordMessageFactory(state=QueuedDiscordMessage.State.DISCARDED)

        html = admin_client.get(TOOLS).content.decode()

        assert '<span class="hub-badge" data-spotlight-waiting-count>2 waiting</span>' in html

    def it_shows_no_count_when_none_wait(admin_client: Client):
        assert "data-spotlight-waiting-count" not in admin_client.get(TOOLS).content.decode()
