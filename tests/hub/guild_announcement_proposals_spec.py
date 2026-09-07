"""BDD specs for member-proposed guild announcements: the propose/edit/withdraw flow, the
reviewer queue (gating + guild scoping), the approve/changes/decline decision (including the
outbound-channel toggles and required-notes error), and the guild-page entry points.
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


def _member(username: str, *, fog_role: str = Member.FogRole.MEMBER) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="pass")
    member = user.member
    member.fog_role = fog_role
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()
    return user


def _payload(guild_pk: int, **overrides: str) -> dict:
    data = {"guild": str(guild_pk), "title": "My Announcement", "body": "Some news.", "expires_at": ""}
    data.update(overrides)
    return data


@pytest.mark.django_db
def describe_propose_create():
    def it_redirects_an_anonymous_user_to_login(client: Client):
        resp = client.get(reverse("hub_guild_announcement_propose"))
        assert resp.status_code == 302
        assert "login" in resp["Location"]

    def it_submits_any_members_proposal_for_review(client: Client):
        user = _member("a1")
        guild = GuildFactory()
        client.login(username="a1", password="pass")
        resp = client.post(reverse("hub_guild_announcement_propose"), data=_payload(guild.pk, title="Proposed News"))
        assert resp.status_code == 302
        announcement = GuildAnnouncement.objects.get(title="Proposed News")
        assert announcement.moderation_state == GuildAnnouncement.ModerationState.PENDING
        assert announcement.submitted_by == user
        assert announcement.guild == guild

    def it_prefills_the_guild_from_the_query_string(client: Client):
        _member("a2")
        guild = GuildFactory(name="Prefill Guild")
        client.login(username="a2", password="pass")
        resp = client.get(reverse("hub_guild_announcement_propose") + f"?guild={guild.pk}")
        assert resp.status_code == 200
        assert b"Prefill Guild" in resp.content

    def it_re_renders_with_errors_on_an_invalid_submission(client: Client):
        _member("inv1")
        guild = GuildFactory()
        client.login(username="inv1", password="pass")
        resp = client.post(reverse("hub_guild_announcement_propose"), data=_payload(guild.pk, title=""))
        assert resp.status_code == 200
        assert not GuildAnnouncement.objects.filter(body="Some news.", title="").exists()

    def it_shows_the_empty_state_with_no_proposals(client: Client):
        _member("mp3")
        client.login(username="mp3", password="pass")
        resp = client.get(reverse("hub_guild_announcement_propose"))
        assert resp.status_code == 200


@pytest.mark.django_db
def describe_propose_edit():
    def it_lets_the_owner_edit_a_changes_requested_proposal_and_resubmit(client: Client):
        user = _member("e1")
        guild = GuildFactory()
        announcement = GuildAnnouncementFactory(
            guild=guild,
            changes_requested=True,
            submitted_by=user,
            review_notes="Please add the time.",
        )
        client.login(username="e1", password="pass")
        get_resp = client.get(reverse("hub_guild_announcement_propose_edit", args=[announcement.pk]))
        assert get_resp.status_code == 200
        assert b"A reviewer asked for changes" in get_resp.content
        assert b"Please add the time." in get_resp.content

        post_resp = client.post(
            reverse("hub_guild_announcement_propose_edit", args=[announcement.pk]),
            data=_payload(guild.pk, title="Fixed Title", body="Now at 6pm."),
        )
        assert post_resp.status_code == 302
        announcement.refresh_from_db()
        assert announcement.title == "Fixed Title"
        assert announcement.moderation_state == GuildAnnouncement.ModerationState.PENDING
        assert announcement.review_notes == ""

    def it_404s_editing_another_members_proposal(client: Client):
        _member("e2")
        other = _member("e2other")
        announcement = GuildAnnouncementFactory(pending=True, submitted_by=other)
        client.login(username="e2", password="pass")
        assert client.get(reverse("hub_guild_announcement_propose_edit", args=[announcement.pk])).status_code == 404

    def it_404s_editing_a_published_or_declined_proposal(client: Client):
        user = _member("e3")
        published = GuildAnnouncementFactory(submitted_by=user)  # PUBLISHED
        declined = GuildAnnouncementFactory(declined=True, submitted_by=user)
        client.login(username="e3", password="pass")
        assert client.get(reverse("hub_guild_announcement_propose_edit", args=[published.pk])).status_code == 404
        assert client.get(reverse("hub_guild_announcement_propose_edit", args=[declined.pk])).status_code == 404


@pytest.mark.django_db
def describe_my_proposals_surface():
    def it_shows_the_members_own_non_published_proposals_with_status(client: Client):
        user = _member("mp1")
        GuildAnnouncementFactory(pending=True, submitted_by=user, title="Mine Pending")
        client.login(username="mp1", password="pass")
        resp = client.get(reverse("hub_guild_announcement_propose"))
        assert b"Mine Pending" in resp.content
        assert b"Pending review" in resp.content

    def it_does_not_show_another_members_proposals(client: Client):
        _member("mp2")
        other = _member("mp2other")
        GuildAnnouncementFactory(pending=True, submitted_by=other, title="Someone Elses")
        client.login(username="mp2", password="pass")
        resp = client.get(reverse("hub_guild_announcement_propose"))
        assert b"Someone Elses" not in resp.content


@pytest.mark.django_db
def describe_withdraw():
    def it_deletes_the_owners_pending_proposal(client: Client):
        user = _member("w1")
        announcement = GuildAnnouncementFactory(pending=True, submitted_by=user)
        client.login(username="w1", password="pass")
        resp = client.post(reverse("hub_guild_announcement_withdraw", args=[announcement.pk]))
        assert resp.status_code == 302
        assert not GuildAnnouncement.objects.filter(pk=announcement.pk).exists()

    def it_404s_a_non_owner(client: Client):
        _member("w2")
        other = _member("w2other")
        announcement = GuildAnnouncementFactory(pending=True, submitted_by=other)
        client.login(username="w2", password="pass")
        assert client.post(reverse("hub_guild_announcement_withdraw", args=[announcement.pk])).status_code == 404
        assert GuildAnnouncement.objects.filter(pk=announcement.pk).exists()

    def it_rejects_a_get(client: Client):
        user = _member("w4")
        announcement = GuildAnnouncementFactory(pending=True, submitted_by=user)
        client.login(username="w4", password="pass")
        assert client.get(reverse("hub_guild_announcement_withdraw", args=[announcement.pk])).status_code == 405


@pytest.mark.django_db
def describe_review_queue_gating():
    def it_403s_a_plain_member(client: Client):
        _member("pm1")
        client.login(username="pm1", password="pass")
        assert client.get(reverse("hub_guild_announcement_review_queue")).status_code == 403

    def it_lets_an_admin_see_every_pending_proposal(client: Client):
        _member("adm1", fog_role=Member.FogRole.ADMIN)
        GuildAnnouncementFactory(pending=True, title="Any Guild Pending")
        client.login(username="adm1", password="pass")
        resp = client.get(reverse("hub_guild_announcement_review_queue"))
        assert resp.status_code == 200
        assert b"Any Guild Pending" in resp.content

    def it_scopes_a_lead_to_their_own_guilds(client: Client):
        user = _member("lead1")
        guild_a = GuildFactory(guild_lead=user.member)
        guild_b = GuildFactory()
        GuildAnnouncementFactory(guild=guild_a, pending=True, title="Mine Pending")
        GuildAnnouncementFactory(guild=guild_b, pending=True, title="Other Guild Pending")
        client.login(username="lead1", password="pass")
        resp = client.get(reverse("hub_guild_announcement_review_queue"))
        assert resp.status_code == 200
        assert b"Mine Pending" in resp.content
        assert b"Other Guild Pending" not in resp.content
        assert b'id="announcement-' in resp.content

    def it_renders_the_channel_picker_in_the_approve_modal(client: Client):
        # §6.4 — the approve modal reuses the same Discord channel picker as the lead's post
        # form, with this guild's unconfigured channels disabled.
        _member("adm_picker", fog_role=Member.FogRole.ADMIN)
        GuildAnnouncementFactory(pending=True, title="Pick me")  # guild has no webhook
        client.login(username="adm_picker", password="pass")
        content = client.get(reverse("hub_guild_announcement_review_queue")).content.decode()
        assert "Post to Discord channel" in content
        assert 'name="discord_channel"' in content
        assert 'data-hint="Not set up yet."' in content


@pytest.mark.django_db
def describe_review_decision():
    def it_approves_and_posts_with_the_chosen_channels(client: Client):
        _member("adm2", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory(discord_webhook_url="https://discord.com/api/webhooks/1/guild")
        announcement = GuildAnnouncementFactory(pending=True, guild=guild)
        client.login(username="adm2", password="pass")
        resp = client.post(
            reverse("hub_guild_announcement_review_decision", args=[announcement.pk]),
            data={"decision": "approve", "discord_channel": "guild"},  # send_email unchecked
        )
        assert resp.status_code == 302
        announcement.refresh_from_db()
        assert announcement.moderation_state == GuildAnnouncement.ModerationState.PUBLISHED
        assert announcement.discord_channel == GuildAnnouncement.DiscordChannel.GUILD
        assert announcement.send_email is False

    def it_approves_via_the_query_string(client: Client):
        _member("adm2b", fog_role=Member.FogRole.ADMIN)
        announcement = GuildAnnouncementFactory(pending=True)
        client.login(username="adm2b", password="pass")
        resp = client.post(
            reverse("hub_guild_announcement_review_decision", args=[announcement.pk]) + "?decision=approve"
        )
        assert resp.status_code == 302
        announcement.refresh_from_db()
        assert announcement.moderation_state == GuildAnnouncement.ModerationState.PUBLISHED

    def it_declines_with_a_note(client: Client):
        _member("adm3", fog_role=Member.FogRole.ADMIN)
        announcement = GuildAnnouncementFactory(pending=True)
        client.login(username="adm3", password="pass")
        resp = client.post(
            reverse("hub_guild_announcement_review_decision", args=[announcement.pk]),
            data={"decision": "decline", "notes": "Not this time."},
        )
        assert resp.status_code == 302
        announcement.refresh_from_db()
        assert announcement.moderation_state == GuildAnnouncement.ModerationState.DECLINED
        assert announcement.review_notes == "Not this time."

    def it_requests_changes_with_a_note(client: Client):
        _member("adm4", fog_role=Member.FogRole.ADMIN)
        announcement = GuildAnnouncementFactory(pending=True)
        client.login(username="adm4", password="pass")
        resp = client.post(
            reverse("hub_guild_announcement_review_decision", args=[announcement.pk]),
            data={"decision": "changes", "notes": "Add a start time."},
        )
        assert resp.status_code == 302
        announcement.refresh_from_db()
        assert announcement.moderation_state == GuildAnnouncement.ModerationState.CHANGES_REQUESTED

    def it_re_renders_the_queue_with_an_error_when_a_note_is_missing(client: Client):
        _member("adm5", fog_role=Member.FogRole.ADMIN)
        announcement = GuildAnnouncementFactory(pending=True)
        client.login(username="adm5", password="pass")
        resp = client.post(
            reverse("hub_guild_announcement_review_decision", args=[announcement.pk]),
            data={"decision": "decline", "notes": ""},
        )
        assert resp.status_code == 200
        assert b"Add a note so the proposer knows why." in resp.content
        announcement.refresh_from_db()
        assert announcement.moderation_state == GuildAnnouncement.ModerationState.PENDING

    def it_friendly_redirects_when_already_handled(client: Client):
        _member("adm6", fog_role=Member.FogRole.ADMIN)
        announcement = GuildAnnouncementFactory()  # already PUBLISHED
        client.login(username="adm6", password="pass")
        resp = client.post(
            reverse("hub_guild_announcement_review_decision", args=[announcement.pk]) + "?decision=approve"
        )
        assert resp.status_code == 302  # no 500
        announcement.refresh_from_db()
        assert announcement.moderation_state == GuildAnnouncement.ModerationState.PUBLISHED

    def it_403s_a_plain_member(client: Client):
        _member("pm2")
        announcement = GuildAnnouncementFactory(pending=True)
        client.login(username="pm2", password="pass")
        resp = client.post(
            reverse("hub_guild_announcement_review_decision", args=[announcement.pk]) + "?decision=approve"
        )
        assert resp.status_code == 403

    def it_404s_a_lead_deciding_on_another_guilds_proposal(client: Client):
        user = _member("iso_lead")
        GuildFactory(guild_lead=user.member)
        guild_b = GuildFactory()
        announcement = GuildAnnouncementFactory(guild=guild_b, pending=True)
        client.login(username="iso_lead", password="pass")
        resp = client.post(
            reverse("hub_guild_announcement_review_decision", args=[announcement.pk]) + "?decision=approve"
        )
        assert resp.status_code == 404

    def it_rejects_a_get(client: Client):
        _member("adm7", fog_role=Member.FogRole.ADMIN)
        announcement = GuildAnnouncementFactory(pending=True)
        client.login(username="adm7", password="pass")
        assert client.get(reverse("hub_guild_announcement_review_decision", args=[announcement.pk])).status_code == 405


@pytest.mark.django_db
def describe_guild_page_entry_points():
    # These assert on button-specific markup (the propose href / the button's title
    # attribute) — NOT loose visible text, which would also match the "what's new"
    # release-notes widget that echoes this version's changelog on every page.
    def it_shows_suggest_to_a_logged_in_non_editor(client: Client):
        _member("v1")
        guild = GuildFactory(name="Suggest Guild")
        client.login(username="v1", password="pass")
        resp = client.get(reverse("hub_guild_detail", args=[guild.slug]))
        assert f"/announcements/propose/?guild={guild.pk}".encode() in resp.content

    def it_hides_suggest_from_an_editor(client: Client):
        _member("v1b", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory(name="Suggest Editor Guild")
        client.login(username="v1b", password="pass")
        resp = client.get(reverse("hub_guild_detail", args=[guild.slug]))
        assert f"/announcements/propose/?guild={guild.pk}".encode() not in resp.content

    def it_hides_suggest_when_the_guild_disables_member_suggestions(client: Client):
        _member("v1c")
        guild = GuildFactory(name="Closed Suggest Guild", allow_member_announcement_suggestions=False)
        client.login(username="v1c", password="pass")
        resp = client.get(reverse("hub_guild_detail", args=[guild.slug]))
        assert f"/announcements/propose/?guild={guild.pk}".encode() not in resp.content
        # The empty-state invitation is softened to plain copy (no dead-end invite to a hidden button).
        assert b"be the first to suggest one" not in resp.content
        assert b"No announcements yet." in resp.content

    def it_keeps_the_suggest_invite_when_suggestions_are_on(client: Client):
        _member("v1d")
        guild = GuildFactory(name="Open Suggest Guild")
        client.login(username="v1d", password="pass")
        resp = client.get(reverse("hub_guild_detail", args=[guild.slug]))
        assert b"be the first to suggest one" in resp.content

    def it_shows_the_view_public_button_to_an_editor_on_a_guild(client: Client):
        _member("ed1", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory(name="Public View Guild")
        client.login(username="ed1", password="pass")
        resp = client.get(reverse("hub_guild_detail", args=[guild.slug]))
        assert b"public page as a guest" in resp.content  # the button's title attribute


@pytest.mark.django_db
def describe_suggestion_gating():
    def it_excludes_disabled_guilds_from_the_picker_for_new_proposals(client: Client):
        _member("sg1")
        GuildFactory(name="Open Guild")
        GuildFactory(name="Closed Guild", allow_member_announcement_suggestions=False)
        client.login(username="sg1", password="pass")
        resp = client.get(reverse("hub_guild_announcement_propose"))
        names = {g.name for g in resp.context["form"].fields["guild"].queryset}
        assert "Open Guild" in names
        assert "Closed Guild" not in names

    def it_rejects_a_post_naming_a_disabled_guild_with_the_exact_message(client: Client):
        _member("sg2")
        guild = GuildFactory(allow_member_announcement_suggestions=False)
        # A second, open guild so the form still renders (this is not the all-disabled case).
        GuildFactory(name="Still Open")
        client.login(username="sg2", password="pass")
        resp = client.post(reverse("hub_guild_announcement_propose"), data=_payload(guild.pk))
        assert resp.status_code == 200
        assert "This guild isn't taking member suggestions right now." in resp.context["form"].errors["guild"]
        assert not GuildAnnouncement.objects.filter(guild=guild).exists()

    def it_degrades_a_disabled_guild_query_string_to_the_unfixed_picker(client: Client):
        _member("sg3")
        GuildFactory(name="Still Open")
        guild = GuildFactory(name="Closed Guild", allow_member_announcement_suggestions=False)
        client.login(username="sg3", password="pass")
        resp = client.get(reverse("hub_guild_announcement_propose") + f"?guild={guild.pk}")
        assert resp.status_code == 200
        assert resp.context["form"]["guild"].value() is None  # not preselected
        assert guild not in set(resp.context["form"].fields["guild"].queryset)

    def it_renders_the_empty_state_when_no_guilds_take_suggestions(client: Client):
        _member("sg4")
        GuildFactory(allow_member_announcement_suggestions=False)
        client.login(username="sg4", password="pass")
        resp = client.get(reverse("hub_guild_announcement_propose"))
        assert resp.status_code == 200
        assert resp.context["no_guilds_available"] is True
        assert resp.context["form"] is None
        assert b"No guilds are taking member suggestions right now." in resp.content

    def it_keeps_a_changes_requested_proposal_editable_for_a_since_disabled_guild(client: Client):
        user = _member("sg5")
        guild = GuildFactory(allow_member_announcement_suggestions=False)
        announcement = GuildAnnouncementFactory(guild=guild, changes_requested=True, submitted_by=user)
        client.login(username="sg5", password="pass")
        get_resp = client.get(reverse("hub_guild_announcement_propose_edit", args=[announcement.pk]))
        assert get_resp.status_code == 200
        # The proposal's own guild stays selectable so it can be revised without repointing.
        assert guild in set(get_resp.context["form"].fields["guild"].queryset)
        post_resp = client.post(
            reverse("hub_guild_announcement_propose_edit", args=[announcement.pk]),
            data=_payload(guild.pk, title="Revised", body="Now with the time."),
        )
        assert post_resp.status_code == 302
        announcement.refresh_from_db()
        assert announcement.moderation_state == GuildAnnouncement.ModerationState.PENDING

    def it_still_lets_a_lead_decide_a_pending_proposal_for_a_since_disabled_guild(client: Client):
        # Disabling suggestions suppresses NEW proposals only; pending ones stay decidable.
        lead = _member("sg6lead")
        guild = GuildFactory(guild_lead=lead.member, allow_member_announcement_suggestions=False)
        announcement = GuildAnnouncementFactory(guild=guild, pending=True, title="Still Decidable")
        client.login(username="sg6lead", password="pass")
        resp = client.get(reverse("hub_guild_announcement_review_queue"))
        assert resp.status_code == 200
        assert announcement.title.encode() in resp.content
