"""End-to-end: the Announcements tab shows exactly who a guild announcement reaches.

The recipient count and the Show-recipients list are single-sourced from the real send
resolver (``Guild.announcement_recipients``), so what the lead sees here is exactly the
email fan-out. The reveal is an Alpine toggle; a browser proves the count renders, the
list stays hidden until asked, and the toggle flips its own label. Run with
``pytest -m e2e``.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.urls import reverse
from playwright.sync_api import expect

from tests.membership.factories import GuildFactory, GuildMembershipFactory, MemberFactory, MembershipPlanFactory

ADMIN_EMAIL = "recipients-admin@example.com"


def describe_guild_announcement_recipients():
    def it_counts_members_and_reveals_their_emails_on_toggle(live_server, page, login_via_code):
        # A plan must exist so the login signal auto-creates the member.
        MembershipPlanFactory()
        guild = GuildFactory(name="Fiber Arts Guild")

        # Two active guild members addressed via their linked Users, plus one
        # unlinked member the resolver must skip (no User → no email to reach).
        # Creating a User auto-creates its Member (ensure_user_has_member), so
        # adopt that Member rather than minting a colliding second one.
        from membership.models import Member

        user_model = get_user_model()
        for email in ("weaver@example.com", "spinner@example.com"):
            user = user_model.objects.create_user(username=email, email=email)
            member = Member.objects.get(user=user)
            member.status = Member.Status.ACTIVE
            member.save(update_fields=["status"])
            GuildMembershipFactory(guild=guild, member=member)
        GuildMembershipFactory(guild=guild, member=MemberFactory(user=None))

        # Sign in and elevate to admin so the guild editor is reachable.
        login_via_code(ADMIN_EMAIL)
        user = user_model.objects.get(username=ADMIN_EMAIL)
        user.is_staff = True
        user.is_superuser = True
        user.save(update_fields=["is_staff", "is_superuser"])

        # Open the editor straight onto the Announcements/Emails tab.
        page.goto(f"{live_server.url}{reverse('hub_guild_edit', args=[guild.pk])}?tab=announcements")

        # The count reflects only the reachable members; the list starts hidden.
        expect(page.locator("body")).to_contain_text("2 followers")
        recipient_list = page.locator(".pl-recipient-list")
        expect(recipient_list).to_be_hidden()

        # Reveal the list: both addresses show and the button flips to Hide.
        # (The roster lives in the "Your Mailing List" section, labeled Show/Hide followers.)
        page.get_by_role("button", name="Show followers", exact=True).click()
        expect(recipient_list).to_be_visible()
        expect(recipient_list).to_contain_text("weaver@example.com")
        expect(recipient_list).to_contain_text("spinner@example.com")
        expect(page.get_by_role("button", name="Hide followers", exact=True)).to_be_visible()

        # The compose call-to-action carries the guild audience into the wizard.
        compose_href = f"{reverse('hub_compose')}?audience=guild:{guild.pk}"
        expect(page.locator(f'a[href="{compose_href}"]')).to_be_visible()
