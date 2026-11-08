"""End-to-end: Share and Invite on the guild page (#1137).

Chromium on Linux has no ``navigator.share``, so Share opens the Copy / Text / Email menu, every
item carrying the guild's public link. A guild lead presses Invite, the panel loads into the
modal, a search narrows the list, they tick a member and press Send Invites: the toast counts the
invite, the modal closes and one open invite exists, with nobody added to the guild. The invitee
then opens the guild page, sees the banner and taps Join, which joins them in place. Waits are on
what the page shows. Run with ``pytest -m e2e``, on PostgreSQL in CI.
"""

from __future__ import annotations

from urllib.parse import unquote

from django.contrib.auth.models import User
from django.urls import reverse
from playwright.sync_api import Page, expect

from membership.models import Guild, GuildInvite, GuildMembership, Member
from tests.membership.factories import GuildFactory, MembershipPlanFactory

LEAD_EMAIL = "invite-lead@example.com"
PICK_EMAIL = "invite-pick@example.com"


def _member(email: str, name: str) -> Member:
    MembershipPlanFactory()
    member = User.objects.create_user(username=email, email=email).member
    member.status = Member.Status.ACTIVE
    member.full_legal_name = name
    member.save(update_fields=["status", "full_legal_name"])
    return member


def _guild_url(live_server, guild: Guild) -> str:
    return f"{live_server.url}{reverse('hub_guild_detail', args=[guild.slug])}"


def describe_guild_share_and_invite():
    def it_shares_the_public_link_through_the_menu(live_server, page: Page, login_via_code):
        guild = GuildFactory(name="Brackenfold Letterpress Guild")
        _member(PICK_EMAIL, "Juniper Ashdown")
        page.set_viewport_size({"width": 1280, "height": 900})
        login_via_code(PICK_EMAIL)
        page.goto(_guild_url(live_server, guild))

        share = page.locator("[data-guild-share]")
        expect(share.locator("[role=menu]")).to_be_hidden()
        share.get_by_role("button", name="Share").click()
        expect(share.locator("[role=menu]")).to_be_visible()
        expect(share.locator("[role=menuitem]")).to_have_count(3)
        assert share.locator("[data-share-copy]").get_attribute("data-share-copy") == guild.public_url
        email_href = share.locator("a[href^='mailto:']").get_attribute("href") or ""
        assert guild.public_url in unquote(email_href)

    def it_lets_a_lead_invite_a_member_who_then_joins_from_the_banner(live_server, page: Page, login_via_code):
        lead = _member(LEAD_EMAIL, "Rowan Brackenfold")
        guild = GuildFactory(name="Brackenfold Letterpress Guild", guild_lead=lead)
        pick = _member(PICK_EMAIL, "Juniper Ashdown")
        _member("invite-other@example.com", "Thaddeus Quill")
        page.set_viewport_size({"width": 1280, "height": 900})
        login_via_code(LEAD_EMAIL)
        page.goto(_guild_url(live_server, guild))

        page.locator("[data-guild-invite-open]").click()
        panel = page.locator("[data-guild-invite-panel]")
        expect(panel).to_be_visible()
        rows = panel.locator("[data-guild-invite-row]")
        expect(rows.filter(has_text="Juniper Ashdown")).to_be_visible()
        expect(rows.filter(has_text="Thaddeus Quill")).to_be_visible()
        expect(rows.filter(has_text="Rowan Brackenfold")).to_be_visible()  # the lead is not a member of it yet

        panel.get_by_label("Find members").fill("junip")
        expect(rows.filter(has_text="Thaddeus Quill")).to_be_hidden()
        send = panel.locator("[data-guild-invite-send]")
        expect(send).to_be_disabled()
        rows.filter(has_text="Juniper Ashdown").locator("input[type=checkbox]").check()
        expect(send).to_have_text("Send Invites (1)")
        send.click()

        expect(page.get_by_text("Sent 1 invite.")).to_be_visible()
        expect(panel).to_be_hidden()
        assert GuildInvite.objects.open().filter(guild=guild, member=pick, invited_by=lead).count() == 1
        assert not GuildMembership.objects.filter(guild=guild).exists()

        page.context.clear_cookies()
        login_via_code(PICK_EMAIL)
        page.goto(_guild_url(live_server, guild))
        banner = page.locator("[data-guild-invite-banner]")
        expect(banner).to_contain_text("Rowan Brackenfold invited you to join Brackenfold Letterpress Guild")
        banner.get_by_role("button", name="Join Brackenfold Letterpress Guild").click()

        expect(banner).to_be_hidden()
        expect(
            page.locator("#guild-join-cta").get_by_role("button", name="Member, guild membership options")
        ).to_be_visible()
        membership = GuildMembership.objects.get(guild=guild, member=pick)
        assert membership.source == GuildMembership.Source.INVITE
        assert not GuildInvite.objects.open().exists()
