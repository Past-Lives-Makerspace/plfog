"""Specs for the monthly guild wiki digest: its payload, its copy, and its command."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.core.management import call_command
from django.db.models.signals import post_save
from django.utils import timezone
from factory.django import mute_signals

from core.events.registry import get_event
from core.events.settings_matrix import STAFF_SECTION, _section_for
from core.models import EventDelivery
from membership.models import WikiPage, WikiSearchMiss
from membership.wiki_guild import (
    digest_in_app_body,
    digest_subject,
    guild_digest_payload,
    previous_month_window,
)
from tests.membership.factories import (
    GuildFactory,
    GuildStaffMembershipFactory,
    MemberFactory,
    WikiPageFactory,
    WikiSearchMissFactory,
    WikiWantedPageFactory,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def window():
    """Last calendar month, as the command computes it."""
    return previous_month_window()


def _linked_member(username: str):
    """A member with a login and an address, so the resolvers can actually reach them."""
    with mute_signals(post_save):
        user = User.objects.create_user(username=username, email=f"{username}@example.com")
        user.last_login = timezone.now()
        user.save(update_fields=["last_login"])
    member = MemberFactory(full_legal_name=username.title())
    member.user = user
    member.save(update_fields=["user"])
    return member


def _created_in(page: WikiPage, when) -> None:
    """Backdate ``created_at`` past ``auto_now_add``."""
    WikiPage.objects.filter(pk=page.pk).update(created_at=when)


def _missed_in(guild, query: str, when, *, people: int = 1) -> None:
    for index in range(people):
        row = WikiSearchMissFactory(guild=guild, query=query, member=MemberFactory())
        WikiSearchMiss.objects.filter(pk=row.pk).update(created_at=when, query=query)
        assert index >= 0


def describe_previous_month_window():
    def it_spans_the_whole_previous_calendar_month(db):
        start, end = previous_month_window(date(2026, 3, 14))
        assert (timezone.localtime(start).date(), timezone.localtime(end).date()) == (
            date(2026, 2, 1),
            date(2026, 3, 1),
        )

    def it_does_not_slip_a_day_at_the_end_of_a_month(db):
        """A run at 13:00 UTC on the 1st is 06:00 in Portland; the window is last month HERE."""
        start, end = previous_month_window(date(2026, 1, 1))
        assert (timezone.localtime(start).date(), timezone.localtime(end).date()) == (
            date(2025, 12, 1),
            date(2026, 1, 1),
        )


def describe_guild_digest_payload():
    def it_returns_None_when_all_three_sections_are_empty(db, window):
        start, end = window
        assert guild_digest_payload(GuildFactory(), month_start=start, month_end=end) is None

    def it_sends_on_overdue_alone(db, window):
        start, end = window
        guild = GuildFactory()
        WikiPageFactory(guild=guild, kind=WikiPage.Kind.MACHINE)
        WikiPage.objects.update(created_at=timezone.now() - timedelta(days=800))
        payload = guild_digest_payload(guild, month_start=start, month_end=end)
        assert payload is not None
        assert len(payload["overdue"]) == 1

    def it_lists_last_months_new_pages_only(db, window):
        start, end = window
        guild = GuildFactory()
        inside = WikiPageFactory(guild=guild, title="Written last month")
        _created_in(inside, start + timedelta(days=2))
        outside = WikiPageFactory(guild=guild, title="Written this month")
        _created_in(outside, end + timedelta(hours=1))
        payload = guild_digest_payload(guild, month_start=start, month_end=end)
        assert [item["title"] for item in payload["new_pages"]] == ["Written last month"]

    def it_excludes_a_reported_page_from_the_overdue_section(db, window):
        """needs_review() is a superset that ORs in spec D's reported state. A list whose
        only verb is "still accurate" is the wrong answer to "somebody says this is wrong"."""
        start, end = window
        guild = GuildFactory()
        reported = WikiPageFactory(guild=guild, kind=WikiPage.Kind.MACHINE, needs_review_since=timezone.now())
        _created_in(reported, timezone.now() - timedelta(days=800))
        stale = WikiPageFactory(guild=guild, kind=WikiPage.Kind.MACHINE, title="Just old")
        _created_in(stale, timezone.now() - timedelta(days=800))
        payload = guild_digest_payload(guild, month_start=start, month_end=end)
        assert [item["title"] for item in payload["overdue"]] == ["Just old"]

    def it_links_every_overdue_page_with_the_confirm_cue(db, window):
        start, end = window
        guild = GuildFactory()
        page = WikiPageFactory(guild=guild, kind=WikiPage.Kind.MACHINE)
        _created_in(page, timezone.now() - timedelta(days=800))
        payload = guild_digest_payload(guild, month_start=start, month_end=end)
        url = payload["overdue"][0]["url"]
        assert url.startswith("https://") or url.startswith("http://")
        assert url.endswith("?confirm=1")

    def it_counts_the_open_wanted_rows(db, window):
        start, end = window
        guild = GuildFactory()
        WikiWantedPageFactory(guild=guild)
        page = WikiPageFactory(guild=guild)
        _created_in(page, start + timedelta(days=1))
        payload = guild_digest_payload(guild, month_start=start, month_end=end)
        assert payload["wanted_open_count"] == 1

    def it_carries_absolute_urls_everywhere(db, window):
        start, end = window
        guild = GuildFactory()
        page = WikiPageFactory(guild=guild)
        _created_in(page, start + timedelta(days=1))
        payload = guild_digest_payload(guild, month_start=start, month_end=end)
        for key in ("guild_url", "tab_url", "new_url", "wanted_url"):
            assert payload[key].startswith("http"), key
        assert payload["new_pages"][0]["url"].startswith("http")


def describe_digest_subject():
    def it_leads_with_overdue(db, window):
        guild = GuildFactory(name="Woodworking")
        payload = {"overdue": [1], "misses": [1], "new_pages": [1]}
        assert digest_subject(guild, payload) == "Woodworking wiki: 1 page needs a look"

    def it_writes_the_plural_out(db):
        guild = GuildFactory(name="Woodworking")
        payload = {"overdue": [1, 2], "misses": [], "new_pages": []}
        assert digest_subject(guild, payload) == "Woodworking wiki: 2 pages need a look"

    def it_falls_back_to_the_failed_searches(db):
        guild = GuildFactory(name="Print")
        payload = {"overdue": [], "misses": [1], "new_pages": [1]}
        assert digest_subject(guild, payload) == "Print wiki: 1 search found nothing"

    def it_falls_back_to_the_new_pages(db):
        guild = GuildFactory(name="Print")
        payload = {"overdue": [], "misses": [], "new_pages": [1, 2]}
        assert digest_subject(guild, payload) == "Print wiki: 2 new pages"


def describe_digest_in_app_body():
    def it_omits_a_zero_section_rather_than_saying_zero(db):
        body = digest_in_app_body({"overdue": [1, 2, 3], "misses": [], "new_pages": [1]})
        assert body == "3 pages need a look, and 1 new page went up."

    def it_reads_as_one_clause_when_there_is_one_thing(db):
        assert digest_in_app_body({"overdue": [], "misses": [1], "new_pages": []}) == "1 search found nothing."


def describe_the_settings_page_section():
    def it_lands_under_staff_and_leadership_not_Guilds(db):
        """GUILD_LEADERSHIP is in STAFF_RECIPIENTS, so category only drives the email's
        X-Category header. Asserted so nobody later "fixes" a row that is already right."""
        event = get_event("wiki.guild_digest_monthly")
        assert event.category == "Guilds"
        assert _section_for(event) == STAFF_SECTION


def describe_send_wiki_guild_digest():
    @pytest.fixture
    def guild_with_news(db, window):
        start, _end = window
        guild = GuildFactory(name="Woodworking", guild_lead=_linked_member("digest_lead"))
        GuildStaffMembershipFactory(guild=guild, member=_linked_member("digest_orienter"), role="orienter")
        page = WikiPageFactory(guild=guild, title="Bandsaw basics")
        _created_in(page, start + timedelta(days=3))
        _missed_in(guild, "epoxy cure time", start + timedelta(days=4), people=2)
        return guild

    def it_sends_nothing_on_the_second_of_the_month(db, guild_with_news, monkeypatch):
        monkeypatch.setattr(timezone, "localdate", lambda: date(2026, 3, 2))
        call_command("send_wiki_guild_digest")
        assert mail.outbox == []

    def it_still_prunes_on_a_day_it_does_not_send(db, monkeypatch):
        guild = GuildFactory()
        old = WikiSearchMissFactory(guild=guild)
        WikiSearchMiss.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=200))
        monkeypatch.setattr(timezone, "localdate", lambda: date(2026, 3, 2))
        call_command("send_wiki_guild_digest")
        assert WikiSearchMiss.objects.count() == 0

    def it_sends_off_cycle_with_force(db, guild_with_news):
        """No monkeypatched date: --force is the escape hatch for "today is not the 1st",
        and today, whenever the suite runs, almost never is."""
        call_command("send_wiki_guild_digest", "--force")
        assert len(mail.outbox) >= 1

    def it_reaches_the_lead_and_every_staff_role(db, guild_with_news):
        call_command("send_wiki_guild_digest", "--force")
        addressed = {address for message in mail.outbox for address in message.to}
        assert "digest_lead@example.com" in addressed
        assert "digest_orienter@example.com" in addressed

    def it_delivers_once_per_period(db, guild_with_news):
        call_command("send_wiki_guild_digest", "--force")
        first = EventDelivery.objects.filter(event_key="wiki.guild_digest_monthly").count()
        sent_first = len(mail.outbox)
        assert sent_first >= 1
        call_command("send_wiki_guild_digest", "--force")
        assert EventDelivery.objects.filter(event_key="wiki.guild_digest_monthly").count() == first
        assert len(mail.outbox) == sent_first

    def it_skips_a_guild_with_nothing_to_report(db, guild_with_news):
        GuildFactory(name="Quiet guild", guild_lead=_linked_member("quiet_lead"))
        call_command("send_wiki_guild_digest", "--force")
        addressed = {address for message in mail.outbox for address in message.to}
        assert "quiet_lead@example.com" not in addressed

    def it_gives_every_guild_its_own_numbers(db, guild_with_news, window):
        start, _end = window
        other = GuildFactory(name="Print", guild_lead=_linked_member("print_lead"))
        page = WikiPageFactory(guild=other, title="Screen exposure times")
        _created_in(page, start + timedelta(days=2))
        call_command("send_wiki_guild_digest", "--force")
        by_address = {address: message for message in mail.outbox for address in message.to}
        assert "Bandsaw basics" not in by_address["print_lead@example.com"].body
        assert "Screen exposure times" in by_address["print_lead@example.com"].body

    def it_writes_both_bodies_with_the_same_links(db, guild_with_news):
        call_command("send_wiki_guild_digest", "--force")
        message = next(m for m in mail.outbox if "digest_lead@example.com" in m.to)
        html = next(body for body, _mime in message.alternatives)
        for body in (message.body, html):
            assert "?tab=wiki" in body
            assert "epoxy cure time" in body
        assert "http://testserver/wiki/" not in message.body
