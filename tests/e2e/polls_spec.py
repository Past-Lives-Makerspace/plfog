"""Voting from Home's Poll card in a real browser (#925 AC 3, 4 and 10).

While a poll is open, Home shows a Poll card; a tap on an answer swaps the card for its counts
in place over htmx (the URL does not change), with the member's answer marked, and a reload
shows the same. With no poll open there is no Poll card, but the Past Polls quick link is still
there and opens the page. Run with ``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect

from membership.models import Member
from polls.models import PollVote
from tests.membership.factories import MembershipPlanFactory
from tests.polls.factories import poll_with

CARD = '[data-block="poll"]'


def _sign_in(login_via_code, email: str) -> Member:
    MembershipPlanFactory()
    login_via_code(email)
    member = Member.objects.get(user=get_user_model().objects.get(username=email))
    member.welcome_dismissed_at = timezone.now()
    member.guided_tours_enabled = False
    member.save(update_fields=["welcome_dismissed_at", "guided_tours_enabled"])
    return member


def describe_voting_on_home():
    def it_swaps_in_the_counts_without_leaving_the_page(page, live_server, login_via_code):
        member = _sign_in(login_via_code, "poll-vote-e2e@example.com")
        poll_with("Laser", "Lathe", question="Zorblax e2e poll?")
        home = f"{live_server.url}{reverse('hub_home')}"
        page.goto(home)

        card = page.locator(CARD)
        expect(card).to_contain_text("Zorblax e2e poll?")
        card.get_by_role("button", name="Lathe").click()

        expect(card.locator("[data-my-vote]")).to_contain_text("Lathe")
        expect(card.locator("[data-poll-choices]")).to_have_count(0)
        expect(card).to_contain_text("1 vote · 100%")
        assert page.url == home
        assert PollVote.objects.get().member == member

        page.reload()
        expect(page.locator(CARD).locator("[data-my-vote]")).to_contain_text("Lathe")


def describe_with_no_poll_open():
    def it_shows_no_poll_card_but_still_links_past_polls(page, live_server, login_via_code):
        _sign_in(login_via_code, "poll-none-e2e@example.com")
        opens = timezone.now() - timedelta(days=10)
        poll_with("Laser", "Lathe", question="Zorblax closed e2e?", opens_at=opens, closes_at=opens + timedelta(days=7))
        page.goto(f"{live_server.url}{reverse('hub_home')}")

        expect(page.locator(CARD)).to_have_count(0)
        page.locator("[data-quicklink-past-polls]").click()

        expect(page).to_have_url(f"{live_server.url}{reverse('polls:index')}")
        expect(page.locator("[data-poll-list]")).to_contain_text("Zorblax closed e2e?")
