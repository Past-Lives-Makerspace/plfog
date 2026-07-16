"""Specs for the ``/info`` slash command handler (membership.discord_commands)."""

from __future__ import annotations

import pytest

from membership.discord_commands import INFO, _info
from tests.membership.factories import (
    GuildFactory,
    GuildFAQItemFactory,
    GuildLinkFactory,
    GuildStaffMembershipFactory,
    MemberFactory,
)

pytestmark = pytest.mark.django_db


def _by_option(name: str) -> dict:
    return _info({"data": {"options": [{"name": "guild", "value": name}]}}, None)


def _field_names(result: dict) -> list[str]:
    return [f["name"] for f in result["data"]["embeds"][0]["fields"]]


def _field(result: dict, name: str) -> str:
    return next(f["value"] for f in result["data"]["embeds"][0]["fields"] if f["name"] == name)


def describe_info_command_definition():
    def it_is_public_ungated_immediate_with_a_guild_option():
        assert INFO.name == "info"
        assert (INFO.requires_link, INFO.ephemeral, INFO.defer) == (False, False, False)
        assert [o["name"] for o in INFO.options] == ["guild"]


def describe_info():
    def it_renders_a_public_embed_titled_and_linked_to_the_guild_page(settings):
        settings.MEMBER_BASE_URL = "https://members.example"
        guild = GuildFactory(name="Blacksmithing", about="We forge things.")

        result = _by_option("Blacksmithing")

        assert result["data"]["flags"] == 0  # public, not ephemeral
        embed = result["data"]["embeds"][0]
        assert embed["title"] == "Blacksmithing"
        assert embed["url"] == f"https://members.example/guilds/{guild.slug}/"

    def it_resolves_the_guild_from_the_channel_map():
        GuildFactory(name="Fibers", about="Yarn and looms.", discord_channel_id="chan-fibers")
        result = _info({"channel_id": "chan-fibers", "data": {"options": []}}, None)
        assert result["data"]["embeds"][0]["title"] == "Fibers"

    def describe_when_no_guild_is_resolved():
        def it_returns_the_which_guild_reply(settings):
            GuildFactory(name="Ceramics")
            result = _info({"channel_id": "unknown", "data": {}}, None)
            assert result["data"]["flags"] == 64
            assert "Which guild?" in result["data"]["content"]

    def describe_an_inactive_guild():
        def it_is_treated_as_not_found():
            GuildFactory(name="Dormant Guild", is_active=False, about="Was fun once.")
            result = _by_option("Dormant Guild")
            assert result["data"]["flags"] == 64
            assert "Which guild?" in result["data"]["content"]

    def describe_field_guards():
        def it_shows_about_and_rules_when_set():
            GuildFactory(name="G1", about="About text.", essential_rules="Wear PPE.")
            names = _field_names(_by_option("G1"))
            assert "About" in names
            assert "Essential rules" in names

        def it_omits_rules_when_unset():
            GuildFactory(name="G2", about="Only about.")
            assert "Essential rules" not in _field_names(_by_option("G2"))

        def it_shows_faq_when_present():
            guild = GuildFactory(name="G3", about="x")
            GuildFAQItemFactory(guild=guild, question="How do I join?", answer="Come to a meeting.")
            assert "FAQ" in _field_names(_by_option("G3"))

        def it_omits_faq_when_absent():
            GuildFactory(name="G4", about="x")
            assert "FAQ" not in _field_names(_by_option("G4"))

        def it_shows_links_including_discord():
            guild = GuildFactory(name="G5", about="x", discord_url="https://discord.gg/abc")
            GuildLinkFactory(guild=guild, label="Handbook", url="https://example.com/handbook")
            value = _field(_by_option("G5"), "Links")
            assert "Handbook" in value
            assert "Discord" in value

        def it_shows_staff_lead_first():
            lead = MemberFactory(full_legal_name="Ada Lead")
            guild = GuildFactory(name="G6", about="x", guild_lead=lead)
            GuildStaffMembershipFactory(guild=guild, member=MemberFactory(full_legal_name="Boone Staff"))
            value = _field(_by_option("G6"), "Staff")
            assert "Guild Lead" in value

        def it_omits_staff_and_links_when_absent():
            GuildFactory(name="G7", about="x")
            names = _field_names(_by_option("G7"))
            assert "Staff" not in names
            assert "Links" not in names

    def describe_next_meeting():
        def it_shows_tba_when_forced():
            GuildFactory(name="G8", about="x", meeting_is_tba=True)
            assert "TBA" in _field(_by_option("G8"), "Next meeting")

        def it_uses_the_free_text_schedule_as_a_fallback():
            GuildFactory(name="G9", about="x", meeting_schedule="Tuesdays 6pm, Studio B")
            assert "Tuesdays 6pm" in _field(_by_option("G9"), "Next meeting")

    def describe_truncation():
        def it_trims_a_long_about_with_a_read_more_tail():
            GuildFactory(name="G10", about="x" * 2000)
            value = _field(_by_option("G10"), "About")
            assert len(value) <= 1024
            assert value.endswith("…more on the guild page")

    def describe_a_guild_with_no_content():
        def it_returns_the_hasnt_filled_in_reply():
            GuildFactory(name="Empty Guild")
            result = _by_option("Empty Guild")
            assert result["data"]["flags"] == 64
            assert "hasn't filled in its page yet" in result["data"]["content"]

    def it_ends_with_a_full_page_cta_field():
        GuildFactory(name="G11", about="x")
        assert "Full page" in _field_names(_by_option("G11"))
