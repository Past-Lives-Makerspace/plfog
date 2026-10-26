"""Voting from Home's Poll card and a guild's Polls tab in a real browser (#925 AC 3, 4, 6 to 10).

While a poll is open, Home shows a Poll card; a tap on an answer swaps the card for its counts
in place over htmx (the URL does not change), with the member's answer marked, and a reload
shows the same. Hide folds the card to one line and the
member's choice holds across visits; an answered poll starts folded. With no poll open there is no
Poll card and no Past Polls link. A guild's lead posts a poll from the guild's Polls tab through the
real form (answers added in place), and a member votes there in place. Where a poll takes them, a
member writes their own answer on Home and it shows, anonymous, under the counts. Run with ``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect

from membership.models import Member
from polls.models import Poll, PollVote
from tests.membership.factories import GuildFactory, MembershipPlanFactory
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
        # The vote swapped only the card; folding now still says Voted.
        page.locator(CARD).get_by_role("button", name="Hide").click()
        expect(page.locator(CARD).locator("[data-poll-summary-voted]")).to_be_visible()

        # Answered, so a visit with nothing stored starts folded; Show opens it on the member's answer.
        page.evaluate("localStorage.clear()")
        page.reload()
        block = page.locator(CARD)
        expect(block.locator("[data-poll-body]")).to_be_hidden()
        expect(block.locator("[data-poll-summary]")).to_contain_text("Zorblax e2e poll?")
        expect(block.locator("[data-poll-summary-voted]")).to_have_text("Voted")
        block.get_by_role("button", name="Show").click()
        expect(block.locator("[data-my-vote]")).to_contain_text("Lathe")
        expect(block.get_by_role("button", name="Hide")).to_have_attribute("aria-expanded", "true")


def describe_folding_the_poll():
    def it_remembers_hide_across_visits_and_shows_past_polls_when_open(page, live_server, login_via_code):
        _sign_in(login_via_code, "poll-fold-e2e@example.com")
        poll_with("Laser", "Lathe", question="Zorblax fold e2e?")
        page.goto(f"{live_server.url}{reverse('hub_home')}")

        block = page.locator(CARD)
        expect(block.locator("[data-poll-body]")).to_be_visible()
        expect(block.locator("[data-poll-summary]")).to_be_hidden()
        block.get_by_role("button", name="Hide").click()
        expect(block.locator("[data-poll-body]")).to_be_hidden()
        expect(block.get_by_role("button", name="Show")).to_have_attribute("aria-expanded", "false")

        page.reload()
        expect(page.locator(CARD).locator("[data-poll-body]")).to_be_hidden()
        page.locator(CARD).get_by_role("button", name="Show").click()
        page.locator(CARD).get_by_role("link", name="See Past Polls →").click()
        expect(page).to_have_url(f"{live_server.url}{reverse('polls:index')}")


def describe_with_no_poll_open():
    def it_shows_no_poll_card_and_no_past_polls_quick_link(page, live_server, login_via_code):
        _sign_in(login_via_code, "poll-none-e2e@example.com")
        opens = timezone.now() - timedelta(days=10)
        poll_with("Laser", "Lathe", question="Zorblax closed e2e?", opens_at=opens, closes_at=opens + timedelta(days=7))
        page.goto(f"{live_server.url}{reverse('hub_home')}")

        expect(page.locator(CARD)).to_have_count(0)
        expect(page.locator("[data-quicklink-past-polls]")).to_have_count(0)


def describe_a_guilds_polls_tab():
    def it_lets_the_lead_post_a_poll_from_the_tab(page, live_server, login_via_code):
        lead = _sign_in(login_via_code, "poll-lead-e2e@example.com")
        guild = GuildFactory(name="Zorblax E2E Guild", guild_lead=lead)
        page.goto(f"{live_server.url}{reverse('hub_guild_detail', args=[guild.slug])}?tab=polls")

        page.locator("[data-guild-poll-toggle]").click()
        form = page.locator("[data-guild-polls-tab] [data-new-poll-form]")
        form.locator('textarea[name="question"]').fill("Zorblax which finish?")
        answers = form.locator('input[name="choice"]')
        answers.nth(0).fill("Oil")
        answers.nth(1).fill("Wax")
        form.locator("[data-add-choice]").click()
        form.locator('input[name="choice"]').nth(2).fill("Shellac")
        form.locator("[data-poll-lengths]").get_by_text("2 weeks").click()
        expect(form.locator("[data-compose-closes]")).to_contain_text("Closes ")
        form.locator("[data-post-poll]").click()

        open_poll = page.locator("[data-guild-open-poll]")
        expect(open_poll).to_contain_text("Zorblax which finish?")
        poll = Poll.objects.get()
        assert poll.guild == guild
        assert [choice.text for choice in poll.choices.all()] == ["Oil", "Wax", "Shellac"]
        assert poll.closes_at - poll.opens_at == timedelta(days=14)

    def it_lets_a_member_vote_in_place_on_the_tab(page, live_server, login_via_code):
        _sign_in(login_via_code, "poll-guild-voter-e2e@example.com")
        guild = GuildFactory(name="Zorblax Voting Guild")
        poll_with("Maple", "Walnut", question="Zorblax guild e2e?", guild=guild)
        url = f"{live_server.url}{reverse('hub_guild_detail', args=[guild.slug])}?tab=polls"
        page.goto(url)

        open_poll = page.locator("[data-guild-open-poll]")
        expect(open_poll).to_be_visible()
        open_poll.get_by_role("button", name="Walnut").click()

        expect(open_poll.locator("[data-my-vote]")).to_contain_text("Walnut")
        assert page.url == url
        assert PollVote.objects.count() == 1


def describe_writing_your_own_answer():
    def it_sends_a_written_answer_in_place_as_anonymous(page, live_server, login_via_code):
        member = _sign_in(login_via_code, "poll-writein-e2e@example.com")
        poll_with("Laser", "Lathe", question="Zorblax write e2e?", allows_write_in=True)
        home = f"{live_server.url}{reverse('hub_home')}"
        page.goto(home)

        card = page.locator(CARD)
        card.get_by_label("Or write your own answer").fill("A second bandsaw")
        card.locator("[data-poll-write-in-send]").click()

        written = card.locator("[data-poll-written]")
        expect(written).to_contain_text("A second bandsaw")
        expect(written).to_contain_text("Anonymous")
        assert page.url == home
        vote = PollVote.objects.get()
        assert (vote.member, vote.write_in, vote.anonymous) == (member, "A second bandsaw", True)
