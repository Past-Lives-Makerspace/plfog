"""End-to-end: the Announcements page, from Admin Tools to a sent announcement's record.

The page, its tabs and pagination are boosted navigations; Save draft is an HTMX post that swaps
the draft's pk in out of band and replaces the URL; Delete is a confirm modal teleported to the
body. The Python suite renders each piece; only a browser proves the clicks land, the editor
holds a resumed draft's message, and the cards stack at 375px without a horizontal scroll.
``hub/base.html`` boosts the body, so after an in-app click ``wait_for_url`` resolves before the
arriving page's scripts run: every step waits on what the arriving page shows (a row, a card,
the editor's text), never on the URL alone. Run with ``pytest -m e2e``.
"""

from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect

from core.models import SiteConfiguration
from membership.models import AnnouncementDraft, FundingSnapshot, Member
from tests.membership.factories import (
    FundingSnapshotFactory,
    GuildFactory,
    GuildMembershipFactory,
    MembershipPlanFactory,
)

ADMIN_EMAIL = "announcements-admin@example.com"
EDITOR = '.pl-rte[data-rte-for="id_body"] .ql-editor'
GUILD_NAME = "Factory Forge Guild"


def _seed() -> tuple[AnnouncementDraft, object, object]:
    """September's results draft as the snapshot job makes it, and a guild with one reachable member."""
    # A plan must exist so the login signal auto-creates the admin's member.
    MembershipPlanFactory()
    FundingSnapshotFactory(
        cycle_label="September 2026",
        funding_pool=Decimal("1000.00"),
        results={
            "votes_cast": 12,
            "results": [
                {"guild_name": "Metal Guild", "funding": "600.00", "share_pct": 60.0},
                {"guild_name": "Fiber Arts", "funding": "400.00", "share_pct": 40.0},
            ],
        },
    )
    made = FundingSnapshot.make_newest_results_draft()
    assert made is not None
    guild = GuildFactory(name=GUILD_NAME)
    reader = get_user_model().objects.create_user(
        username="forge-reader@example.com", email="forge-reader@example.com", last_login=timezone.now()
    )
    member = Member.objects.get(user=reader)
    member.status = Member.Status.ACTIVE
    member.save(update_fields=["status"])
    GuildMembershipFactory(guild=guild, member=member)
    return made, guild, reader


def _sign_in_as_admin(login_via_code) -> None:
    login_via_code(ADMIN_EMAIL)
    user = get_user_model().objects.get(username=ADMIN_EMAIL)
    user.is_staff = True
    user.is_superuser = True
    user.save(update_fields=["is_staff", "is_superuser"])


def _row(page, pk: int):
    return page.locator(f'[data-announcement-row="{pk}"]')


def _no_horizontal_scroll(page) -> bool:
    return page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def _start_a_guild_announcement(page, guild, reader, message: str) -> None:
    """From the Announcements page: New announcement, aim it at the guild, write the message."""
    page.locator('a[data-help-key="announcements.new"]').click()
    expect(page.locator(EDITOR)).to_be_visible()
    page.locator("select[name=audience]").select_option(f"guild:{guild.pk}")
    # The audience change re-scopes the recipient checklist out of band; wait for this guild's roster.
    expect(page.locator(f'#compose-recipients input[name=recipients][value="user:{reader.pk}"]')).to_be_attached()
    page.locator(EDITOR).fill(message)


def describe_the_announcements_page():
    def it_finds_saves_deletes_and_sends_announcements(live_server, page, login_via_code):
        made, guild, reader = _seed()
        _sign_in_as_admin(login_via_code)

        # Admin Tools, then the Announcements card, lands on the Drafts tab with the job's draft.
        page.goto(f"{live_server.url}{reverse('hub_admin_tools')}")
        page.locator(f'a.pl-tool-card[href="{reverse("hub_announcements")}"]').click()
        results_row = _row(page, made.pk)
        expect(results_row).to_be_visible()
        expect(page.locator('[data-announcements-tab="drafts"]')).to_have_attribute("aria-current", "page")
        expect(results_row).to_have_attribute("data-announcement-state", "draft")
        expect(results_row.locator('td[data-label="Edited by"]')).to_have_text("Automatic")
        expect(results_row.locator(".pl-announcements-table__title")).to_have_text("September 2026 Voting Results")

        # Edit opens it in the composer with the prose in the editor and the line saying the job made it.
        results_row.locator("[data-announcement-edit]").click()
        expect(page.locator(EDITOR)).to_contain_text("The votes for September 2026 are in.")
        expect(page.locator("[data-compose-draft-automatic]")).to_contain_text("September 2026 voting results")

        # Back to Announcements, then a new guild announcement, saved as a draft.
        page.locator("[data-compose-back]").click()
        expect(results_row).to_be_visible()
        _start_a_guild_announcement(page, guild, reader, "Factory forge night moves to Thursday.")
        page.locator("[data-compose-save-draft]:visible").click()
        expect(page.locator("#compose-draft-pk")).not_to_have_value("")
        saved_pk = int(page.locator("#compose-draft-pk").input_value())
        expect(page).to_have_url(f"{live_server.url}{reverse('hub_compose_resume', args=[saved_pk])}")

        # Back to Announcements finds it on the Drafts tab.
        page.locator("[data-compose-back]").click()
        saved_row = _row(page, saved_pk)
        expect(saved_row).to_be_visible()
        expect(saved_row.locator('td[data-label="Audience"]')).to_have_text(GUILD_NAME)
        expect(saved_row.locator(".pl-announcements-table__excerpt")).to_have_text(
            "Factory forge night moves to Thursday."
        )

        # Delete goes through the confirm modal, and the row is gone.
        saved_row.locator("[data-announcement-delete]").click()
        modal = page.locator(".pl-modal-backdrop:visible")
        expect(modal).to_be_visible()
        modal.get_by_role("button", name="Delete draft").click()
        expect(_row(page, saved_pk)).to_have_count(0)
        expect(results_row).to_be_visible()
        assert not AnnouncementDraft.objects.filter(pk=saved_pk).exists()

        # A guild announcement sent from the composer lands on the Sent tab, sent, with its reach.
        _start_a_guild_announcement(page, guild, reader, "Factory forge is open late on Friday.")
        page.get_by_role("tab", name="2. Preview & send").click()
        page.get_by_role("button", name="Send announcement").click()
        page.get_by_role("button", name="Yes, send it").click()
        sent = AnnouncementDraft.objects.filter(sent_at__isnull=False)
        expect(page.locator('[data-announcements-tab="sent"]')).to_have_attribute("aria-current", "page")
        sent_row = page.locator('[data-announcement-state="sent"]')
        expect(sent_row).to_have_count(1)
        sent_pk = sent.get().pk
        expect(_row(page, sent_pk).locator("td[data-reach]")).to_have_attribute("data-reach", "1")

        # View shows who it reached and the email exactly as it went.
        _row(page, sent_pk).locator("[data-announcement-view]").click()
        facts = page.locator("[data-announcement-facts]")
        expect(facts).to_be_visible()
        expect(facts.locator('[data-fact="reached"]')).to_have_text("1 person")
        expect(facts.locator('[data-fact="audience"]')).to_have_text(GUILD_NAME)
        email = page.frame_locator('[data-announcement-channel="email"] iframe').locator("body")
        expect(email).to_contain_text("Factory forge is open late on Friday.")

    def it_stacks_both_tabs_into_cards_at_375px_without_a_horizontal_scroll(live_server, page, login_via_code):
        made, guild, _reader = _seed()
        AnnouncementDraft.objects.create(
            title=f"{GUILD_NAME} Announcement",
            audience=AnnouncementDraft.Audience.GUILD,
            guild=guild,
            body="<p>Factory forge night moved to Thursday.</p>",
            sent_at=timezone.now(),
            delivery_period="announcement:0",
        )
        _sign_in_as_admin(login_via_code)
        page.set_viewport_size({"width": 375, "height": 812})

        page.goto(f"{live_server.url}{reverse('hub_announcements')}")
        expect(_row(page, made.pk)).to_be_visible()
        assert _no_horizontal_scroll(page)
        # Each row is a card: the header row is hidden and every cell carries its own label.
        expect(page.locator(".pl-announcements-table thead")).to_be_hidden()
        expect(_row(page, made.pk).locator("[data-announcement-edit]")).to_be_visible()

        page.locator('[data-announcements-tab="sent"]').click()
        expect(page.locator('[data-announcement-state="sent"]')).to_be_visible()
        assert _no_horizontal_scroll(page)

    def it_widens_the_results_draft_to_members_who_never_logged_in(live_server, page, login_via_code):
        made, _guild, _reader = _seed()
        never = get_user_model().objects.create_user(username="never@example.com", email="never@example.com")
        Member.objects.filter(user=never).update(status=Member.Status.ACTIVE)
        # With a channel configured the picker defaults to it, so a reset would show.
        config = SiteConfiguration.load()
        config.discord_general_webhook_url = "https://discord.invalid/general"
        config.save()
        _sign_in_as_admin(login_via_code)
        logged_in = AnnouncementDraft(audience=AnnouncementDraft.Audience.SITE).recipient_count()

        page.goto(f"{live_server.url}{reverse('hub_compose_resume', args=[made.pk])}")
        expect(page.locator(EDITOR)).to_contain_text("The votes for September 2026 are in.")
        expect(page.locator("[data-compose-site-reach]")).to_contain_text("1 more has never logged in.")

        # A channel picked on Preview & send survives the toggle, which recounts in the browser; the
        # reach line follows it.
        page.get_by_role("tab", name="2. Preview & send").click()
        reach = page.locator(".pl-wizard-reach strong")
        expect(reach).to_have_text(str(logged_in))
        expect(page.locator("select[name=discord_channel]")).to_have_value("general")
        page.locator("select[name=discord_channel]").select_option("none")
        page.get_by_role("tab", name="1. Compose").click()
        page.locator("label.pl-toggle:has(input[name=include_never_logged_in])").click()
        page.get_by_role("tab", name="2. Preview & send").click()
        expect(reach).to_have_text(str(logged_in + 1))
        expect(page.locator("select[name=discord_channel]")).to_have_value("none")

        page.get_by_role("button", name="Send announcement").click()
        page.get_by_role("button", name="Yes, send it").click()
        expect(page.locator('[data-announcements-tab="sent"]')).to_have_attribute("aria-current", "page")
        queued = AnnouncementDraft.objects.get(pk=made.pk)
        assert queued.send_requested_at is not None
        assert queued.include_never_logged_in is True
        assert queued.discord_channel == "none"
