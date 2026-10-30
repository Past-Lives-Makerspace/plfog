"""The composer's Subject (#1039): an optional subject line that is the title everywhere once written.

Covers the Write step (the field above the message, its placeholder and hint naming the audience's
automatic title, no field on a results announcement), the count refresh re-rendering it for a new
audience while keeping what was typed, saving and resuming a draft with it, a guild send arriving
with it as the email subject, push title, bell title, Discord embed title, Sent row and guild page
headline (with the Urgent lead when marked), blank keeping today's automatic title, and the email,
Discord and push previews showing it.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from factory.django import mute_signals

from core.models import Notification
from membership.models import AnnouncementDraft, GuildAnnouncement
from tests.membership.factories import (
    AnnouncementDraftFactory,
    FundingSnapshotFactory,
    GuildFactory,
    GuildMembershipFactory,
    MemberFactory,
    MembershipPlanFactory,
)

pytestmark = pytest.mark.django_db

_SUBJECT = "Reading night moved to Thursday"
_WEBHOOK = "https://discord.com/api/webhooks/1/writers"


def _login_admin(client: Client, username: str = "admin") -> User:
    user = User.objects.create_superuser(username=username, email=f"{username}@x.com", password="p")
    client.login(username=username, password="p")
    return user


def _login_lead(client: Client, guild, username: str = "lead") -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@x.com", password="p")
    guild.guild_lead = user.member
    guild.save(update_fields=["guild_lead"])
    client.login(username=username, password="p")
    return user


def _guild_member(guild, username: str = "reader") -> User:
    """An ACTIVE guild member with a signed-in account: a real recipient of the guild send."""
    member = MemberFactory()
    with mute_signals(post_save):
        user = User.objects.create_user(
            username=username, email=f"{username}@x.com", password="p", last_login=timezone.now()
        )
    member.user = user
    member.save(update_fields=["user"])
    GuildMembershipFactory(guild=guild, member=member)
    return user


def _writers_guild():
    return GuildFactory(name="Writers Guild", discord_webhook_url=_WEBHOOK)


def _send_data(guild, **overrides) -> dict:
    data = {
        "audience": f"guild:{guild.pk}",
        "subject": _SUBJECT,
        "body": "<p>See you Thursday at 7.</p>",
        "send_email": "on",
        "push_enabled": "on",
        "discord_enabled": "on",
        "discord_channel": "guild",
        "mention": "none",
        "draft_pk": "",
    }
    data.update(overrides)
    return data


def _trigger(response) -> dict:
    return json.loads(response["HX-Trigger"])


def _results_draft(admin: User) -> AnnouncementDraft:
    """The October 2026 results announcement, made the way the Voting page makes it (results and all)."""
    snapshot = FundingSnapshotFactory(
        cycle_label="October 2026",
        results={"votes_cast": 12, "results": [{"guild_name": "Metal Guild", "funding": "600.00", "share_pct": 60.0}]},
    )
    return snapshot.draft_results_announcement(admin)


def describe_the_write_step():
    def it_shows_subject_above_the_message_with_the_automatic_title_as_placeholder_and_hint(client: Client):
        guild = _writers_guild()
        _login_lead(client, guild)
        content = client.get(reverse("hub_compose")).content.decode()
        assert 'id="compose-subject"' in content
        assert 'name="subject"' in content
        assert 'placeholder="Writers Guild Announcement"' in content
        assert "Leave blank to use Writers Guild Announcement" in content
        assert content.index('name="subject"') < content.index('name="body"')
        # Optional: no required mark, and a 200 character cap on the input itself.
        assert 'name="subject"' in content and 'maxlength="200"' in content

    def it_wires_the_subject_into_the_push_preview_title(client: Client):
        _login_admin(client)
        content = client.get(reverse("hub_compose")).content.decode()
        assert 'x-model="subject"' in content
        assert "(subject.trim() || category)" in content

    def it_shows_no_subject_field_on_a_results_announcement(client: Client):
        admin = _login_admin(client)
        draft = _results_draft(admin)
        content = client.get(reverse("hub_compose_resume", args=[draft.pk])).content.decode()
        assert 'name="subject"' not in content
        assert 'id="compose-subject"' not in content
        assert "October 2026 Voting Results" in content

    def it_resumes_a_draft_with_the_subject_it_was_saved_with(client: Client):
        guild = _writers_guild()
        _login_lead(client, guild)
        client.post(reverse("hub_compose_save_draft"), _send_data(guild))
        draft = AnnouncementDraft.objects.get(guild=guild)
        content = client.get(reverse("hub_compose_resume", args=[draft.pk])).content.decode()
        assert f'value="{_SUBJECT}"' in content

    def it_resumes_a_draft_saved_before_subjects_with_the_field_blank(client: Client):
        guild = _writers_guild()
        _login_lead(client, guild)
        draft = AnnouncementDraftFactory(audience=AnnouncementDraft.Audience.GUILD, guild=guild)
        assert draft.title == "Writers Guild Announcement"
        content = client.get(reverse("hub_compose_resume", args=[draft.pk])).content.decode()
        assert 'placeholder="Writers Guild Announcement"' in content
        assert 'value="Writers Guild Announcement"' not in content

    def it_resumes_an_urgent_draft_without_the_urgent_lead_in_the_field(client: Client):
        guild = _writers_guild()
        _login_lead(client, guild)
        client.post(reverse("hub_compose_save_draft"), _send_data(guild, mark_as_urgent="on"))
        draft = AnnouncementDraft.objects.get(guild=guild)
        assert draft.title == f"Urgent: {_SUBJECT}"
        content = client.get(reverse("hub_compose_resume", args=[draft.pk])).content.decode()
        assert f'value="{_SUBJECT}"' in content
        assert f'value="Urgent: {_SUBJECT}"' not in content


def describe_changing_the_audience():
    def it_re_renders_the_field_for_the_new_audience_and_keeps_the_typed_subject(client: Client):
        _login_admin(client)
        glass = GuildFactory(name="Glass Guild")
        response = client.get(reverse("hub_compose_count"), {"audience": f"guild:{glass.pk}", "subject": _SUBJECT})
        assert response.status_code == 200
        content = response.content.decode()
        assert 'id="compose-subject"' in content
        assert 'hx-swap-oob="true"' in content
        assert 'placeholder="Glass Guild Announcement"' in content
        assert "Leave blank to use Glass Guild Announcement" in content
        assert 'data-compose-category="Glass Guild Announcement"' in content
        assert f'value="{_SUBJECT}"' in content

    def it_updates_the_placeholder_alone_when_the_subject_is_blank(client: Client):
        _login_admin(client)
        content = client.get(reverse("hub_compose_count"), {"audience": "site", "subject": ""}).content.decode()
        assert 'placeholder="Makerspace Announcement"' in content
        assert "Leave blank to use Makerspace Announcement" in content
        assert 'name="subject" value=' not in content

    def it_posts_the_subject_with_the_audience_change(client: Client):
        _login_admin(client)
        content = client.get(reverse("hub_compose")).content.decode()
        assert (
            'hx-include="[name=audience],[name=include_waitlist],[name=include_never_logged_in],[name=subject]"'
            in content
        )


def describe_saving_a_draft():
    def it_stores_the_written_subject_as_the_title(client: Client):
        guild = _writers_guild()
        _login_lead(client, guild)
        response = client.post(reverse("hub_compose_save_draft"), _send_data(guild))
        assert response.status_code == 200
        assert AnnouncementDraft.objects.get(guild=guild).title == _SUBJECT

    def it_keeps_the_automatic_title_with_subject_blank(client: Client):
        guild = _writers_guild()
        _login_lead(client, guild)
        client.post(reverse("hub_compose_save_draft"), _send_data(guild, subject=""))
        assert AnnouncementDraft.objects.get(guild=guild).title == "Writers Guild Announcement"

    def it_refuses_a_subject_past_two_hundred_characters_and_saves_nothing(client: Client):
        guild = _writers_guild()
        _login_lead(client, guild)
        response = client.post(reverse("hub_compose_save_draft"), _send_data(guild, subject="x" * 201))
        assert response.status_code == 204
        assert _trigger(response)["showToast"]["type"] == "error"
        assert not AnnouncementDraft.objects.exists()


def describe_sending_a_guild_announcement():
    @respx.mock
    def it_arrives_with_the_written_subject_everywhere(client: Client, mailoutbox):
        guild = _writers_guild()
        _login_lead(client, guild)
        reader = _guild_member(guild)
        route = respx.post(_WEBHOOK).mock(return_value=httpx.Response(204))

        response = client.post(reverse("hub_compose_send"), _send_data(guild))
        assert response.status_code == 302

        draft = AnnouncementDraft.objects.get(guild=guild)
        assert draft.sent_at is not None
        assert draft.title == _SUBJECT
        # Email subject, bell title and the Discord embed title.
        assert [message.subject for message in mailoutbox if reader.email in message.to] == [_SUBJECT]
        assert Notification.objects.get(user=reader, trigger="guild_announcement").title == _SUBJECT
        assert route.called
        assert json.loads(route.calls.last.request.content)["embeds"][0]["title"] == _SUBJECT
        # The guild page headline and the Sent tab row.
        assert GuildAnnouncement.objects.get(guild=guild).title == _SUBJECT
        guild_page = client.get(reverse("hub_guild_detail", args=[guild.slug])).content.decode()
        assert f'class="pl-guild-announcement__title">{_SUBJECT}</h3>' in guild_page
        sent_tab = client.get(f"{reverse('hub_announcements')}?tab=sent").content.decode()
        assert f'class="pl-announcements-table__title">{_SUBJECT}</a>' in sent_tab

    def it_leads_with_urgent_when_marked(client: Client, mailoutbox):
        guild = _writers_guild()
        _login_lead(client, guild)
        reader = _guild_member(guild)
        client.post(reverse("hub_compose_send"), _send_data(guild, mark_as_urgent="on", discord_channel="none"))
        assert AnnouncementDraft.objects.get(guild=guild).title == f"Urgent: {_SUBJECT}"
        assert GuildAnnouncement.objects.get(guild=guild).title == f"Urgent: {_SUBJECT}"
        assert [message.subject for message in mailoutbox if reader.email in message.to] == [f"Urgent: {_SUBJECT}"]
        assert Notification.objects.get(user=reader, trigger="guild_announcement").title == f"Urgent: {_SUBJECT}"

    @respx.mock
    def it_arrives_titled_the_guild_announcement_with_subject_blank(client: Client, mailoutbox):
        guild = _writers_guild()
        _login_lead(client, guild)
        reader = _guild_member(guild)
        route = respx.post(_WEBHOOK).mock(return_value=httpx.Response(204))
        client.post(reverse("hub_compose_send"), _send_data(guild, subject=""))
        assert AnnouncementDraft.objects.get(guild=guild).title == "Writers Guild Announcement"
        assert GuildAnnouncement.objects.get(guild=guild).title == "Writers Guild Announcement"
        assert [message.subject for message in mailoutbox if reader.email in message.to] == [
            "Writers Guild Announcement"
        ]
        assert Notification.objects.get(user=reader, trigger="guild_announcement").title == "Writers Guild Announcement"
        assert json.loads(route.calls.last.request.content)["embeds"][0]["title"] == "Writers Guild Announcement"


def describe_the_previews():
    def it_shows_the_written_subject_in_the_email_and_discord_previews(client: Client):
        guild = _writers_guild()
        _login_lead(client, guild)
        content = client.post(reverse("hub_compose_preview"), _send_data(guild)).content.decode()
        assert f'class="pl-email-preview__subject">{_SUBJECT}</span>' in content
        assert f"data-discord-preview-title>{_SUBJECT}</div>" in content
        assert "Writers Guild Announcement" not in content

    def it_shows_the_automatic_title_with_subject_blank(client: Client):
        guild = _writers_guild()
        _login_lead(client, guild)
        content = client.post(reverse("hub_compose_preview"), _send_data(guild, subject="")).content.decode()
        assert 'class="pl-email-preview__subject">Writers Guild Announcement</span>' in content

    def it_leads_the_previews_with_urgent_when_marked(client: Client):
        guild = _writers_guild()
        _login_lead(client, guild)
        content = client.post(reverse("hub_compose_preview"), _send_data(guild, mark_as_urgent="on")).content.decode()
        assert f'class="pl-email-preview__subject">Urgent: {_SUBJECT}</span>' in content

    def it_keeps_the_voting_results_title_whatever_subject_is_posted(client: Client):
        admin = _login_admin(client)
        draft = _results_draft(admin)
        data = {
            "audience": "site",
            "subject": "Retitled",
            "body": "<p>Results.</p>",
            "discord_channel": "none",
            "mention": "none",
            "draft_pk": str(draft.pk),
        }
        content = client.post(reverse("hub_compose_preview"), data).content.decode()
        assert 'class="pl-email-preview__subject">October 2026 Voting Results</span>' in content
        assert "Retitled" not in content

    def it_titles_the_push_test_with_the_written_subject(client: Client, monkeypatch):
        from core import push_admin

        guild = _writers_guild()
        _login_lead(client, guild)
        seen: dict = {}

        def fake_send(user, **kwargs):
            seen.update(kwargs)
            return push_admin.TestSendResult(delivered=1, attempted=1)

        monkeypatch.setattr(push_admin, "send_test_push", fake_send)
        client.post(reverse("hub_compose_push_test"), _send_data(guild))
        assert seen["title"] == _SUBJECT

    def it_sends_the_test_email_with_the_written_subject(client: Client, mailoutbox):
        guild = _writers_guild()
        lead = _login_lead(client, guild)
        client.post(reverse("hub_compose_test"), _send_data(guild))
        assert [message.subject for message in mailoutbox if lead.email in message.to] == [_SUBJECT]
