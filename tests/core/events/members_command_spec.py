"""Specs for the ``/members`` slash command + its Prev/Next component handler.

Privacy parity is the heart of these specs: the command must show exactly what the app
member directory shows — the same visibility filter (no admin bypass) and the same
per-field ``is_public()`` gates — card by card, line by line.
"""

from __future__ import annotations

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from core.events.discord_commands import dispatch_component
from core.events.discord_interactions import error_reply
from membership.discord_commands import (
    MEMBERS,
    _members,
    _members_component,
    _members_options,
    _search_budget,
)
from membership.models import Member
from tests.membership.factories import (
    GuildFactory,
    GuildMembershipFactory,
    MemberContactFactory,
    MemberFactory,
    MemberSkillFactory,
    SkillFactory,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def invoker(linked_member):
    """The linked member running the command — hidden from the directory so they never
    pollute the roster a test builds (linked-only is dispatch's concern, not the handler's)."""
    return linked_member(show_in_directory=False, full_legal_name="Zzz Invoker")


def _invoke(member, *, guild: str | None = None, search: str | None = None) -> dict:
    options = []
    if guild is not None:
        options.append({"name": "guild", "value": guild})
    if search is not None:
        options.append({"name": "search", "value": search})
    return _members({"data": {"options": options}}, member)


def _click(member, custom_id: str) -> dict:
    return _members_component({"data": {"custom_id": custom_id}}, member)


def _embeds(result: dict) -> list[dict]:
    return result["data"]["embeds"]


def _cards(result: dict) -> list[dict]:
    return _embeds(result)[:-1]


def _footer_text(result: dict) -> str:
    return _embeds(result)[-1]["description"]


def _card_for(result: dict, member) -> dict:
    matches = [card for card in _cards(result) if card["title"] == member.display_name]
    assert len(matches) == 1, f"expected exactly one card titled {member.display_name!r}"
    return matches[0]


def _buttons(result: dict) -> list[dict]:
    return result["data"]["components"][0]["components"]


def _pager(result: dict) -> dict:
    """The Prev/Next buttons keyed by label, or {} when the pager is omitted."""
    return {b["label"]: b for b in _buttons(result) if b.get("style") == 2}


def describe_members_command_definition():
    def it_is_linked_only_ephemeral_immediate_and_guild_scoped():
        assert MEMBERS.name == "members"
        assert (MEMBERS.requires_link, MEMBERS.ephemeral, MEMBERS.defer, MEMBERS.scope) == (True, True, False, "guild")

    def it_offers_a_guild_dropdown_of_slugs_and_a_search_option():
        GuildFactory(name="Woodshop")
        GuildFactory(name="Fiber Arts")
        guild_option, search_option = _members_options()
        assert guild_option["name"] == "guild"
        assert guild_option["required"] is False
        assert guild_option["description"] == "Filter to one guild — omit to browse everyone."
        assert {c["value"] for c in guild_option["choices"]} == {"woodshop", "fiber-arts"}
        assert search_option == {
            "name": "search",
            "description": "Match a name or skill.",
            "type": 3,
            "required": False,
        }

    def it_caps_the_guild_choices_at_25():
        for n in range(26):
            GuildFactory(name=f"Cap Guild {n}")
        guild_option = _members_options()[0]
        assert len(guild_option["choices"]) == 25

    def it_ships_no_choices_key_when_there_are_no_active_guilds():
        assert "choices" not in _members_options()[0]


def describe_members_privacy():
    def it_omits_an_opted_out_member(invoker):
        listed = MemberFactory(full_legal_name="Listed Member")
        hidden = MemberFactory(full_legal_name="Hidden Member", show_in_directory=False)
        result = _invoke(invoker)
        titles = [card["title"] for card in _cards(result)]
        assert listed.display_name in titles
        assert hidden.display_name not in titles

    def it_shows_a_must_show_member_despite_their_opt_out(invoker):
        officer = MemberFactory(show_in_directory=False, fog_role=Member.FogRole.GUILD_OFFICER)
        assert officer.display_name in [card["title"] for card in _cards(_invoke(invoker))]

    def it_gives_an_admin_invoker_no_bypass(linked_member):
        admin = linked_member(fog_role=Member.FogRole.ADMIN)
        hidden = MemberFactory(full_legal_name="Hidden Member", show_in_directory=False)
        assert hidden.display_name not in [card["title"] for card in _cards(_invoke(admin))]

    def it_omits_each_hidden_field_while_the_rest_of_the_card_renders(invoker):
        member = MemberFactory(
            full_legal_name="Gated Member",
            phone="503-555-0100",
            discord_handle="gated_makes",
            pronouns=Member.Pronouns.SHE_HER,
            directory_visibility={"email": False, "phone": False, "discord_handle": False, "pronouns": False},
        )
        description = _card_for(_invoke(invoker), member)["description"]
        assert "✉️" not in description
        assert "📞" not in description
        assert "💬" not in description
        assert "she/her" not in description
        assert member.get_member_type_display() in description  # the card itself still renders

    def it_shows_public_contact_fields(invoker):
        member = MemberFactory(
            full_legal_name="Open Member",
            phone="503-555-0100",
            discord_handle="open_makes",
            pronouns=Member.Pronouns.SHE_HER,
        )
        description = _card_for(_invoke(invoker), member)["description"]
        assert f"✉️ {member.primary_email}" in description
        assert "📞 503-555-0100" in description
        assert "💬 open_makes" in description
        assert "she/her" in description

    def it_skips_prefer_not_to_share_pronouns_even_when_public(invoker):
        member = MemberFactory(full_legal_name="Quiet Member", pronouns=Member.Pronouns.PREFER_NOT)
        assert "prefer not to share" not in _card_for(_invoke(invoker), member)["description"]

    def it_hides_the_commissions_line_when_skills_are_hidden(invoker):
        # Web parity: the commissions block nests inside the skills gate on the directory card.
        member = MemberFactory(
            full_legal_name="Commission Member",
            open_for_commissions=True,
            commission_note="Custom furniture",
            directory_visibility={"skills": False},
        )
        MemberSkillFactory(member=member)
        description = _card_for(_invoke(invoker), member)["description"]
        assert "💼" not in description
        assert "🎨" not in description

    def it_shows_the_commissions_line_with_its_note_when_skills_are_public(invoker):
        member = MemberFactory(
            full_legal_name="Commission Member", open_for_commissions=True, commission_note="Custom furniture"
        )
        description = _card_for(_invoke(invoker), member)["description"]
        assert "💼 Open for commissions! — Custom furniture" in description

    def it_renders_only_directory_visible_custom_contacts(invoker):
        member = MemberFactory(full_legal_name="Contact Member")
        MemberContactFactory(member=member, label="Website", value="https://maya.example")
        MemberContactFactory(member=member, label="Secret", value="https://hidden.example", show_in_directory=False)
        description = _card_for(_invoke(invoker), member)["description"]
        assert "🔗 Website — https://maya.example" in description
        assert "Secret" not in description


def describe_members_cards():
    def it_orders_cards_by_full_legal_name(invoker):
        MemberFactory(full_legal_name="Beta Member")
        MemberFactory(full_legal_name="Alpha Member", preferred_name="Zed")
        titles = [card["title"] for card in _cards(_invoke(invoker))]
        assert titles == ["Zed", "Beta Member"]  # titles are display names, order is legal-name

    def it_builds_five_cards_plus_a_footer_on_a_full_page(invoker):
        for n in range(7):
            MemberFactory(full_legal_name=f"Member {n}")
        result = _invoke(invoker)
        assert len(_embeds(result)) == 6
        assert _footer_text(result).startswith("Page 1 of 2 · 7 members")
        assert "Edit what you share from the app — Settings → Directory." in _footer_text(result)

    def it_names_the_active_filters_in_the_footer(invoker):
        guild = GuildFactory(name="Woodshop")
        member = MemberFactory(full_legal_name="Joinery Member")
        GuildMembershipFactory(guild=guild, member=member)
        result = _invoke(invoker, guild="woodshop", search="joinery")
        assert "· Woodshop · “joinery”" in _footer_text(result)

    def it_caps_skills_at_six_with_a_more_count(invoker):
        member = MemberFactory(full_legal_name="Skilled Member")
        for n in range(8):
            MemberSkillFactory(member=member, skill=SkillFactory(name=f"Craft {n}"))
        description = _card_for(_invoke(invoker), member)["description"]
        assert "🎨 Skills: " in description
        assert ", +2 more" in description
        assert "Craft 7" not in description

    def it_excludes_unapproved_skills(invoker):
        from membership.models import Skill

        member = MemberFactory(full_legal_name="Pending Member")
        MemberSkillFactory(member=member, skill=SkillFactory(name="Unvetted Craft", status=Skill.Status.PENDING))
        assert "Unvetted Craft" not in _card_for(_invoke(invoker), member)["description"]

    def it_lists_guild_names(invoker):
        member = MemberFactory(full_legal_name="Guilded Member")
        GuildMembershipFactory(guild=GuildFactory(name="Woodshop"), member=member)
        GuildMembershipFactory(guild=GuildFactory(name="Fiber Arts"), member=member)
        assert "🛠️ Guilds: " in _card_for(_invoke(invoker), member)["description"]

    def it_thumbnails_an_absolute_public_photo_url(invoker, settings):
        settings.MEDIA_URL = "https://media.example/"
        member = MemberFactory(full_legal_name="Photo Member", profile_photo="members/profile/p.jpg")
        card = _card_for(_invoke(invoker), member)
        assert card["thumbnail"] == {"url": "https://media.example/members/profile/p.jpg"}

    def it_skips_the_thumbnail_for_a_relative_local_url(invoker, settings):
        settings.MEDIA_URL = "/media/"
        member = MemberFactory(full_legal_name="Photo Member", profile_photo="members/profile/p.jpg")
        assert "thumbnail" not in _card_for(_invoke(invoker), member)

    def it_skips_the_thumbnail_when_the_photo_is_private(invoker, settings):
        settings.MEDIA_URL = "https://media.example/"
        member = MemberFactory(
            full_legal_name="Photo Member",
            profile_photo="members/profile/p.jpg",
            directory_visibility={"profile_photo": False},
        )
        assert "thumbnail" not in _card_for(_invoke(invoker), member)

    def it_skips_the_thumbnail_when_there_is_no_photo(invoker):
        member = MemberFactory(full_legal_name="Plain Member")
        assert "thumbnail" not in _card_for(_invoke(invoker), member)

    def it_stays_under_the_message_budget_with_five_maximal_cards(invoker):
        for n in range(5):
            member = MemberFactory(
                full_legal_name=f"{n}" + "N" * 254,
                phone="503-555-0100",
                discord_handle="a-very-long-discord-handle-indeed",
                pronouns=Member.Pronouns.ALL_THREE,
                open_for_commissions=True,
                commission_note="c" * 280,
            )
            for s in range(15):
                MemberSkillFactory(member=member, skill=SkillFactory(name=f"Skill {n}-{s}" + "k" * 60))
            for c in range(4):
                MemberContactFactory(member=member, label=f"Contact {c}" + "l" * 80, value="v" * 255)
        result = _invoke(invoker)
        for card in _cards(result):
            assert len(card["title"]) <= 80
            assert len(card["description"]) <= 950
        total_chars = sum(len(e.get("title", "")) + len(e.get("description", "")) for e in _embeds(result))
        assert total_chars < 6000

    def it_builds_a_page_in_a_constant_number_of_queries(invoker):
        def _query_count() -> int:
            with CaptureQueriesContext(connection) as ctx:
                result = _invoke(invoker)
            assert result["type"] == 4
            return len(ctx)

        # The baseline member exercises every prefetch level (Django skips a nested
        # prefetch's follow-up query entirely when the parent level returns no rows).
        member = MemberFactory(full_legal_name="Solo Member")
        GuildMembershipFactory(guild=GuildFactory(name="Solo Guild"), member=member)
        MemberSkillFactory(member=member)
        MemberContactFactory(member=member)
        baseline = _query_count()

        for n in range(5):
            extra = MemberFactory(full_legal_name=f"Extra {n}")
            GuildMembershipFactory(guild=GuildFactory(name=f"Extra Guild {n}"), member=extra)
            MemberSkillFactory(member=extra)
            MemberContactFactory(member=extra)
        assert _query_count() == baseline  # zero queries per card beyond the page fetch


def describe_members_filters():
    def it_narrows_to_the_chosen_guilds_roster(invoker):
        woodshop = GuildFactory(name="Woodshop")
        insider = MemberFactory(full_legal_name="Insider Member")
        GuildMembershipFactory(guild=woodshop, member=insider)
        MemberFactory(full_legal_name="Outsider Member")
        titles = [card["title"] for card in _cards(_invoke(invoker, guild="woodshop"))]
        assert titles == [insider.display_name]

    def it_searches_names_and_approved_skills(invoker):
        from membership.models import Skill

        by_preferred = MemberFactory(full_legal_name="Alpha Member", preferred_name="Joinery Fan")
        by_legal = MemberFactory(full_legal_name="Joinery Member")
        by_skill = MemberFactory(full_legal_name="Skill Member")
        MemberSkillFactory(member=by_skill, skill=SkillFactory(name="Fine Joinery"))
        unapproved = MemberFactory(full_legal_name="Unapproved Member")
        MemberSkillFactory(member=unapproved, skill=SkillFactory(name="Rough Joinery", status=Skill.Status.PENDING))
        titles = [card["title"] for card in _cards(_invoke(invoker, search="joinery"))]
        assert set(titles) == {by_preferred.display_name, by_legal.display_name, by_skill.display_name}

    def it_nudges_on_an_unresolvable_guild_option(invoker):
        result = _invoke(invoker, guild="no-such-guild")
        assert "Which guild?" in result["data"]["content"]


def describe_members_pagination():
    def it_disables_prev_on_the_first_page_with_correct_targets(invoker):
        for n in range(7):
            MemberFactory(full_legal_name=f"Member {n}")
        pager = _pager(_invoke(invoker))
        assert pager["◀ Prev"]["disabled"] is True
        assert pager["Next ▶"] == {
            "type": 2,
            "style": 2,
            "label": "Next ▶",
            "custom_id": "members:2:-:",
            "disabled": False,
        }

    def it_disables_next_on_the_last_page(invoker):
        for n in range(7):
            MemberFactory(full_legal_name=f"Member {n}")
        pager = _pager(_click(invoker, "members:2:-:"))
        assert pager["◀ Prev"]["custom_id"] == "members:1:-:"
        assert pager["◀ Prev"]["disabled"] is False
        assert pager["Next ▶"]["custom_id"] == "members:3:-:"
        assert pager["Next ▶"]["disabled"] is True

    def it_omits_the_pager_on_a_single_page_but_keeps_the_link_button(invoker):
        MemberFactory(full_legal_name="Only Member")
        buttons = _buttons(_invoke(invoker))
        assert [b["style"] for b in buttons] == [5]
        assert buttons[0]["label"] == "Open the full directory"
        assert buttons[0]["url"].endswith("/members/")

    def it_encodes_the_guild_slug_in_the_custom_ids(invoker):
        guild = GuildFactory(name="Woodshop")
        for n in range(7):
            GuildMembershipFactory(guild=guild, member=MemberFactory(full_legal_name=f"Member {n}"))
        pager = _pager(_invoke(invoker, guild="woodshop"))
        assert pager["Next ▶"]["custom_id"] == "members:2:woodshop:"

    def it_truncates_a_long_search_once_so_every_page_runs_the_same_query(invoker):
        for n in range(7):
            MemberFactory(full_legal_name=f"Member {n} " + "s" * 60)
        long_search = "s" * 200
        first = _invoke(invoker, search=long_search)
        next_id = _pager(first)["Next ▶"]["custom_id"]
        assert next_id == "members:2:-:" + "s" * _search_budget("-")
        assert len(next_id) <= 100
        second = _click(invoker, next_id)
        # Both pages carry the same truncated query — same filter, same footer suffix.
        truncated = "“" + "s" * _search_budget("-") + "”"
        assert truncated in _footer_text(first)
        assert truncated in _footer_text(second)
        assert _footer_text(second).startswith("Page 2 of 2 · 7 members")


def describe_members_empty_states():
    def it_replies_with_the_empty_copy_and_the_link_button_on_slash(invoker):
        result = _invoke(invoker, search="nobody-matches-this")
        assert result["type"] == 4
        assert "embeds" not in result["data"]
        assert "No members match for **“nobody-matches-this”**" in result["data"]["content"]
        assert "run `/members` without the filters" in result["data"]["content"]
        assert [b["style"] for b in _buttons(result)] == [5]

    def it_names_the_guild_filter_in_the_empty_copy(invoker):
        GuildFactory(name="Woodshop")
        result = _invoke(invoker, guild="woodshop")
        assert "No members match in **Woodshop**." in result["data"]["content"]

    def it_updates_to_the_empty_state_when_the_roster_empties_between_clicks(invoker):
        result = _click(invoker, "members:2:-:")
        assert result["type"] == 7
        assert len(_embeds(result)) == 1
        assert "No members match." in _embeds(result)[0]["description"]
        assert [b["style"] for b in _buttons(result)] == [5]


def describe_members_component():
    def it_updates_in_place_with_the_requested_page(invoker):
        for n in range(7):
            MemberFactory(full_legal_name=f"Member {n}")
        result = _click(invoker, "members:2:-:")
        assert result["type"] == 7
        assert "flags" not in result["data"]
        assert [card["title"] for card in _cards(result)] == ["Member 5", "Member 6"]
        assert _footer_text(result).startswith("Page 2 of 2 · 7 members")

    def it_clamps_a_page_beyond_the_count_to_the_last_page(invoker):
        for n in range(7):
            MemberFactory(full_legal_name=f"Member {n}")
        result = _click(invoker, "members:9:-:")
        assert _footer_text(result).startswith("Page 2 of 2")

    @pytest.mark.parametrize("custom_id", ["members:x:-:", "members:0:-:", "members:2:-"])
    def it_error_replies_on_a_malformed_custom_id(invoker, custom_id):
        MemberFactory(full_legal_name="Present Member")
        assert _click(invoker, custom_id) == error_reply()

    def it_error_replies_when_the_guild_slug_no_longer_resolves(invoker):
        GuildFactory(name="Gone Guild", is_active=False)
        assert _click(invoker, "members:1:gone-guild:") == error_reply()
        assert _click(invoker, "members:1:never-existed:") == error_reply()

    def it_prompts_a_clicker_who_unlinked_since_invoking(rf):
        import membership.discord_commands  # noqa: F401  # ensures the "members" prefix is registered

        interaction = {"type": 3, "data": {"custom_id": "members:2:-:"}, "member": {"user": {"id": "000"}}}
        result = dispatch_component(interaction, rf.post("/"))
        assert result["type"] == 4  # a fresh ephemeral connect prompt, not an in-place update
        button = result["data"]["components"][0]["components"][0]
        assert button["url"].endswith("/discord/link/")
