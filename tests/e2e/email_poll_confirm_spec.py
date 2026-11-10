"""End-to-end (#1142 part 3): the poll's Ready to Send counts members, and names the email-only addresses it leaves out.

A guild of two members and one mailing list address: the email confirm would say three, a poll
reaches two, and the address is named as left out. Drives the real composer; sends nothing.
Run with ``pytest -m e2e``.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.urls import reverse
from playwright.sync_api import expect

from membership.models import Member
from tests.membership.factories import (
    GuildFactory,
    GuildMailingListEmailFactory,
    GuildMembershipFactory,
    MembershipPlanFactory,
)

LEAD_EMAIL = "zorblax-lead@example.com"


def _member(email: str) -> Member:
    user = get_user_model().objects.create_user(username=email, email=email)
    member = Member.objects.get(user=user)
    member.status = Member.Status.ACTIVE
    member.save(update_fields=["status"])
    return member


def describe_the_poll_confirm():
    def it_counts_members_only_and_names_the_addresses_left_out(live_server, page, login_via_code):
        MembershipPlanFactory()
        guild = GuildFactory(name="Zorblax Guild")
        for email in ("ann-zorblax@example.com", "ben-zorblax@example.com"):
            GuildMembershipFactory(guild=guild, member=_member(email))
        GuildMailingListEmailFactory(guild=guild, email="list-only-zorblax@example.com")
        login_via_code(LEAD_EMAIL)
        guild.guild_lead = Member.objects.get(user__username=LEAD_EMAIL)
        guild.save(update_fields=["guild_lead"])

        page.goto(f"{live_server.url}{reverse('hub_compose')}?audience=guild:{guild.pk}")
        expect(page.locator('input[name=recipients][value^="custom:"]')).to_have_count(1)
        page.locator("[data-compose-kind-choice] >> text=Poll").click()
        page.locator("[data-compose-poll-send]").click()

        expect(page.locator("[data-compose-poll-members]")).to_have_text("2")
        expect(page.locator("[data-compose-poll-email-only]")).to_have_text(
            "1 email-only address is left out: polls reach members only."
        )

        page.locator("button:has-text('Cancel')").click()
        page.locator('input[name=recipients][value^="custom:"]').uncheck()
        page.locator("[data-compose-poll-send]").click()
        expect(page.locator("[data-compose-poll-members]")).to_have_text("2")
        expect(page.locator("[data-compose-poll-email-only]")).to_be_hidden()
