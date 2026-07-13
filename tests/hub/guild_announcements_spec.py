"""Guild announcements — edit + delete of an already-published post on the guild-edit page.

Composing/sending a guild announcement moved to the /announcements/compose/ wizard (see the
note below); this file covers editing and deleting posts that already exist.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from membership.models import GuildAnnouncement, Member
from tests.membership.factories import (
    GuildAnnouncementFactory,
    GuildFactory,
    MembershipPlanFactory,
)


def _editor_user(username: str) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pw")
    member = user.member
    member.fog_role = Member.FogRole.ADMIN
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()
    return user


@pytest.mark.django_db
def describe_announcement_delete():
    def it_deletes_an_announcement_for_an_editor(client: Client):
        _editor_user("a")
        client.login(username="a", password="pw")
        guild = GuildFactory()
        announcement = GuildAnnouncementFactory(guild=guild)
        client.post(reverse("hub_guild_announcement_delete", args=[guild.pk, announcement.pk]))
        assert not GuildAnnouncement.objects.filter(pk=announcement.pk).exists()


@pytest.mark.django_db
def describe_announcement_delete_permissions():
    def it_forbids_non_editors(client: Client):
        MembershipPlanFactory()
        user = User.objects.create_user(username="plain_ann", password="pw")
        member = user.member
        member.fog_role = Member.FogRole.MEMBER
        member.save(update_fields=["fog_role"])
        member.sync_user_permissions()
        client.login(username="plain_ann", password="pw")
        guild = GuildFactory()
        announcement = GuildAnnouncementFactory(guild=guild)
        resp = client.post(reverse("hub_guild_announcement_delete", args=[guild.pk, announcement.pk]))
        assert resp.status_code == 403
        assert GuildAnnouncement.objects.filter(pk=announcement.pk).exists()


# NOTE: the guild-edit inline "Post an Announcement" *create* form (hub_guild_announcement_create)
# was retired — guild announcements are now composed in the /announcements/compose/ wizard, which
# materializes a published GuildAnnouncement + notifies via AnnouncementDraft.send(). That path is
# covered by tests/membership/announcement_draft_spec.py and tests/hub/announcement_compose_spec.py.
# The edit + delete of an already-published post (below) still live on the guild-edit page.


@pytest.mark.django_db
def describe_announcement_edit():
    def it_renders_a_prefilled_form_on_get(client: Client):
        from datetime import date

        _editor_user("ae_get")
        client.login(username="ae_get", password="pw")
        guild = GuildFactory()
        announcement = GuildAnnouncementFactory(
            guild=guild, title="Forge Night", body="Bring gloves.", expires_at=date(2099, 12, 31)
        )
        resp = client.get(reverse("hub_guild_announcement_edit", args=[guild.pk, announcement.pk]))
        assert resp.status_code == 200
        assert b"Forge Night" in resp.content
        assert b"Bring gloves." in resp.content
        # Date renders in the input's YYYY-MM-DD format.
        assert b"2099-12-31" in resp.content

    def it_updates_the_announcement_and_keeps_published_at_and_author(client: Client):
        from datetime import date

        user = _editor_user("ae_post")
        client.login(username="ae_post", password="pw")
        guild = GuildFactory()
        announcement = GuildAnnouncementFactory(guild=guild, title="Old", body="Old body")
        announcement.author = user
        announcement.save(update_fields=["author"])
        original_published = announcement.published_at
        resp = client.post(
            reverse("hub_guild_announcement_edit", args=[guild.pk, announcement.pk]),
            {"title": "New title", "body": "New body", "expires_at": "2099-01-15"},
        )
        assert resp.status_code == 200
        announcement.refresh_from_db()
        assert announcement.title == "New title"
        assert announcement.body == "New body"
        assert announcement.expires_at == date(2099, 1, 15)
        # published_at and author are untouched on edit.
        assert announcement.published_at == original_published
        assert announcement.author == user

    def it_returns_an_oob_row_a_toast_and_a_close_modal_trigger(client: Client):
        import json

        _editor_user("ae_oob")
        client.login(username="ae_oob", password="pw")
        guild = GuildFactory()
        announcement = GuildAnnouncementFactory(guild=guild)
        resp = client.post(
            reverse("hub_guild_announcement_edit", args=[guild.pk, announcement.pk]),
            {"title": "Edited", "body": "Edited body", "expires_at": ""},
        )
        assert resp.status_code == 200
        # OOB row for the swap ...
        assert f'id="announcement-row-{announcement.pk}"'.encode() in resp.content
        assert b'hx-swap-oob="true"' in resp.content
        # ... a success toast (HX-Trigger) ...
        trigger = json.loads(resp["HX-Trigger"])
        assert trigger["showToast"]["message"] == "Announcement updated."
        assert trigger["showToast"]["type"] == "success"
        # ... and the close-modal event that actually dismisses the modal.
        settle = json.loads(resp["HX-Trigger-After-Settle"])
        assert settle["close-modal"] == f"edit-ann-{announcement.pk}"

    def it_does_not_clobber_the_send_options_on_edit(client: Client):
        # Editing never re-sends, and the send options aren't on the edit form — a blank
        # value there must not reset the originally-chosen email toggle or channel choice.
        _editor_user("ae_noclobber")
        client.login(username="ae_noclobber", password="pw")
        guild = GuildFactory()
        announcement = GuildAnnouncementFactory(
            guild=guild, send_email=True, discord_channel=GuildAnnouncement.DiscordChannel.GENERAL
        )
        client.post(
            reverse("hub_guild_announcement_edit", args=[guild.pk, announcement.pk]),
            {"title": "Edited", "body": "Edited body", "expires_at": ""},
        )
        announcement.refresh_from_db()
        assert announcement.send_email is True
        assert announcement.discord_channel == GuildAnnouncement.DiscordChannel.GENERAL

    def it_re_renders_with_errors_and_no_close_trigger_on_invalid(client: Client):
        _editor_user("ae_inv")
        client.login(username="ae_inv", password="pw")
        guild = GuildFactory()
        announcement = GuildAnnouncementFactory(guild=guild, title="Keep", body="Keep body")
        resp = client.post(
            reverse("hub_guild_announcement_edit", args=[guild.pk, announcement.pk]),
            {"title": "", "body": "Keep body", "expires_at": ""},  # blank title → invalid
        )
        assert resp.status_code == 200
        announcement.refresh_from_db()
        assert announcement.title == "Keep"  # unchanged
        # The modal must stay open — no close trigger on an invalid submit.
        assert "HX-Trigger-After-Settle" not in resp
        # The form re-renders so the user can fix the error.
        assert b"hx-post" in resp.content

    def it_forbids_non_editors_on_get_and_post(client: Client):
        MembershipPlanFactory()
        user = User.objects.create_user(username="ae_403", password="pw")
        user.member.fog_role = Member.FogRole.MEMBER
        user.member.save(update_fields=["fog_role"])
        user.member.sync_user_permissions()
        client.login(username="ae_403", password="pw")
        guild = GuildFactory()
        announcement = GuildAnnouncementFactory(guild=guild)
        get_resp = client.get(reverse("hub_guild_announcement_edit", args=[guild.pk, announcement.pk]))
        assert get_resp.status_code == 403
        post_resp = client.post(
            reverse("hub_guild_announcement_edit", args=[guild.pk, announcement.pk]),
            {"title": "x", "body": "y", "expires_at": ""},
        )
        assert post_resp.status_code == 403

    def it_404s_for_an_announcement_from_another_guild(client: Client):
        _editor_user("ae_404")
        client.login(username="ae_404", password="pw")
        guild = GuildFactory()
        other_guild = GuildFactory()
        announcement = GuildAnnouncementFactory(guild=other_guild)
        resp = client.get(reverse("hub_guild_announcement_edit", args=[guild.pk, announcement.pk]))
        assert resp.status_code == 404


@pytest.mark.django_db
def describe_announcement_display():
    def it_shows_active_announcements_on_the_guild_page(client: Client):
        _editor_user("disp")
        client.login(username="disp", password="pw")
        guild = GuildFactory()
        GuildAnnouncementFactory(guild=guild, title="LiveAnnounce")
        resp = client.get(reverse("hub_guild_detail", args=[guild.slug]))
        assert b"LiveAnnounce" in resp.content

    def it_hides_expired_announcements_on_the_guild_page(client: Client):
        from datetime import timedelta

        from django.utils import timezone

        _editor_user("disp2")
        client.login(username="disp2", password="pw")
        guild = GuildFactory()
        GuildAnnouncementFactory(guild=guild, title="GoneAnnounce", expires_at=timezone.localdate() - timedelta(days=1))
        resp = client.get(reverse("hub_guild_detail", args=[guild.slug]))
        assert b"GoneAnnounce" not in resp.content


@pytest.mark.django_db
def describe_announcement_form_send_options():
    def it_defaults_email_on_and_channel_to_the_guilds_own_when_configured():
        from hub.forms import GuildAnnouncementForm

        guild = GuildFactory(discord_webhook_url="https://discord.com/api/webhooks/1/guild")
        form = GuildAnnouncementForm(guild=guild)
        assert form["send_email"].value() is True
        # Guild Channel is pre-selected when the guild has its own webhook (§5.3).
        assert form["discord_channel"].value() == GuildAnnouncement.DiscordChannel.GUILD

    def it_steps_down_to_dont_post_when_no_channel_is_configured():
        from hub.forms import GuildAnnouncementForm

        guild = GuildFactory()  # no guild webhook, no shared webhooks
        form = GuildAnnouncementForm(guild=guild)
        assert form["discord_channel"].value() == GuildAnnouncement.DiscordChannel.NONE
