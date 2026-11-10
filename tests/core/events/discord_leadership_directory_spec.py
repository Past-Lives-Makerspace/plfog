"""The #leadership-directory Discord post mirrors the Leadership Directory (#1149).

The renderer is pure; the sync edits FOG's stored messages in place, posts and deletes to
match, and sends nothing when the content hash is unchanged. All HTTP is mocked with respx.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
import pytest
import respx
from django.tasks import TaskResultStatus
from django.utils import timezone

from core.events import discord_leadership_directory as dld
from core.models import SiteConfiguration
from membership.models import EXAMPLE_GUILD_SLUG, GuildStaffMembership, LeadershipPage, LeadershipTab
from tests.membership.factories import (
    GuildFactory,
    GuildStaffMembershipFactory,
    LeadershipListingFactory,
    LeadershipRoleFactory,
    LeadershipTabFactory,
    MemberFactory,
)

pytestmark = pytest.mark.django_db

API = "https://discord.com/api/v10"
CHANNEL = "968637078738976868"
MESSAGES = f"{API}/channels/{CHANNEL}/messages"


def _people_tab() -> LeadershipTab:
    return LeadershipTab.objects.get(kind=LeadershipTab.Kind.PEOPLE, title="Leadership")


def _configure(**overrides: Any) -> SiteConfiguration:
    config = SiteConfiguration.load()
    config.discord_leadership_directory_channel_id = CHANNEL
    for name, value in overrides.items():
        setattr(config, name, value)
    config.save()
    return config


@pytest.fixture
def discord_on(settings):
    settings.DISCORD_BOT_TOKEN = "bot-tok"
    return _configure()


def _embeds(payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [embed for payload in payloads for embed in payload["embeds"]]


def _titled(payloads: list[dict[str, Any]], title: str) -> dict[str, Any]:
    return next(embed for embed in _embeds(payloads) if embed.get("title") == title)


def describe_render_directory_payloads():
    def it_renders_the_header_from_the_leadership_page():
        page = LeadershipPage.load()
        page.hero_title = "Who Runs This"
        page.hero_lead = "Ask any of us."
        page.save()
        header = dld.render_directory_payloads()[0]["embeds"][0]
        assert header["title"] == "🙋 Who Runs This"
        assert header["color"] == dld.HEADER_COLOR
        assert header["description"].startswith("Ask any of us.\nTap a name to message them on Discord.\n\n")
        assert "📖 **Full directory with photos:** " in header["description"]
        assert header["description"].endswith("/leadership/")

    def it_renders_a_people_tab_with_titles_and_emails():
        alex = MemberFactory(full_legal_name="Alex Smith", preferred_name="Alex", discord_user_id="123456")
        listing = LeadershipListingFactory(tab=_people_tab(), member=alex)
        LeadershipRoleFactory(listing=listing, title="Treasurer", email="treasurer@example.com")
        LeadershipRoleFactory(listing=listing, title="Lead Engineer", email="")
        embed = _titled(dld.render_directory_payloads(), "✨ Leadership")
        assert embed["color"] == dld.PEOPLE_COLOR
        assert embed["description"] == "**Alex** <@123456>\nTreasurer · Lead Engineer\n✉️ treasurer@example.com"

    def it_mentions_a_verified_account_and_ignores_the_typed_handle():
        alex = MemberFactory(preferred_name="Alex", discord_user_id="123456", discord_handle="alex_typed")
        LeadershipListingFactory(tab=_people_tab(), member=alex)
        payloads = dld.render_directory_payloads()
        assert _titled(payloads, "✨ Leadership")["description"] == "**Alex** <@123456>"
        assert dld.TAP_LINE + "\n" in payloads[0]["embeds"][0]["description"]

    def it_shows_a_public_typed_handle_as_plain_text_and_says_to_search_it():
        sam = MemberFactory(preferred_name="Sam", discord_user_id="", discord_handle=" @@sam_ typed ")
        LeadershipRoleFactory(listing=LeadershipListingFactory(tab=_people_tab(), member=sam), title="Secretary")
        payloads = dld.render_directory_payloads()
        assert _titled(payloads, "✨ Leadership")["description"] == "**Sam** @sam\\_typed\nSecretary"
        header = payloads[0]["embeds"][0]["description"]
        assert "Tap a name to message them on Discord, or search their handle.\n" in header

    def it_shows_only_the_name_when_the_handle_is_private():
        sam = MemberFactory(
            preferred_name="Sam", discord_handle="sam_typed", directory_visibility={"discord_handle": False}
        )
        LeadershipRoleFactory(listing=LeadershipListingFactory(tab=_people_tab(), member=sam), title="Secretary")
        payloads = dld.render_directory_payloads()
        assert _titled(payloads, "✨ Leadership")["description"] == "**Sam**\nSecretary"
        assert "sam" not in json.dumps(payloads).replace("Sam", "")
        assert "or search their handle" not in payloads[0]["embeds"][0]["description"]

    def it_shows_a_public_handle_for_a_guild_lead_too():
        GuildFactory(name="Glass Guild", guild_lead=MemberFactory(preferred_name="Robin", discord_handle="robin.g"))
        payloads = dld.render_directory_payloads()
        assert "**Robin** @robin.g" in _titled(payloads, "🛠️ Guild Leads")["description"]
        assert "or search their handle" in payloads[0]["embeds"][0]["description"]

    def it_leaves_out_unlisted_people_and_empty_people_tabs():
        LeadershipListingFactory(tab=_people_tab(), member=MemberFactory(preferred_name="Hidden"), is_listed=False)
        LeadershipTabFactory(title="Council")
        titles = [embed.get("title") for embed in _embeds(dld.render_directory_payloads())]
        assert "✨ Leadership" not in titles
        assert "✨ Council" not in titles

    def it_lists_active_guilds_by_name_with_lead_co_leads_and_the_no_lead_line():
        lead = MemberFactory(preferred_name="Robin", discord_user_id="777")
        co = MemberFactory(preferred_name="Kai")
        GuildFactory(name="Woodworking Guild", guild_lead=lead)
        GuildStaffMembershipFactory(guild=GuildFactory(name="Woodworking Guild"), member=co)
        GuildStaffMembershipFactory(
            guild=GuildFactory(name="Woodworking Guild"), member=lead, role=GuildStaffMembership.Role.CO_LEAD
        )
        GuildFactory(name="Ceramics Guild")
        GuildFactory(name="Zine Guild")
        GuildFactory(name="Asleep Guild", is_active=False)
        embed = _titled(dld.render_directory_payloads(), "🛠️ Guild Leads")
        assert embed["color"] == dld.GUILDS_COLOR
        assert embed["description"] == (
            "🏺 **Ceramics Guild**\n*Looking for a lead!*\n\n"
            "🪚 **Woodworking Guild**\n**Robin** <@777> & **Kai**\n\n"
            "🛠️ **Zine Guild**\n*Looking for a lead!*"
        )

    def it_never_lists_the_example_guild_even_when_the_demo_setting_is_on():
        _configure(display_demo_guild=True)
        GuildFactory(name="Cartographers Guild", slug=EXAMPLE_GUILD_SLUG, is_active=False)
        GuildFactory(name="Glass Guild")
        description = _titled(dld.render_directory_payloads(), "🛠️ Guild Leads")["description"]
        assert "Cartographers" not in description
        assert "Glass Guild" in description

    def it_follows_the_tab_order_and_titles():
        tab = LeadershipTab.objects.get(kind=LeadershipTab.Kind.GUILD_LEADS)
        tab.title = "Guild Leaders"
        tab.sort_order = 0
        tab.save()
        people = _people_tab()
        people.sort_order = 5
        people.save()
        GuildFactory(name="Tech Guild")
        LeadershipListingFactory(tab=people, member=MemberFactory(preferred_name="Pat"))
        titles = [embed.get("title") for embed in _embeds(dld.render_directory_payloads()) if embed.get("title")]
        assert titles[1:] == ["🛠️ Guild Leaders", "✨ Leadership"]

    def it_escapes_markdown_in_names():
        LeadershipListingFactory(tab=_people_tab(), member=MemberFactory(preferred_name="*star*_"))
        assert "**\\*star\\*\\_**" in _titled(dld.render_directory_payloads(), "✨ Leadership")["description"]

    def it_footers_the_last_embed_with_the_stored_content_date():
        _configure(discord_leadership_directory_updated_on=date(2026, 11, 9))
        GuildFactory(name="Tech Guild")
        embeds = _embeds(dld.render_directory_payloads())
        assert embeds[-1]["footer"] == {"text": "Updated November 9, 2026 · Kept current from the member app"}
        assert all("footer" not in embed for embed in embeds[:-1])

    def it_footers_today_in_pacific_when_no_date_is_stored(monkeypatch):
        # 2026-11-10 06:00 UTC is still November 9 in Portland.
        monkeypatch.setattr(timezone, "now", lambda: datetime(2026, 11, 10, 6, 0, tzinfo=UTC))
        footer = _embeds(dld.render_directory_payloads())[-1]["footer"]["text"]
        assert footer.startswith("Updated November 9, 2026")

    def it_leaves_the_footer_out_of_the_content_hash():
        embeds = dld.directory_embeds()
        assert all("footer" not in embed for embed in embeds)
        assert dld.content_hash(embeds) == dld.content_hash(dld.directory_embeds())

    def it_sends_no_mentions_on_every_message():
        for payload in dld.render_directory_payloads():
            assert payload["allowed_mentions"] == {"parse": []}


def describe_limits():
    def it_splits_sixty_guilds_with_long_lines_within_every_discord_limit():
        for number in range(60):
            lead = MemberFactory(preferred_name=f"Lead {number} " + "x" * 90, discord_user_id=str(10**17 + number))
            GuildFactory(name=f"Guild {number:02d} " + "y" * 80, guild_lead=lead)
            co = MemberFactory(preferred_name=f"Co {number} " + "z" * 90)
            GuildStaffMembershipFactory(guild=GuildFactory(name=f"Guild {number:02d} " + "y" * 80), member=co)
        for number in range(30):
            listing = LeadershipListingFactory(tab=_people_tab(), member=MemberFactory(preferred_name=f"P{number}"))
            for line in range(3):
                LeadershipRoleFactory(listing=listing, title=f"Role {line} " + "r" * 110, email=f"r{line}@example.com")
        payloads = dld.render_directory_payloads()
        assert len(payloads) > 1
        for payload in payloads:
            assert len(payload["embeds"]) <= dld.EMBEDS_PER_MESSAGE_MAX
            assert sum(dld._embed_size(embed) for embed in payload["embeds"]) <= dld.MESSAGE_TEXT_MAX
            assert all(len(embed["description"]) <= dld.EMBED_DESCRIPTION_MAX for embed in payload["embeds"])
        text = "\n".join(embed["description"] for embed in _embeds(payloads))
        for number in range(60):
            assert f"Guild {number:02d} " in text  # nothing cut off
        guild_embeds = [e for e in _embeds(payloads) if e["color"] == dld.GUILDS_COLOR]
        assert len(guild_embeds) > 1
        assert "title" in guild_embeds[0] and all("title" not in e for e in guild_embeds[1:])

    def it_starts_a_new_message_after_ten_embeds():
        section = dld.Section(title="T", color=1, entries=["a" * 4000] * 12)
        embeds = dld.section_embeds(section)
        assert len(embeds) == 12
        payloads = dld.pack_messages([{"description": "d", "color": 1}] * 23)
        assert [len(payload["embeds"]) for payload in payloads] == [10, 10, 3]

    def it_cuts_one_entry_longer_than_an_embed_by_lines():
        embeds = dld.section_embeds(dld.Section(title="T", color=1, entries=["a" * 3000 + "\n" + "b" * 3000]))
        assert [len(embed["description"]) for embed in embeds] == [3000, 3000]

    def it_cuts_one_line_longer_than_an_embed():
        embeds = dld.section_embeds(dld.Section(title="T", color=1, entries=["c" * 9000]))
        assert all(len(embed["description"]) <= dld.EMBED_DESCRIPTION_MAX for embed in embeds)
        assert "".join(embed["description"].replace("\n\n", "") for embed in embeds) == "c" * 9000


def _payload_count() -> int:
    return len(dld.render_directory_payloads())


def describe_sync_leadership_directory():
    @respx.mock
    def it_sends_nothing_without_a_channel(settings):
        settings.DISCORD_BOT_TOKEN = "bot-tok"
        dld.sync_leadership_directory.call()
        assert respx.calls.call_count == 0

    @respx.mock
    def it_sends_nothing_without_a_bot_token(settings):
        settings.DISCORD_BOT_TOKEN = ""
        _configure()
        dld.sync_leadership_directory.call()
        assert respx.calls.call_count == 0
        assert SiteConfiguration.load().discord_leadership_directory_message_ids == []

    @respx.mock
    def it_posts_a_fresh_set_and_stores_the_ids_and_hash(discord_on):
        post = respx.post(MESSAGES).mock(return_value=httpx.Response(200, json={"id": "501"}))
        dld.sync_leadership_directory.call()
        body = json.loads(post.calls[0].request.content)
        assert body["allowed_mentions"] == {"parse": []}
        assert body["embeds"][0]["title"] == "🙋 Leadership Directory"
        config = SiteConfiguration.load()
        assert config.discord_leadership_directory_message_ids == ["501"]
        assert config.discord_leadership_directory_hash == dld.content_hash(dld.directory_embeds())
        assert config.discord_leadership_directory_updated_on == timezone.localdate()

    @respx.mock
    def it_sends_no_write_when_the_hash_is_unchanged_and_the_messages_remain(discord_on):
        _configure(
            discord_leadership_directory_message_ids=["501"],
            discord_leadership_directory_hash=dld.content_hash(dld.directory_embeds()),
        )
        read = respx.get(f"{MESSAGES}/501").mock(return_value=httpx.Response(200, json={"id": "501"}))
        dld.sync_leadership_directory.call()
        dld.sync_leadership_directory.call()
        assert respx.calls.call_count == read.call_count == 2  # one read each run, no write

    @respx.mock
    def it_edits_the_stored_message_in_place_when_the_content_changed(discord_on):
        _configure(discord_leadership_directory_message_ids=["501"], discord_leadership_directory_hash="old")
        edit = respx.patch(f"{MESSAGES}/501").mock(return_value=httpx.Response(200, json={"id": "501"}))
        dld.sync_leadership_directory.call()
        assert edit.call_count == 1
        assert json.loads(edit.calls[0].request.content)["allowed_mentions"] == {"parse": []}
        assert respx.calls.call_count == 1
        assert SiteConfiguration.load().discord_leadership_directory_message_ids == ["501"]

    @respx.mock
    def it_posts_a_fresh_set_when_a_stored_message_is_unchanged_but_gone_and_keeps_its_date(discord_on):
        _configure(
            discord_leadership_directory_message_ids=["501"],
            discord_leadership_directory_hash=dld.content_hash(dld.directory_embeds()),
            discord_leadership_directory_updated_on=date(2026, 9, 30),
        )
        respx.get(f"{MESSAGES}/501").mock(return_value=httpx.Response(404))
        respx.patch(f"{MESSAGES}/501").mock(return_value=httpx.Response(404))
        respx.delete(f"{MESSAGES}/501").mock(return_value=httpx.Response(404))
        post = respx.post(MESSAGES).mock(return_value=httpx.Response(200, json={"id": "777"}))
        dld.sync_leadership_directory.call()
        config = SiteConfiguration.load()
        assert config.discord_leadership_directory_message_ids == ["777"]
        assert config.discord_leadership_directory_updated_on == date(2026, 9, 30)
        assert "Updated September 30, 2026" in post.calls[0].request.content.decode()

    @respx.mock
    def it_moves_the_date_to_today_when_the_content_changes(discord_on):
        _configure(
            discord_leadership_directory_message_ids=["501"],
            discord_leadership_directory_hash=dld.content_hash(dld.directory_embeds()),
            discord_leadership_directory_updated_on=date(2026, 9, 30),
        )
        GuildFactory(name="Glass Guild", guild_lead=MemberFactory(preferred_name="Robin"))
        edit = respx.patch(f"{MESSAGES}/501").mock(return_value=httpx.Response(200, json={}))
        dld.sync_leadership_directory.call()
        today = timezone.localdate()
        assert SiteConfiguration.load().discord_leadership_directory_updated_on == today
        assert f"Updated {today:%B} {today.day}, {today.year}" in edit.calls[0].request.content.decode()

    @respx.mock
    def it_keeps_the_date_on_a_forced_send_of_unchanged_content(discord_on):
        _configure(
            discord_leadership_directory_message_ids=["501"],
            discord_leadership_directory_hash="",
            discord_leadership_directory_updated_on=date(2026, 9, 30),
        )
        respx.patch(f"{MESSAGES}/501").mock(return_value=httpx.Response(200, json={}))
        dld.sync_leadership_directory.call()
        assert SiteConfiguration.load().discord_leadership_directory_updated_on == date(2026, 9, 30)

    @respx.mock
    def it_reposts_every_message_when_an_edit_finds_one_gone(discord_on):
        _configure(discord_leadership_directory_message_ids=["501", "502"], discord_leadership_directory_hash="old")
        respx.patch(f"{MESSAGES}/501").mock(return_value=httpx.Response(404))
        deleted = respx.delete(f"{MESSAGES}/502").mock(return_value=httpx.Response(204))
        gone = respx.delete(f"{MESSAGES}/501").mock(return_value=httpx.Response(404))
        respx.post(MESSAGES).mock(return_value=httpx.Response(200, json={"id": "900"}))
        dld.sync_leadership_directory.call()
        assert deleted.call_count == gone.call_count == 1
        assert SiteConfiguration.load().discord_leadership_directory_message_ids == ["900"]

    @respx.mock
    def it_posts_the_extra_when_the_post_grows(discord_on):
        for number in range(30):
            GuildFactory(name=f"Guild {number:02d} " + "y" * 200)
        assert _payload_count() == 2
        _configure(discord_leadership_directory_message_ids=["501"], discord_leadership_directory_hash="old")
        edit = respx.patch(f"{MESSAGES}/501").mock(return_value=httpx.Response(200, json={}))
        post = respx.post(MESSAGES).mock(return_value=httpx.Response(200, json={"id": "502"}))
        dld.sync_leadership_directory.call()
        assert edit.call_count == post.call_count == 1
        assert SiteConfiguration.load().discord_leadership_directory_message_ids == ["501", "502"]

    @respx.mock
    def it_deletes_the_surplus_when_the_post_shrinks(discord_on):
        _configure(
            discord_leadership_directory_message_ids=["501", "502", "503"], discord_leadership_directory_hash="old"
        )
        respx.patch(f"{MESSAGES}/501").mock(return_value=httpx.Response(200, json={}))
        surplus = [respx.delete(f"{MESSAGES}/{mid}").mock(return_value=httpx.Response(204)) for mid in ("502", "503")]
        dld.sync_leadership_directory.call()
        assert [route.call_count for route in surplus] == [1, 1]
        assert SiteConfiguration.load().discord_leadership_directory_message_ids == ["501"]

    @respx.mock
    def it_stores_what_exists_and_fails_loudly_when_discord_errors(discord_on):
        for number in range(60):
            GuildFactory(name=f"Guild {number:02d} " + "y" * 200)
        _configure(discord_leadership_directory_message_ids=["501"], discord_leadership_directory_hash="old")
        respx.patch(f"{MESSAGES}/501").mock(return_value=httpx.Response(200, json={}))
        respx.post(MESSAGES).mock(return_value=httpx.Response(500, text="boom"))
        with pytest.raises(dld.LeadershipDirectorySyncError):
            dld.sync_leadership_directory.call()
        config = SiteConfiguration.load()
        assert config.discord_leadership_directory_message_ids == ["501"]
        assert config.discord_leadership_directory_hash == ""

    @respx.mock
    def it_waits_out_one_rate_limit(discord_on, monkeypatch):
        monkeypatch.setattr("core.integrations.discord_channel.time.sleep", lambda seconds: None)
        respx.post(MESSAGES).mock(
            side_effect=[httpx.Response(429, headers={"Retry-After": "1"}), httpx.Response(200, json={"id": "9"})]
        )
        dld.sync_leadership_directory.call()
        assert SiteConfiguration.load().discord_leadership_directory_message_ids == ["9"]


def describe_directory_on_commit():
    @respx.mock
    def it_registers_one_sync_per_transaction(discord_on, django_capture_on_commit_callbacks):
        respx.post(MESSAGES).mock(return_value=httpx.Response(200, json={"id": "1"}))
        with django_capture_on_commit_callbacks(execute=True) as callbacks:
            dld.directory_on_commit()
            dld.directory_on_commit()
            dld.directory_on_commit()
        assert len([cb for cb in callbacks if isinstance(cb, dld._SyncWhenCommitted)]) == 1
        assert respx.calls.call_count == 1

    @respx.mock
    def it_enqueues_nothing_while_a_sync_waits_in_the_queue(discord_on, django_capture_on_commit_callbacks):
        from django_tasks_db.models import DBTaskResult

        DBTaskResult.objects.create(
            task_path=dld.sync_leadership_directory.module_path,
            status=TaskResultStatus.READY,
            args_kwargs={"args": [], "kwargs": {}},
            priority=0,
            queue_name="default",
            backend_name="default",
            run_after=timezone.now() + timedelta(seconds=10),
        )
        with django_capture_on_commit_callbacks(execute=True):
            dld.directory_on_commit()
        assert respx.calls.call_count == 0

    @respx.mock
    def it_enqueues_nothing_without_a_channel(settings, django_capture_on_commit_callbacks):
        settings.DISCORD_BOT_TOKEN = "bot-tok"
        with django_capture_on_commit_callbacks(execute=True):
            dld.directory_on_commit()
        assert respx.calls.call_count == 0

    def it_delays_the_sync_where_the_backend_can_wait(monkeypatch):
        backend = dld.sync_leadership_directory.get_backend()
        monkeypatch.setattr(type(backend), "supports_defer", True)
        task = dld._deferred_sync()
        assert task.run_after is not None
        assert timedelta(seconds=5) < task.run_after - timezone.now() <= timedelta(seconds=dld.COALESCE_SECONDS)


def _snowflake(at: datetime) -> str:
    return str((int(at.timestamp() * 1000) - 1420070400000) << 22)


def describe_wipe_channel():
    @respx.mock
    def it_bulk_deletes_recent_messages_and_deletes_old_ones_singly(discord_on):
        now = timezone.now()
        recent = [_snowflake(now - timedelta(days=1, minutes=n)) for n in range(103)]
        old = [_snowflake(now - timedelta(days=20, minutes=n)) for n in range(2)]
        bulk = respx.post(f"{MESSAGES}/bulk-delete").mock(return_value=httpx.Response(204))
        single = respx.delete(url__regex=rf"{MESSAGES}/\d+").mock(return_value=httpx.Response(204))
        assert dld.wipe_channel(CHANNEL, recent + old, now=now) == 105
        assert [len(json.loads(call.request.content)["messages"]) for call in bulk.calls] == [100, 3]
        assert {call.request.url.path.rsplit("/", 1)[1] for call in single.calls} == set(old)

    @respx.mock
    def it_deletes_a_lone_recent_message_singly(discord_on):
        now = timezone.now()
        lone = _snowflake(now - timedelta(hours=1))
        bulk = respx.post(f"{MESSAGES}/bulk-delete").mock(return_value=httpx.Response(204))
        single = respx.delete(f"{MESSAGES}/{lone}").mock(return_value=httpx.Response(204))
        dld.wipe_channel(CHANNEL, [lone], now=now)
        assert bulk.call_count == 0 and single.call_count == 1

    def it_reads_a_message_time_from_its_id():
        at = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
        assert abs(dld.message_time(_snowflake(at)) - at) < timedelta(seconds=1)
