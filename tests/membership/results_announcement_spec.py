"""The monthly results announcement: a site draft linked to a FundingSnapshot.

An admin's Draft announcement click (``FundingSnapshot.draft_results_announcement``) opens a
pre-filled site announcement in the composer. It takes the "<cycle> Voting Results" title,
carries a results visual built from the snapshot (a bar chart in the email, ``/voting`` style
bars on Discord), goes out through the background queue like every site send, and marks the
snapshot's results sent once it has gone.
"""

from __future__ import annotations

import json
from decimal import Decimal
from unittest.mock import patch

import httpx
import pytest
import respx
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db.models.signals import post_save
from django.utils import timezone
from factory.django import mute_signals

from core.events.discord import build_embed_payload
from core.html_sanitize import sanitize_rich_html
from core.models import EventDelivery, Notification, SiteConfiguration, TransactionalEmailLog
from membership.models import (
    AlreadySentError,
    AnnouncementDraft,
    FundingSnapshot,
    GuildAnnouncement,
    NoResultsToAnnounceError,
    ResultsAlreadySentError,
)
from tests.membership.factories import FundingSnapshotFactory, GuildFactory, MemberFactory, MembershipPlanFactory

pytestmark = pytest.mark.django_db

_WEBHOOK = "https://discord.test/api/webhooks/general"
_FULL = "█"
_EMPTY = "░"


def _bar(filled: int) -> str:
    return f"`{_FULL * filled}{_EMPTY * (12 - filled)}`"


def _author(username: str = "felix") -> User:
    MembershipPlanFactory()
    return User.objects.create_user(username=username, email=f"{username}@x.com", password="pw", first_name="Felix")


def _activated(username: str) -> User:
    """An ACTIVE member whose user has signed in: a real site-wide recipient."""
    member = MemberFactory()
    with mute_signals(post_save):
        user = User.objects.create_user(
            username=username, email=f"{username}@x.com", password="pw", last_login=timezone.now()
        )
    member.user = user
    member.save(update_fields=["user"])
    return user


def _row(name: str, funding: str, share: float) -> dict:
    return {"guild_name": name, "funding": funding, "share_pct": share, "total_points": 1}


def _snapshot(*, label: str = "September 2026", pool: str = "1000.00", votes: int = 12, rows=None) -> FundingSnapshot:
    rows = rows if rows is not None else [_row("Metal Guild", "600.00", 60.0), _row("Fiber Arts", "400.00", 40.0)]
    return FundingSnapshotFactory(
        cycle_label=label,
        funding_pool=Decimal(pool),
        results={"votes_cast": votes, "total_pool": pool, "results": rows},
    )


def _four_guild_snapshot() -> FundingSnapshot:
    return _snapshot(
        rows=[
            _row("Alpha", "500.00", 50.0),
            _row("Bravo", "250.00", 25.0),
            _row("Charlie", "150.00", 15.0),
            _row("Delta", "100.00", 10.0),
        ]
    )


def _general_webhook() -> None:
    config = SiteConfiguration.load()
    config.discord_general_webhook_url = _WEBHOOK
    config.save()


def _sent_to(address: str) -> int:
    return TransactionalEmailLog.objects.filter(
        trigger_kind="site_announcement", to_email=address, status=TransactionalEmailLog.Status.SENT
    ).count()


def describe_draft_results_announcement():
    def it_creates_a_site_draft_linked_to_the_snapshot_with_the_results_defaults():
        author = _author()
        snapshot = _snapshot()

        draft = snapshot.draft_results_announcement(author)

        draft.refresh_from_db()
        assert draft.author == author
        assert draft.funding_snapshot == snapshot
        assert draft.audience == AnnouncementDraft.Audience.SITE
        assert draft.title == "September 2026 Voting Results"
        assert draft.body == snapshot.results_announcement_body()
        assert draft.push_message == "September 2026 voting results are in. See how the guild funding was split."
        assert draft.send_email is True
        assert draft.push_enabled is True
        assert draft.discord_enabled is True
        assert draft.discord_channel == GuildAnnouncement.DiscordChannel.GENERAL
        assert draft.mention == AnnouncementDraft.Mention.EVERYONE
        assert draft.show_sender is True
        assert draft.mark_as_urgent is False
        assert draft.sent_at is None
        assert draft.send_requested_at is None

    def it_reopens_the_same_draft_on_a_second_click():
        author = _author()
        snapshot = _snapshot()
        first = snapshot.draft_results_announcement(author)
        second = snapshot.draft_results_announcement(author)
        assert second.pk == first.pk
        assert AnnouncementDraft.objects.count() == 1

    def it_gives_another_admin_their_own_draft():
        snapshot = _snapshot()
        mine = snapshot.draft_results_announcement(_author("felix"))
        theirs = snapshot.draft_results_announcement(_author("robin"))
        assert theirs.pk != mine.pk

    def it_does_not_reopen_a_draft_already_queued():
        author = _author()
        snapshot = _snapshot()
        queued = snapshot.draft_results_announcement(author)
        queued.queue_send()
        fresh = snapshot.draft_results_announcement(author)
        assert fresh.pk != queued.pk

    def it_does_not_reopen_another_snapshots_draft():
        author = _author()
        august = _snapshot(label="August 2026")
        september = _snapshot()
        august_draft = august.draft_results_announcement(author)
        assert september.draft_results_announcement(author).pk != august_draft.pk

    def it_refuses_results_that_already_went_out():
        snapshot = _snapshot()
        snapshot.results_sent_at = timezone.now()
        snapshot.save()
        with pytest.raises(ResultsAlreadySentError, match="These results were already sent."):
            snapshot.draft_results_announcement(_author())
        assert not AnnouncementDraft.objects.exists()

    def it_refuses_a_snapshot_with_no_per_guild_results():
        snapshot = FundingSnapshotFactory(cycle_label="Legacy", results={})
        with pytest.raises(NoResultsToAnnounceError, match="'Legacy' has no results to announce."):
            snapshot.draft_results_announcement(_author())
        assert not AnnouncementDraft.objects.exists()


def describe_results_announcement_body():
    def it_says_the_cycle_turnout_and_pool_and_links_the_results_page(settings):
        settings.MEMBER_BASE_URL = "https://members.example"
        body = _snapshot(pool="12345.60", votes=120).results_announcement_body()
        assert body == (
            "<p>The votes for September 2026 are in. 120 members voted on how the $12,345.60 guild "
            "funding pool is split. Thank you to everyone who voted.</p>"
            '<p>See the full breakdown on the <a href="https://members.example/guilds/voting/history/" '
            'rel="noopener nofollow noreferrer" target="_blank">voting results page</a>.</p>'
        )

    def it_says_member_for_a_single_voter():
        assert "1 member voted on how" in _snapshot(votes=1).results_announcement_body()

    def it_is_stored_exactly_as_the_sanitizer_keeps_it():
        body = _snapshot().results_announcement_body()
        assert sanitize_rich_html(body) == body

    def it_escapes_markup_in_an_admin_typed_cycle_label():
        body = _snapshot(label="Fall <b>Fun</b> & Games").results_announcement_body()
        assert "Fall &lt;b&gt;Fun&lt;/b&gt; &amp; Games" in body
        assert "<b>" not in body

    def it_fails_loudly_without_a_vote_count():
        snapshot = FundingSnapshotFactory(results={"results": [_row("Metal Guild", "600.00", 60.0)]})
        with pytest.raises(KeyError):
            snapshot.results_announcement_body()


def describe_results_visual():
    def it_heads_the_visual_with_the_pool_and_thousands_separators():
        assert _snapshot(pool="12345.6").results_heading == "How the $12,345.60 funding pool was split"

    def it_writes_one_plain_line_per_guild_in_results_order():
        assert _snapshot().allocation_lines() == ["Metal Guild: $600.00 (60.0%)", "Fiber Arts: $400.00 (40.0%)"]

    def it_puts_the_heading_over_the_existing_bar_chart_in_the_email():
        snapshot = _snapshot()
        html = snapshot.results_visual_html()
        assert html.startswith("<h3 style=")
        assert ">How the $1,000.00 funding pool was split</h3>" in html
        assert html.endswith(str(snapshot.allocation_chart_html()))

    def it_escapes_a_guild_name_in_the_email_and_keeps_it_as_typed_on_discord():
        snapshot = _snapshot(rows=[_row("B&W <Darkroom>", "1000.00", 100.0)])
        assert "B&amp;W &lt;Darkroom&gt;" in snapshot.results_visual_html()
        assert "<Darkroom>" not in snapshot.results_visual_html()
        assert f"🥇 {_bar(12)} **B&W <Darkroom>**: $1,000.00 (100.0%)" in snapshot.allocation_discord_block()

    def it_draws_voting_style_bars_sized_to_the_leader_on_discord():
        assert _four_guild_snapshot().allocation_discord_block() == "\n".join(
            [
                "**How the $1,000.00 funding pool was split**",
                f"🥇 {_bar(12)} **Alpha**: $500.00 (50.0%)",
                f"🥈 {_bar(6)} **Bravo**: $250.00 (25.0%)",
                f"🥉 {_bar(4)} **Charlie**: $150.00 (15.0%)",
                f"`4.` {_bar(2)} Delta: $100.00 (10.0%)",
            ]
        )

    def it_draws_a_sliver_for_every_guild_when_the_pool_is_empty():
        snapshot = _snapshot(pool="0.00", rows=[_row("Alpha", "0.00", 50.0), _row("Bravo", "0.00", 50.0)])
        lines = snapshot.allocation_discord_block().split("\n")
        assert lines[1] == f"🥇 {_bar(1)} **Alpha**: $0.00 (50.0%)"
        assert lines[2] == f"🥈 {_bar(1)} **Bravo**: $0.00 (50.0%)"


def describe_a_results_draft():
    def _draft(**overrides) -> AnnouncementDraft:
        author = _author()
        draft = _snapshot().draft_results_announcement(author)
        for field, value in overrides.items():
            setattr(draft, field, value)
        draft.title = draft.announcement_category
        return draft

    def describe_announcement_category():
        def it_is_the_cycle_voting_results():
            assert _draft().announcement_category == "September 2026 Voting Results"

        def it_leads_with_urgent_when_marked():
            assert _draft(mark_as_urgent=True).announcement_category == "Urgent: September 2026 Voting Results"

        def it_keeps_the_results_title_for_another_audience():
            guild = GuildFactory(name="Metal Guild")
            draft = _draft(audience=AnnouncementDraft.Audience.GUILD, guild=guild)
            assert draft.announcement_category == "September 2026 Voting Results"

        def it_becomes_a_plain_site_announcement_when_the_snapshot_is_deleted():
            draft = _draft()
            draft.save()
            draft.funding_snapshot.delete()
            draft.refresh_from_db()
            assert draft.funding_snapshot is None
            assert draft.announcement_category == "Makerspace Announcement"

    def describe_build_email_message():
        def it_puts_the_chart_and_its_heading_after_the_message():
            message = _draft().build_email_message("https://members.example/")
            html = message.html_body
            assert html.index("The votes for September 2026 are in.") < html.index(
                "How the $1,000.00 funding pool was split"
            )
            assert html.index("How the $1,000.00 funding pool was split") < html.index("Metal Guild")
            assert "linear-gradient(90deg,#0d4876,#d4a043)" in html
            assert message.title == "September 2026 Voting Results"

        def it_writes_one_line_per_guild_into_the_text_part():
            body = _draft().build_email_message("https://members.example/").body
            assert (
                "How the $1,000.00 funding pool was split\nMetal Guild: $600.00 (60.0%)\nFiber Arts: $400.00 (40.0%)"
                "\n\nhttps://members.example/"
            ) in body

        def it_leaves_a_plain_site_draft_without_a_chart():
            plain = AnnouncementDraft(author=_author("plain"), title="Makerspace Announcement", body="<p>Hello</p>")
            message = plain.build_email_message("https://members.example/")
            assert "funding pool was split" not in message.html_body
            assert "linear-gradient(90deg,#0d4876,#d4a043)" not in message.html_body
            assert message.body == "Makerspace Announcement\n\nFrom Felix\n\nHello\n\nhttps://members.example/"

    def describe_build_discord_message():
        def it_posts_the_message_then_the_results_block():
            draft = _draft()
            message = draft.build_discord_message("https://members.example/")
            prose = (
                "The votes for September 2026 are in. 12 members voted on how the $1,000.00 guild funding pool "
                "is split. Thank you to everyone who voted. See the full breakdown on the voting results page."
            )
            block = draft.funding_snapshot.allocation_discord_block()
            assert message.title == "September 2026 Voting Results"
            assert message.body == f"{prose}\n\n{block}"
            assert message.url == "https://members.example/"
            assert message.trigger_kind == "site_announcement"

        def it_shortens_a_long_message_so_the_results_always_fit():
            draft = _draft(body="<p>" + "word " * 1500 + "</p>")
            body = draft.build_discord_message("https://members.example/").body
            block = draft.funding_snapshot.allocation_discord_block()
            assert len(body) <= 4096
            assert body.endswith(f"…\n\n{block}")

        def it_hands_the_bold_and_the_bars_to_discord_unescaped():
            message = _draft().build_discord_message("https://members.example/")
            description = build_embed_payload(message)["embeds"][0]["description"]
            assert description == message.body
            assert "**Metal Guild**" in description
            assert f"🥇 {_bar(12)}" in description

        def it_posts_a_plain_draft_as_the_in_app_message():
            plain = AnnouncementDraft(author=_author("plain"), title="Makerspace Announcement", body="<p>Hello</p>")
            message = plain.build_discord_message("https://members.example/")
            assert message.title == "Makerspace Announcement"
            assert message.body == "Hello"


def describe_queue_send():
    def _site_draft(**overrides) -> AnnouncementDraft:
        fields = {"author": _author(), "title": "Makerspace Announcement", "body": "<p>Hello</p>"}
        fields.update(overrides)
        return AnnouncementDraft.objects.create(**fields)

    def it_stamps_the_request_and_sends_nothing(mailoutbox):
        _activated("reader")
        draft = _site_draft()
        draft.queue_send()
        draft.refresh_from_db()
        assert draft.send_requested_at is not None
        assert draft.sent_at is None
        assert mailoutbox == []
        assert not Notification.objects.exists()
        assert not EventDelivery.objects.exists()

    def it_changes_nothing_on_a_draft_already_queued():
        draft = _site_draft()
        draft.queue_send()
        first = AnnouncementDraft.objects.get(pk=draft.pk).send_requested_at
        draft.queue_send()
        assert AnnouncementDraft.objects.get(pk=draft.pk).send_requested_at == first

    def it_refuses_a_draft_already_sent():
        draft = _site_draft(sent_at=timezone.now())
        with pytest.raises(AlreadySentError):
            draft.queue_send()

    def it_refuses_an_empty_message():
        draft = _site_draft(body="<p><br></p>")
        with pytest.raises(ValidationError):
            draft.queue_send()
        draft.refresh_from_db()
        assert draft.send_requested_at is None

    def it_takes_a_queued_draft_out_of_the_composers_hands():
        draft = _site_draft()
        draft.queue_send()
        assert draft not in AnnouncementDraft.objects.for_user(draft.author)
        assert list(AnnouncementDraft.objects.queued()) == [draft]

    def it_puts_a_draft_back_in_the_composers_hands_when_unqueued():
        draft = _site_draft()
        draft.queue_send()
        draft.unqueue()
        draft.refresh_from_db()
        assert draft.send_requested_at is None
        assert list(AnnouncementDraft.objects.for_user(draft.author)) == [draft]

    def describe_for_a_results_announcement():
        def it_refuses_results_already_sent():
            snapshot = _snapshot()
            draft = snapshot.draft_results_announcement(_author())
            FundingSnapshot.objects.filter(pk=snapshot.pk).update(results_sent_at=timezone.now())
            with pytest.raises(ResultsAlreadySentError, match="These results were already sent."):
                draft.queue_send()
            draft.refresh_from_db()
            assert draft.send_requested_at is None

        def it_refuses_a_second_admins_draft_while_the_first_is_sending():
            snapshot = _snapshot()
            snapshot.draft_results_announcement(_author("felix")).queue_send()
            second = snapshot.draft_results_announcement(_author("robin"))
            with pytest.raises(ResultsAlreadySentError, match="These results are already sending."):
                second.queue_send()
            second.refresh_from_db()
            assert second.send_requested_at is None

        def it_queues_alongside_a_draft_for_another_snapshot():
            _snapshot(label="August 2026").draft_results_announcement(_author("felix")).queue_send()
            september = _snapshot().draft_results_announcement(_author("robin"))
            september.queue_send()
            september.refresh_from_db()
            assert september.send_requested_at is not None

        def it_says_the_snapshot_is_sending_while_queued():
            snapshot = _snapshot()
            draft = snapshot.draft_results_announcement(_author())
            assert snapshot.results_announcement_sending is False
            draft.queue_send()
            assert snapshot.results_announcement_sending is True
            assert snapshot.results_pending is True


def describe_send_for_a_results_announcement():
    def it_marks_the_draft_sent_and_the_snapshots_results_sent():
        _activated("reader")
        snapshot = _snapshot()
        draft = snapshot.draft_results_announcement(_author())
        draft.queue_send()

        draft.send()

        draft.refresh_from_db()
        snapshot.refresh_from_db()
        assert draft.sent_at is not None
        assert draft.send_requested_at is None
        assert snapshot.results_sent_at is not None
        assert snapshot.results_send_count == 1
        assert snapshot.results_pending is False
        assert snapshot.results_announcement_sending is False

    def it_refuses_at_send_when_another_draft_already_sent_the_results(mailoutbox):
        _activated("reader")
        snapshot = _snapshot()
        mine = snapshot.draft_results_announcement(_author("felix"))
        theirs = snapshot.draft_results_announcement(_author("robin"))
        mine.send()
        mailoutbox.clear()
        with pytest.raises(ResultsAlreadySentError, match="These results were already sent."):
            theirs.send()
        assert mailoutbox == []
        theirs.refresh_from_db()
        assert theirs.sent_at is None

    def it_counts_the_results_once_however_often_the_stamp_runs():
        snapshot = _snapshot()
        snapshot.mark_results_announced()
        first = snapshot.results_sent_at
        snapshot.mark_results_announced()
        snapshot.refresh_from_db()
        assert snapshot.results_send_count == 1
        assert snapshot.results_sent_at == first

    def it_sends_the_title_chart_and_bars_on_every_channel(mailoutbox):
        _general_webhook()
        reader = _activated("reader")
        draft = _snapshot().draft_results_announcement(_author())
        with respx.mock:
            route = respx.post(_WEBHOOK).mock(return_value=httpx.Response(204))
            draft.send()
        assert [m.subject for m in mailoutbox] == ["September 2026 Voting Results"]
        html = mailoutbox[0].alternatives[0][0]
        assert "How the $1,000.00 funding pool was split" in html
        assert "Metal Guild" in html
        payload = json.loads(route.calls.last.request.content)
        assert payload["content"] == "@everyone"
        assert payload["embeds"][0]["title"] == "September 2026 Voting Results"
        assert payload["embeds"][0]["description"] == draft.build_discord_message("https://x/").body
        assert f"🥇 {_bar(12)} **Metal Guild**: $600.00 (60.0%)" in payload["embeds"][0]["description"]
        bell = Notification.objects.get(user=reader, trigger="site_announcement")
        assert bell.title == "September 2026 Voting Results"


def describe_a_retried_site_send():
    """The site send's period is the draft's pk, so a retry reaches only who the last run missed."""

    def _three_readers() -> list[User]:
        return [_activated(name) for name in ("ada", "bo", "cy")]

    def _queued_results_draft() -> AnnouncementDraft:
        draft = _snapshot().draft_results_announcement(_author())
        draft.queue_send()
        return draft

    def it_reaches_only_the_missed_members_after_a_run_killed_mid_fan_out():
        from core.email import _deliver as real_deliver

        _general_webhook()
        readers = _three_readers()
        draft = _queued_results_draft()

        def killed_at_bo(*args, **kwargs):
            if "bo@x.com" in kwargs.get("recipients", []):
                raise KeyboardInterrupt("worker killed")
            return real_deliver(*args, **kwargs)

        with respx.mock:
            route = respx.post(_WEBHOOK).mock(return_value=httpx.Response(204))
            with patch("core.email._deliver", side_effect=killed_at_bo):
                with pytest.raises(KeyboardInterrupt):
                    call_command("send_queued_announcements")
            draft.refresh_from_db()
            assert draft.sent_at is None
            assert draft.send_requested_at is not None
            assert route.call_count == 0

            call_command("send_queued_announcements")

            assert route.call_count == 1
        for reader in readers:
            assert _sent_to(reader.email) == 1
            assert Notification.objects.filter(user=reader, trigger="site_announcement").count() == 1
        draft.refresh_from_db()
        assert draft.sent_at is not None
        assert draft.funding_snapshot.results_send_count == 1

    def it_posts_to_discord_once_after_a_run_killed_before_it_was_marked_sent():
        _general_webhook()
        readers = _three_readers()
        draft = _queued_results_draft()

        with respx.mock:
            route = respx.post(_WEBHOOK).mock(return_value=httpx.Response(204))
            with patch.object(FundingSnapshot, "mark_results_announced", side_effect=RuntimeError("worker killed")):
                with pytest.raises(CommandError):
                    call_command("send_queued_announcements")
            draft.refresh_from_db()
            assert draft.sent_at is None
            assert draft.send_requested_at is not None
            assert route.call_count == 1

            call_command("send_queued_announcements")

            assert route.call_count == 1
        for reader in readers:
            assert _sent_to(reader.email) == 1
        draft.refresh_from_db()
        assert draft.sent_at is not None
        assert draft.funding_snapshot.results_sent_at is not None
