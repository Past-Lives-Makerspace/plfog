"""Specs for the hub ``/create-announcement`` slash command + its confirm/cancel component.

The command posts an announcement to Discord AND the in-app bell — never email (the email
fan-out has no rate cap, so v1 is Discord + in-app only, ``send_email=False`` throughout).
Authority mirrors the hub composer: a SITE announcement needs ``is_fog_admin``; a GUILD one
needs ``can_edit_guild``. Any ``@here`` / ``@everyone`` / ``@role`` ping is gated behind a
two-step EPHEMERAL confirm — the slash reply previews with Confirm / Cancel buttons and the
post fires ONLY on the button click.

Discord HTTP is mocked at ``core.events.discord.post_embed`` (asserting which webhook + which
ping rode the Message); one end-to-end case leaves it real and mocks the raw webhook with
``respx`` to prove the ``<@&id>`` role ping + its ``allowed_mentions`` roles gate.
"""

from __future__ import annotations

import json
import logging
import types
from unittest import mock

import httpx
import pytest
import respx
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.utils import timezone
from factory.django import mute_signals

from core.events.channels import Message
from core.events.discord import build_embed_payload
from core.models import Notification, SiteConfiguration
from hub.discord_commands import (
    CREATE_ANNOUNCEMENT,
    _announce_audience_choices,
    _announce_channel,
    _announce_channel_label,
    _announce_config_missing_text,
    _announce_mention_literal,
    _announce_mention_option,
    _announce_message_option,
    _announce_ping_display,
    _announce_post_blocker,
    _announce_split_message,
    _create_announcement,
    _create_announcement_component,
    _create_announcement_options,
)
from membership.models import AnnouncementDraft, GuildAnnouncement
from tests.membership.factories import GuildFactory, GuildMembershipFactory, MemberFactory

pytestmark = pytest.mark.django_db

_GEN_WEBHOOK = "https://discord.com/api/webhooks/1/general"
_GUILD_WEBHOOK = "https://discord.com/api/webhooks/2/guild"


# --- Fixtures / helpers -------------------------------------------------------


def _link(member):
    """Attach a linked, signed-in User to ``member`` so it is a real broadcast recipient + author."""
    with mute_signals(post_save):
        user = User.objects.create_user(
            username=f"u{member.pk}", email=f"u{member.pk}@x.com", last_login=timezone.now()
        )
    member.user = user
    member.save(update_fields=["user"])
    return member


def _admin(**kw):
    """A linked fog admin — can post site-wide."""
    from membership.models import Member

    return _link(MemberFactory(fog_role=Member.FogRole.ADMIN, **kw))


def _plain(**kw):
    """A linked plain member — can post nothing on their own."""
    return _link(MemberFactory(**kw))


def _general_webhook():
    config = SiteConfiguration.load()
    config.discord_general_webhook_url = _GEN_WEBHOOK
    config.save()
    return config


def _slash(*, message="Big news", audience="site", mention=None):
    options = [{"name": "message", "value": message}, {"name": "audience", "value": audience}]
    if mention is not None:
        options.append({"name": "mention", "value": mention})
    return {"data": {"name": "create-announcement", "options": options}}


def _component(custom_id):
    return {"data": {"custom_id": custom_id}}


@pytest.fixture
def post_spy(monkeypatch):
    """Patch the Discord embed poster so no HTTP fires; capture (webhook, Message) per call."""
    spy = mock.MagicMock(name="post_embed", return_value=True)
    monkeypatch.setattr("core.events.discord.post_embed", spy)
    return spy


def _posted(post_spy):
    return [(call.args[0], call.args[1]) for call in post_spy.call_args_list]


# --- build_embed_payload: the NEW role-mention gate ---------------------------


def describe_build_embed_payload_role_mentions():
    def it_gates_a_single_role_ping_with_allowed_mentions_roles():
        payload = build_embed_payload(Message(title="T", body="B", discord_mention="<@&123>"))
        assert payload["content"] == "<@&123>"
        assert payload["allowed_mentions"] == {"parse": [], "roles": ["123"]}

    def it_gates_every_role_id_in_a_multi_role_ping():
        payload = build_embed_payload(Message(title="T", body="B", discord_mention="<@&11> <@&22>"))
        assert payload["allowed_mentions"] == {"parse": [], "roles": ["11", "22"]}

    def it_leaves_here_and_everyone_on_the_parse_everyone_gate():
        assert build_embed_payload(Message(title="T", body="B", discord_mention="@here"))["allowed_mentions"] == {
            "parse": ["everyone"]
        }
        assert build_embed_payload(Message(title="T", body="B", discord_mention="@everyone"))["allowed_mentions"] == {
            "parse": ["everyone"]
        }


# --- Pure helpers -------------------------------------------------------------


def describe_announce_helpers():
    def describe_channel():
        def it_routes_site_to_general_and_guild_to_the_guild_channel():
            assert _announce_channel(True) == GuildAnnouncement.DiscordChannel.GENERAL
            assert _announce_channel(False) == GuildAnnouncement.DiscordChannel.GUILD

    def describe_channel_label():
        def it_names_general_for_site():
            assert _announce_channel_label(True, None) == "#general-chat"

        def it_names_the_guild_channel_for_a_guild():
            guild = types.SimpleNamespace(name="Forge")
            assert _announce_channel_label(False, guild) == "the Forge Discord channel"

    def describe_config_missing_text():
        def it_explains_the_missing_general_webhook():
            assert "#general Discord webhook" in _announce_config_missing_text(True, None)

        def it_explains_the_missing_guild_webhook():
            guild = types.SimpleNamespace(name="Forge")
            assert "Forge doesn't have a Discord channel webhook" in _announce_config_missing_text(False, guild)

    def describe_split_message():
        def it_uses_the_first_line_as_the_headline_and_keeps_the_full_body():
            title, body = _announce_split_message("Headline here\nmore detail\nand more")
            assert title == "Headline here"
            assert body == "Headline here\nmore detail\nand more"

        def it_truncates_a_very_long_first_line_for_the_headline():
            title, _body = _announce_split_message("x" * 200)
            assert len(title) <= 120
            assert title.endswith("…")

    def describe_mention_literal():
        def it_maps_none_to_empty():
            assert _announce_mention_literal("none", None) == ""

        def it_maps_here_and_everyone_to_their_literals():
            assert _announce_mention_literal("here", None) == "@here"
            assert _announce_mention_literal("everyone", None) == "@everyone"

        def it_expands_every_role_id_for_a_guild_role_ping():
            guild = types.SimpleNamespace(discord_role_ids=["7", "8"])
            assert _announce_mention_literal("role", guild) == "<@&7> <@&8>"

        def it_returns_empty_for_a_role_ping_with_no_guild():
            # Defensive: the blocker prevents site+role reaching a post, but the branch is guarded.
            assert _announce_mention_literal("role", None) == ""

    def describe_ping_display():
        def it_wraps_every_ping_token_in_backticks_so_the_preview_never_pings():
            assert _announce_ping_display("here").startswith("`@here`")
            assert _announce_ping_display("everyone") == "`@everyone`"
            assert "`@role`" in _announce_ping_display("role")


# --- _announce_post_blocker (the shared authority + config gate) --------------


def describe_announce_post_blocker():
    def describe_site_audience():
        def it_blocks_a_non_admin():
            msg = _announce_post_blocker(_plain(), is_site=True, guild=None, mention="none")
            assert "admins can post a site-wide" in msg

        def it_blocks_a_role_ping_on_a_site_audience():
            _general_webhook()
            msg = _announce_post_blocker(_admin(), is_site=True, guild=None, mention="role")
            assert "@role ping only works for a guild" in msg

        def it_flags_a_missing_general_webhook():
            msg = _announce_post_blocker(_admin(), is_site=True, guild=None, mention="none")
            assert "#general Discord webhook" in msg

        def it_clears_an_admin_with_a_configured_webhook():
            _general_webhook()
            assert _announce_post_blocker(_admin(), is_site=True, guild=None, mention="everyone") is None

    def describe_guild_audience():
        def it_blocks_a_member_who_cannot_edit_the_guild():
            guild = GuildFactory(discord_webhook_url=_GUILD_WEBHOOK)
            msg = _announce_post_blocker(_plain(), is_site=False, guild=guild, mention="none")
            assert "not a lead or staff for" in msg
            assert "propose one on the hub" in msg

        def it_blocks_a_role_ping_when_the_guild_has_no_role_ids():
            member = _plain()
            guild = GuildFactory(guild_lead=member, discord_webhook_url=_GUILD_WEBHOOK, discord_role_ids=[])
            msg = _announce_post_blocker(member, is_site=False, guild=guild, mention="role")
            assert "doesn't have a Discord @role" in msg

        def it_flags_a_missing_guild_webhook():
            member = _plain()
            guild = GuildFactory(guild_lead=member, discord_webhook_url="")
            msg = _announce_post_blocker(member, is_site=False, guild=guild, mention="none")
            assert "doesn't have a Discord channel webhook" in msg

        def it_clears_an_editor_with_a_configured_role_ping():
            member = _plain()
            guild = GuildFactory(guild_lead=member, discord_webhook_url=_GUILD_WEBHOOK, discord_role_ids=["9"])
            assert _announce_post_blocker(member, is_site=False, guild=guild, mention="role") is None

        def it_clears_an_editor_for_a_plain_guild_post():
            member = _plain()
            guild = GuildFactory(guild_lead=member, discord_webhook_url=_GUILD_WEBHOOK)
            assert _announce_post_blocker(member, is_site=False, guild=guild, mention="none") is None


# --- Slash handler: validation + no-ping immediate posts ----------------------


def describe_create_announcement_validation():
    def it_asks_for_a_message_when_blank(post_spy):
        result = _create_announcement(_slash(message="   "), _admin())
        assert "Add a message" in result["data"]["content"]
        assert result["data"]["flags"] == 64
        post_spy.assert_not_called()

    def it_rejects_an_unknown_ping_option(post_spy):
        result = _create_announcement(_slash(mention="loud"), _admin())
        assert "didn't recognize that ping" in result["data"]["content"]
        post_spy.assert_not_called()

    def it_reports_an_unresolvable_guild_audience(post_spy):
        result = _create_announcement(_slash(audience="ghost-guild"), _plain())
        assert "couldn't find that audience" in result["data"]["content"]
        post_spy.assert_not_called()

    def it_stops_a_member_whose_account_is_not_fully_linked(post_spy):
        from membership.models import Member

        unlinked = MemberFactory(fog_role=Member.FogRole.ADMIN)  # discord id but no user
        result = _create_announcement(_slash(), unlinked)
        assert "isn't fully connected" in result["data"]["content"]
        post_spy.assert_not_called()

    def it_bounces_a_non_admin_off_a_site_announcement(post_spy):
        _general_webhook()
        result = _create_announcement(_slash(audience="site"), _plain())
        assert "admins can post a site-wide" in result["data"]["content"]
        post_spy.assert_not_called()
        assert not Notification.objects.exists()

    def it_points_a_non_editor_at_the_hub_for_a_guild_announcement(post_spy):
        guild = GuildFactory(discord_webhook_url=_GUILD_WEBHOOK)
        result = _create_announcement(_slash(audience=guild.slug), _plain())
        assert "propose one on the hub" in result["data"]["content"]
        post_spy.assert_not_called()
        assert GuildAnnouncement.objects.count() == 0


def describe_create_announcement_no_ping_posts():
    def it_posts_a_site_announcement_to_general_and_writes_the_bell(post_spy):
        _general_webhook()
        admin = _admin()
        reader = _link(MemberFactory())
        result = _create_announcement(_slash(message="Doors open Friday", audience="site"), admin)
        assert "Posted to #general-chat" in result["data"]["content"]
        webhooks = [hook for hook, _msg in _posted(post_spy)]
        assert webhooks == [_GEN_WEBHOOK]
        # No ping rides a plain post.
        assert _posted(post_spy)[0][1].discord_mention == ""
        assert Notification.objects.filter(user=reader.user, trigger="site_announcement").exists()

    def it_posts_a_guild_announcement_and_materializes_the_published_post(post_spy):
        member = _plain()
        guild = GuildFactory(guild_lead=member, discord_webhook_url=_GUILD_WEBHOOK)
        result = _create_announcement(_slash(message="Kiln firing Sat", audience=guild.slug), member)
        assert f"the {guild.name} Discord channel" in result["data"]["content"]
        announcement = GuildAnnouncement.objects.get(guild=guild)
        assert announcement.title == "Kiln firing Sat"
        assert announcement.send_email is False
        assert announcement.moderation_state == GuildAnnouncement.ModerationState.PUBLISHED
        assert [hook for hook, _msg in _posted(post_spy)] == [_GUILD_WEBHOOK]

    def it_does_not_persist_a_draft_for_a_no_ping_post(post_spy):
        _general_webhook()
        _create_announcement(_slash(audience="site"), _admin())
        assert AnnouncementDraft.objects.count() == 0


# --- Slash handler: the two-step confirm preview ------------------------------


def describe_create_announcement_ping_preview():
    def it_previews_without_posting_and_persists_a_draft(post_spy):
        _general_webhook()
        admin = _admin()
        result = _create_announcement(_slash(message="Party!", audience="site", mention="everyone"), admin)
        assert "please confirm" in result["data"]["content"]
        assert "`@everyone`" in result["data"]["content"]
        post_spy.assert_not_called()  # nothing posts at preview time
        draft = AnnouncementDraft.objects.get(author=admin.user)
        assert draft.audience == AnnouncementDraft.Audience.SITE
        assert draft.send_email is False
        assert draft.sent_at is None

    def it_offers_confirm_and_cancel_buttons_carrying_the_draft_and_ping(post_spy):
        _general_webhook()
        admin = _admin()
        result = _create_announcement(_slash(audience="site", mention="here"), admin)
        draft = AnnouncementDraft.objects.get(author=admin.user)
        row = result["data"]["components"][0]["components"]
        assert row[0]["custom_id"] == f"announce:confirm:{draft.pk}:here"
        assert row[0]["style"] == 3  # success (Confirm & post)
        assert row[1]["custom_id"] == f"announce:cancel:{draft.pk}:here"
        assert row[1]["style"] == 4  # danger (Cancel)

    def it_counts_a_single_guild_member_in_the_singular(post_spy):
        member = _plain()
        guild = GuildFactory(guild_lead=member, discord_webhook_url=_GUILD_WEBHOOK, discord_role_ids=["9"])
        GuildMembershipFactory(guild=guild, member=_link(MemberFactory()))
        result = _create_announcement(_slash(audience=guild.slug, mention="role"), member)
        assert "about 1 member will be notified" in result["data"]["content"]
        assert "the guild's `@role`" in result["data"]["content"]

    def it_counts_multiple_guild_members_in_the_plural(post_spy):
        member = _plain()
        guild = GuildFactory(guild_lead=member, discord_webhook_url=_GUILD_WEBHOOK, discord_role_ids=["9"])
        GuildMembershipFactory(guild=guild, member=_link(MemberFactory()))
        GuildMembershipFactory(guild=guild, member=_link(MemberFactory()))
        result = _create_announcement(_slash(audience=guild.slug, mention="everyone"), member)
        assert "about 2 members will be notified" in result["data"]["content"]


# --- Component handler: confirm / cancel --------------------------------------


def _draft_for(member, *, audience=AnnouncementDraft.Audience.SITE, guild=None):
    return AnnouncementDraft.objects.create(
        author=member.user,
        audience=audience,
        guild=guild,
        title="Headline",
        body="Headline\nbody",
        send_email=False,
        discord_channel=(
            GuildAnnouncement.DiscordChannel.GENERAL
            if audience == AnnouncementDraft.Audience.SITE
            else GuildAnnouncement.DiscordChannel.GUILD
        ),
    )


def describe_create_announcement_component_validation():
    def it_errors_on_a_custom_id_with_the_wrong_shape(post_spy):
        result = _create_announcement_component(_component("announce:confirm:5"), _admin())
        assert "went wrong" in result["data"]["content"]

    def it_errors_on_an_unknown_action(post_spy):
        result = _create_announcement_component(_component("announce:bogus:5:everyone"), _admin())
        assert "went wrong" in result["data"]["content"]

    def it_errors_on_a_non_numeric_draft_id(post_spy):
        result = _create_announcement_component(_component("announce:confirm:abc:everyone"), _admin())
        assert "went wrong" in result["data"]["content"]

    def it_errors_on_an_unknown_ping_token(post_spy):
        result = _create_announcement_component(_component("announce:confirm:5:loud"), _admin())
        assert "went wrong" in result["data"]["content"]

    def it_reports_an_expired_or_already_posted_preview(post_spy):
        result = _create_announcement_component(_component("announce:confirm:999999:everyone"), _admin())
        assert "expired or was already posted" in result["data"]["content"]
        post_spy.assert_not_called()


def describe_create_announcement_component_cancel():
    def it_deletes_the_draft_and_posts_nothing(post_spy):
        admin = _admin()
        draft = _draft_for(admin)
        result = _create_announcement_component(_component(f"announce:cancel:{draft.pk}:everyone"), admin)
        assert "Cancelled" in result["data"]["content"]
        assert result["type"] == 7  # UPDATE_MESSAGE — the preview is replaced in place
        assert not AnnouncementDraft.objects.filter(pk=draft.pk).exists()
        post_spy.assert_not_called()


def describe_create_announcement_component_confirm():
    def it_posts_a_site_ping_and_marks_the_draft_sent(post_spy):
        _general_webhook()
        admin = _admin()
        draft = _draft_for(admin)
        result = _create_announcement_component(_component(f"announce:confirm:{draft.pk}:everyone"), admin)
        assert "Posted to #general-chat" in result["data"]["content"]
        assert result["type"] == 7
        hook, message = _posted(post_spy)[0]
        assert hook == _GEN_WEBHOOK
        assert message.discord_mention == "@everyone"
        draft.refresh_from_db()
        assert draft.sent_at is not None

    def it_posts_a_guild_role_ping_with_the_role_literal(post_spy):
        member = _plain()
        guild = GuildFactory(guild_lead=member, discord_webhook_url=_GUILD_WEBHOOK, discord_role_ids=["55", "66"])
        draft = _draft_for(member, audience=AnnouncementDraft.Audience.GUILD, guild=guild)
        _create_announcement_component(_component(f"announce:confirm:{draft.pk}:role"), member)
        hook, message = _posted(post_spy)[0]
        assert hook == _GUILD_WEBHOOK
        assert message.discord_mention == "<@&55> <@&66>"
        assert GuildAnnouncement.objects.filter(guild=guild, title="Headline").exists()

    def it_will_not_double_post_once_the_draft_is_claimed(post_spy):
        _general_webhook()
        admin = _admin()
        draft = _draft_for(admin)
        first = _create_announcement_component(_component(f"announce:confirm:{draft.pk}:everyone"), admin)
        second = _create_announcement_component(_component(f"announce:confirm:{draft.pk}:everyone"), admin)
        assert "Posted to" in first["data"]["content"]
        assert "expired or was already posted" in second["data"]["content"]
        assert post_spy.call_count == 1

    def it_re_blocks_a_confirm_when_authority_was_revoked(post_spy):
        _general_webhook()
        # A draft authored by someone who is no longer an admin: build the draft, then
        # confirm as a plain member (the ownership filter uses author, so author == clicker).
        member = _plain()
        draft = _draft_for(member)  # SITE draft, but this member is not an admin
        result = _create_announcement_component(_component(f"announce:confirm:{draft.pk}:everyone"), member)
        assert "admins can post a site-wide" in result["data"]["content"]
        assert not AnnouncementDraft.objects.filter(pk=draft.pk).exists()  # cleaned up
        post_spy.assert_not_called()


# --- End-to-end: the raw webhook payload for a guild role ping -----------------


def describe_create_announcement_role_ping_end_to_end():
    @respx.mock
    def it_posts_the_role_mention_with_the_allowed_mentions_roles_gate():
        member = _plain()
        guild = GuildFactory(guild_lead=member, discord_webhook_url=_GUILD_WEBHOOK, discord_role_ids=["777"])
        draft = _draft_for(member, audience=AnnouncementDraft.Audience.GUILD, guild=guild)
        route = respx.post(_GUILD_WEBHOOK).mock(return_value=httpx.Response(204))

        _create_announcement_component(_component(f"announce:confirm:{draft.pk}:role"), member)

        assert route.called
        payload = json.loads(route.calls.last.request.content)
        assert payload["content"] == "<@&777>"
        assert payload["allowed_mentions"] == {"parse": [], "roles": ["777"]}


# --- Option builders + registration -------------------------------------------


def describe_create_announcement_options():
    def it_leads_the_audience_dropdown_with_general_then_one_choice_per_guild():
        GuildFactory(name="Alpha")
        GuildFactory(name="Beta")
        option = _announce_audience_choices()
        assert option["name"] == "audience"
        assert option["required"] is True
        assert option["choices"][0] == {"name": "Everyone (site-wide)", "value": "site"}
        assert {c["value"] for c in option["choices"][1:]} == {"alpha", "beta"}

    def it_caps_the_audience_choices_at_25_and_logs_the_overflow(caplog):
        for i in range(30):
            GuildFactory(name=f"Guild {i:02d}")
        with caplog.at_level(logging.WARNING):
            option = _announce_audience_choices()
        assert len(option["choices"]) == 25
        assert "dropped from the picker" in caplog.text

    def it_always_ships_the_general_choice_even_with_no_guilds():
        option = _announce_audience_choices()
        assert option["choices"] == [{"name": "Everyone (site-wide)", "value": "site"}]

    def it_offers_the_four_ping_choices_as_an_optional_option():
        option = _announce_mention_option()
        assert option["required"] is False
        assert {c["value"] for c in option["choices"]} == {"none", "here", "everyone", "role"}

    def it_marks_the_message_option_required():
        assert _announce_message_option()["required"] is True

    def it_orders_the_options_message_then_audience_then_mention():
        names = [o["name"] for o in _create_announcement_options()]
        assert names == ["message", "audience", "mention"]

    def it_serializes_within_discords_option_and_description_limits():
        # Discord rejects the whole bulk PUT if any description > 100 chars or a required
        # option follows an optional one — validate the whole serialized command shape here.
        api = CREATE_ANNOUNCEMENT.to_api_dict()
        assert len(api["description"]) <= 100
        requireds = [bool(o.get("required")) for o in api["options"]]
        assert requireds == sorted(requireds, reverse=True)  # all required options come first
        for option in api["options"]:
            assert len(option["description"]) <= 100
            for choice in option.get("choices", []):
                assert len(choice["name"]) <= 100


def describe_CREATE_ANNOUNCEMENT_command():
    def it_is_registered_and_reachable_by_name():
        from core.events.discord_commands import all_commands

        assert "create-announcement" in [c.name for c in all_commands()]

    def it_requires_a_link_defers_and_is_ephemeral():
        assert CREATE_ANNOUNCEMENT.requires_link is True
        assert CREATE_ANNOUNCEMENT.defer is True
        assert CREATE_ANNOUNCEMENT.ephemeral is True
        assert CREATE_ANNOUNCEMENT.scope == "guild"

    def it_registers_the_confirm_component_handler():
        from core.events.discord_commands import dispatch_component

        # A well-formed but stale confirm click routes to our handler (not the unknown-prefix path).
        _admin(discord_user_id="disc-admin")  # creates the linked Member resolve_member will find
        request = types.SimpleNamespace()
        result = dispatch_component(
            {
                "data": {"custom_id": "announce:confirm:999999:everyone"},
                "member": {"user": {"id": "disc-admin"}},
            },
            request,
        )
        assert "expired or was already posted" in result["data"]["content"]
