"""The monthly results announcement: a site draft linked to a FundingSnapshot.

An admin's Draft announcement click (``FundingSnapshot.draft_results_announcement``) opens a
pre-filled site announcement in the composer. It takes the "<cycle> Voting Results" title,
carries a results visual built from the snapshot (a bar chart in the email, ``/voting`` style
bars on Discord), goes out through the background queue like every site send, and marks the
snapshot's results sent once it has gone.
"""

from __future__ import annotations

import json
from datetime import timedelta
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

    def it_gives_a_second_admin_the_same_shared_draft():
        # Replaces it_gives_another_admin_their_own_draft: drafts are shared.
        snapshot = _snapshot()
        felix = _author("felix")
        mine = snapshot.draft_results_announcement(felix)
        theirs = snapshot.draft_results_announcement(_author("robin"))
        assert theirs.pk == mine.pk
        assert AnnouncementDraft.objects.count() == 1
        theirs.refresh_from_db()
        assert theirs.author == felix

    def it_returns_the_snapshot_jobs_draft():
        snapshot = _snapshot()
        made = snapshot.make_results_draft()
        opened = snapshot.draft_results_announcement(_author())
        assert opened.pk == made.pk
        assert opened.author is None

    def it_returns_the_newest_edit_when_two_are_open():
        snapshot = _snapshot()
        older = snapshot._new_results_draft(_author("felix"))
        newer = snapshot._new_results_draft(_author("robin"))
        AnnouncementDraft.objects.filter(pk=older.pk).update(updated_at=timezone.now() - timedelta(hours=1))
        assert snapshot.draft_results_announcement(_author("sam")).pk == newer.pk

    def it_locks_the_snapshot_row_before_it_looks_for_an_open_draft(monkeypatch):
        """The lock the job takes too, so a click and the job in one instant make one draft."""
        from django.db.models import QuerySet

        steps: list[str] = []
        real_lock = QuerySet.select_for_update
        real_open = FundingSnapshot._open_results_draft

        def lock(self, *args, **kwargs):
            steps.append(f"lock {self.model.__name__}")
            return real_lock(self, *args, **kwargs)

        def open_draft(self):
            steps.append("look")
            return real_open(self)

        monkeypatch.setattr(QuerySet, "select_for_update", lock)
        monkeypatch.setattr(FundingSnapshot, "_open_results_draft", open_draft)
        _snapshot().draft_results_announcement(_author())
        assert steps[:2] == ["lock FundingSnapshot", "look"]

    def it_refuses_a_second_draft_while_one_is_sending():
        """A stale tab must not open a second draft that could never send."""
        snapshot = _snapshot()
        snapshot.draft_results_announcement(_author("felix")).queue_send()
        with pytest.raises(ResultsAlreadySentError, match="These results are already sending."):
            snapshot.draft_results_announcement(_author("robin"))
        assert AnnouncementDraft.objects.count() == 1

    def it_reopens_the_draft_the_queue_gave_up_on():
        author = _author()
        snapshot = _snapshot()
        draft = snapshot.draft_results_announcement(author)
        draft.queue_send()
        AnnouncementDraft.objects.filter(pk=draft.pk).update(send_requested_at=None, send_error="provider down")
        assert snapshot.draft_results_announcement(author).pk == draft.pk

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
        assert FundingSnapshot.objects.get(pk=snapshot.pk).results_draft_created_at is None

    def it_stamps_the_snapshot_so_the_job_never_makes_it_again():
        snapshot = _snapshot()
        with patch("airtable_sync.service.sync_snapshot_to_airtable") as sync:
            snapshot.draft_results_announcement(_author())
        sync.assert_not_called()
        assert FundingSnapshot.objects.get(pk=snapshot.pk).results_draft_created_at is not None

    def it_keeps_the_first_stamp_on_a_later_click():
        snapshot = _snapshot()
        snapshot.draft_results_announcement(_author("felix"))
        first = FundingSnapshot.objects.get(pk=snapshot.pk).results_draft_created_at
        FundingSnapshot.objects.filter(pk=snapshot.pk).update(results_draft_created_at=first - timedelta(days=1))
        snapshot.draft_results_announcement(_author("robin"))
        assert FundingSnapshot.objects.get(pk=snapshot.pk).results_draft_created_at == first - timedelta(days=1)

    def it_refuses_a_snapshot_with_no_per_guild_results():
        snapshot = FundingSnapshotFactory(cycle_label="Legacy", results={})
        with pytest.raises(NoResultsToAnnounceError, match="'Legacy' has no results to announce."):
            snapshot.draft_results_announcement(_author())
        assert not AnnouncementDraft.objects.exists()
        assert FundingSnapshot.objects.get(pk=snapshot.pk).results_draft_created_at is None


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
        # Discord shows "<" and "&" as typed; ">" is escaped with the other markdown characters.
        assert f"🥇 {_bar(12)} **B&W <Darkroom\\>**: $1,000.00 (100.0%)" in snapshot.allocation_discord_block()

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

    def it_escapes_discord_markdown_in_a_guild_name():
        snapshot = _snapshot(rows=[_row("Wood`work **Bold**", "600.00", 60.0), _row("a_b|c~d>e\\f", "400.00", 40.0)])
        lines = snapshot.allocation_discord_block().split("\n")
        assert lines[1] == f"🥇 {_bar(12)} **Wood\\`work \\*\\*Bold\\*\\***: $600.00 (60.0%)"
        assert lines[2] == f"🥈 {_bar(8)} **a\\_b\\|c\\~d\\>e\\\\f**: $400.00 (40.0%)"

    def describe_with_a_length_limit():
        def _forty() -> FundingSnapshot:
            rows = [
                _row(
                    f"The Very Long Named Guild Of Example Crafts And Fine Woodworking Restoration Society Number {n:02d}",
                    "25.00",
                    2.5,
                )
                for n in range(40)
            ]
            return _snapshot(rows=rows)

        def it_leaves_a_block_that_fits_alone():
            snapshot = _four_guild_snapshot()
            assert snapshot.allocation_discord_block(max_length=4096) == snapshot.allocation_discord_block()

        def it_drops_whole_guild_lines_from_the_bottom_and_says_how_many():
            snapshot = _forty()
            full = snapshot.allocation_discord_block().split("\n")
            block = snapshot.allocation_discord_block(max_length=2000)
            lines = block.split("\n")
            assert len(block) <= 2000
            kept = lines[1:-1]
            assert kept == full[1 : 1 + len(kept)]
            assert lines[-1] == f"And {40 - len(kept)} more guilds on the voting results page."

        def it_keeps_only_the_heading_and_the_note_when_no_guild_line_fits():
            assert _four_guild_snapshot().allocation_discord_block(max_length=10) == (
                "**How the $1,000.00 funding pool was split**\nAnd 4 more guilds on the voting results page."
            )

        def it_says_guild_when_only_one_is_left_out():
            snapshot = _snapshot(
                rows=[
                    _row("Alpha", "600.00", 60.0),
                    _row("Delta Woodworking and Furniture Restoration Guild", "400.00", 40.0),
                ]
            )
            full = snapshot.allocation_discord_block()
            last_line = full.split("\n")[-1]
            note = "And 1 more guild on the voting results page."
            block = snapshot.allocation_discord_block(max_length=len(full) - len(last_line) + len(note))
            assert block.split("\n")[-1] == note
            assert last_line not in block

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
        def it_posts_the_message_then_the_results_block(settings):
            settings.MEMBER_BASE_URL = "https://members.example"
            draft = _draft()
            message = draft.build_discord_message("https://members.example/")
            prose = (
                "The votes for September 2026 are in. 12 members voted on how the $1,000.00 guild funding pool "
                "is split. Thank you to everyone who voted. See the full breakdown on the voting results page."
            )
            block = draft.funding_snapshot.allocation_discord_block()
            assert message.title == "September 2026 Voting Results"
            assert message.body == f"{prose}\n\n{block}"
            assert message.url == "https://members.example/guilds/voting/history/"
            assert message.trigger_kind == "site_announcement"

        def it_shortens_a_long_message_so_the_results_always_fit():
            draft = _draft(body="<p>" + "word " * 1500 + "</p>")
            body = draft.build_discord_message("https://members.example/").body
            block = draft.funding_snapshot.allocation_discord_block()
            assert len(body) <= 4096
            assert body.endswith(f"…\n\n{block}")

        def it_keeps_the_whole_message_and_drops_guild_lines_for_forty_long_names():
            rows = [
                _row(
                    f"The Very Long Named Guild Of Example Crafts And Fine Woodworking Restoration Society Number {n:02d}",
                    "25.00",
                    2.5,
                )
                for n in range(40)
            ]
            draft = _snapshot(rows=rows).draft_results_announcement(_author("forty"))
            body = draft.build_discord_message("https://members.example/").body
            prose = (
                "The votes for September 2026 are in. 12 members voted on how the $1,000.00 guild funding pool "
                "is split. Thank you to everyone who voted. See the full breakdown on the voting results page."
            )
            assert len(body) <= 4096
            assert body.startswith(prose + "\n\n**How the $1,000.00 funding pool was split**\n")
            lines = body.split("\n")
            assert lines[-1].startswith("And ") and lines[-1].endswith(" more guilds on the voting results page.")
            assert all(line.endswith("(2.5%)") for line in lines[3:-1])

        def it_hands_the_bold_and_the_bars_to_discord_unescaped():
            message = _draft().build_discord_message("https://members.example/")
            description = build_embed_payload(message)["embeds"][0]["description"]
            assert description == message.body
            assert "**Metal Guild**" in description
            assert f"🥇 {_bar(12)}" in description

        def it_points_every_tap_but_the_emails_at_the_voting_results_page(settings):
            from core.events.channels import Channel

            settings.MEMBER_BASE_URL = "https://members.example"
            overrides = _draft()._channel_overrides("https://members.example/")
            results_page = "https://members.example/guilds/voting/history/"
            assert overrides[Channel.IN_APP].url == results_page
            assert overrides[Channel.PUSH].url == results_page
            assert overrides[Channel.DISCORD].url == results_page
            assert overrides[Channel.EMAIL].url == "https://members.example/"

        def it_leaves_a_plain_drafts_taps_on_the_site():
            plain = AnnouncementDraft(author=_author("plain"), title="Makerspace Announcement", body="<p>Hello</p>")
            overrides = plain._channel_overrides("https://members.example/")
            assert {message.url for message in overrides.values()} == {"https://members.example/"}

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

    def it_starts_the_attempts_and_error_afresh_when_queued_again():
        draft = _site_draft()
        AnnouncementDraft.objects.filter(pk=draft.pk).update(send_attempts=3, send_error="provider down")
        draft.refresh_from_db()
        draft.queue_send()
        draft.refresh_from_db()
        assert draft.send_attempts == 0
        assert draft.send_error == ""
        assert draft.send_requested_at is not None

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
        assert draft not in AnnouncementDraft.objects.resumable()
        assert list(AnnouncementDraft.objects.queued()) == [draft]

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
            # A second open results draft for one snapshot can only be one made before drafts were
            # shared, so it is built directly; the queue still refuses it.
            snapshot = _snapshot()
            first = snapshot.draft_results_announcement(_author("felix"))
            second = snapshot._new_results_draft(_author("robin"))
            first.queue_send()
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

        def it_blocks_deleting_the_snapshot_only_while_queued():
            snapshot = _snapshot()
            draft = snapshot.draft_results_announcement(_author())
            assert snapshot.deletion_blocker == ""
            draft.queue_send()
            assert snapshot.deletion_blocker == (
                "Its results announcement is sending. Delete it after the announcement has gone out."
            )

        def it_says_why_the_newest_given_up_draft_failed():
            snapshot = _snapshot()
            older = snapshot._new_results_draft(_author("felix"))
            newer = snapshot._new_results_draft(_author("robin"))
            AnnouncementDraft.objects.filter(pk=older.pk).update(send_error="old trouble.")
            AnnouncementDraft.objects.filter(pk=newer.pk).update(send_error="Provider down.")
            AnnouncementDraft.objects.filter(pk=older.pk).update(updated_at=timezone.now() - timedelta(hours=1))
            assert snapshot.results_announcement_failure == "Provider down"

        def it_reports_no_failure_while_queued_or_untried():
            snapshot = _snapshot()
            draft = snapshot.draft_results_announcement(_author())
            assert snapshot.results_announcement_failure == ""
            draft.queue_send()
            AnnouncementDraft.objects.filter(pk=draft.pk).update(send_error="provider down")
            assert snapshot.results_announcement_failure == ""

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

    def it_stamps_the_snapshots_shared_period_as_the_delivery_period():
        _activated("reader")
        snapshot = _snapshot()
        draft = snapshot.draft_results_announcement(_author())
        draft.send()
        draft.refresh_from_db()
        assert draft.delivery_period == f"announce:results:{snapshot.pk}"
        assert EventDelivery.objects.filter(event_key="site_announcement", period=draft.delivery_period).exists()

    def it_refuses_at_send_when_another_draft_already_sent_the_results(mailoutbox):
        _activated("reader")
        snapshot = _snapshot()
        mine = snapshot.draft_results_announcement(_author("felix"))
        theirs = snapshot._new_results_draft(_author("robin"))
        mine.send()
        mailoutbox.clear()
        with pytest.raises(ResultsAlreadySentError, match="These results were already sent."):
            theirs.send()
        assert mailoutbox == []
        theirs.refresh_from_db()
        assert theirs.sent_at is None

    def describe_pointed_at_anyone_but_everyone():
        def it_refuses_to_send_a_guild_retarget_and_stamps_nothing(mailoutbox):
            guild = GuildFactory()
            snapshot = _snapshot()
            draft = snapshot.draft_results_announcement(_author())
            draft.audience = AnnouncementDraft.Audience.GUILD
            draft.guild = guild
            with pytest.raises(ValidationError, match="A results announcement goes to everyone."):
                draft.send()
            snapshot.refresh_from_db()
            assert snapshot.results_sent_at is None
            assert not GuildAnnouncement.objects.exists()
            assert mailoutbox == []

        def it_refuses_to_queue_a_class_retarget():
            from classes.factories import ClassOfferingFactory

            draft = _snapshot().draft_results_announcement(_author())
            draft.audience = AnnouncementDraft.Audience.CLASS
            draft.class_offering = ClassOfferingFactory()
            with pytest.raises(ValidationError, match="A results announcement goes to everyone."):
                draft.queue_send()
            assert AnnouncementDraft.objects.get(pk=draft.pk).send_requested_at is None

        def it_refuses_to_save_a_retarget_from_the_form():
            import types

            guild = GuildFactory()
            author = _author()
            draft = _snapshot().draft_results_announcement(author)
            cleaned = {
                "audience": AnnouncementDraft.Audience.GUILD,
                "guild": guild,
                "body": draft.body,
                "send_email": True,
                "discord_channel": "none",
                "mention": "none",
            }
            with pytest.raises(ValidationError, match="A results announcement goes to everyone."):
                AnnouncementDraft.save_from_form(types.SimpleNamespace(cleaned_data=cleaned), author, instance=draft)
            draft.refresh_from_db()
            assert draft.audience == AnnouncementDraft.Audience.SITE
            assert draft.guild is None

    def it_points_the_bell_and_the_discord_title_at_the_voting_results_page(settings):
        settings.MEMBER_BASE_URL = "https://members.example"
        _general_webhook()
        reader = _activated("reader")
        draft = _snapshot().draft_results_announcement(_author())
        with respx.mock:
            route = respx.post(_WEBHOOK).mock(return_value=httpx.Response(204))
            draft.send()
        payload = json.loads(route.calls.last.request.content)
        assert payload["embeds"][0]["url"] == "https://members.example/guilds/voting/history/"
        bell = Notification.objects.get(user=reader, trigger="site_announcement")
        assert bell.url == "https://members.example/guilds/voting/history/"

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

    def it_reaches_only_the_missed_members_when_sent_again_after_the_queue_gave_up():
        from core.events.channels import EmailAdapter

        _general_webhook()
        readers = _three_readers()
        draft = _queued_results_draft()
        real_deliver = EmailAdapter.deliver

        def provider_down_for_bo(self, user, message, *, attachments=None):
            if user.email == "bo@x.com":
                raise RuntimeError("provider down")
            return real_deliver(self, user, message, attachments=attachments)

        with respx.mock:
            route = respx.post(_WEBHOOK).mock(return_value=httpx.Response(204))
            with patch.object(EmailAdapter, "deliver", provider_down_for_bo):
                for _attempt in range(3):
                    with pytest.raises(CommandError):
                        call_command("send_queued_announcements")
            draft.refresh_from_db()
            assert draft.send_requested_at is None
            assert draft.sent_at is None
            assert draft.send_attempts == 3
            assert draft.send_error == "provider down"
            assert draft.funding_snapshot.results_announcement_failure == "provider down"

            draft.queue_send()
            call_command("send_queued_announcements")

            assert route.call_count == 1
        for reader in readers:
            assert _sent_to(reader.email) == 1
        draft.refresh_from_db()
        assert draft.sent_at is not None
        assert draft.send_error == ""
        assert draft.funding_snapshot.results_sent_at is not None

    def it_reaches_nobody_twice_when_another_admins_draft_sends_after_the_first_gave_up():
        """The reviewer's double send: a second admin's fresh draft must share the first one's slots."""
        from core.events.channels import EmailAdapter

        _general_webhook()
        readers = _three_readers()
        snapshot = _snapshot()
        felix_draft = snapshot.draft_results_announcement(_author("felix"))
        felix_draft.queue_send()
        real_deliver = EmailAdapter.deliver

        def provider_down_for_bo(self, user, message, *, attachments=None):
            if user.email == "bo@x.com":
                raise RuntimeError("provider down")
            return real_deliver(self, user, message, attachments=attachments)

        with respx.mock:
            route = respx.post(_WEBHOOK).mock(return_value=httpx.Response(204))
            with patch.object(EmailAdapter, "deliver", provider_down_for_bo):
                for _attempt in range(3):
                    with pytest.raises(CommandError):
                        call_command("send_queued_announcements")
            felix_draft.refresh_from_db()
            assert felix_draft.send_given_up is True

            # A second admin's own draft, as could exist from before drafts were shared.
            robin_draft = snapshot._new_results_draft(_author("robin"))
            assert robin_draft.pk != felix_draft.pk
            robin_draft.queue_send()
            call_command("send_queued_announcements")

            assert route.call_count == 1
        for reader in readers:
            assert _sent_to(reader.email) == 1
            assert Notification.objects.filter(user=reader, trigger="site_announcement").count() == 1
        robin_draft.refresh_from_db()
        snapshot.refresh_from_db()
        assert robin_draft.sent_at is not None
        assert snapshot.results_sent_at is not None
        assert snapshot.results_send_count == 1

    def it_posts_to_discord_once_when_another_admins_draft_sends_after_the_first_posted():
        _general_webhook()
        readers = _three_readers()
        snapshot = _snapshot()
        felix_draft = snapshot.draft_results_announcement(_author("felix"))
        felix_draft.queue_send()

        with respx.mock:
            route = respx.post(_WEBHOOK).mock(return_value=httpx.Response(204))
            with patch.object(FundingSnapshot, "mark_results_announced", side_effect=RuntimeError("worker killed")):
                for _attempt in range(3):
                    with pytest.raises(CommandError):
                        call_command("send_queued_announcements")
            assert route.call_count == 1

            robin_draft = snapshot._new_results_draft(_author("robin"))
            robin_draft.queue_send()
            call_command("send_queued_announcements")

            assert route.call_count == 1
        for reader in readers:
            assert _sent_to(reader.email) == 1
            assert Notification.objects.filter(user=reader, trigger="site_announcement").count() == 1
        snapshot.refresh_from_db()
        assert snapshot.results_sent_at is not None


def describe_site_delivery_period():
    def it_keys_a_plain_draft_on_its_own_pk():
        draft = AnnouncementDraft.objects.create(author=_author(), title="Makerspace Announcement", body="<p>x</p>")
        assert draft._site_delivery_period() == f"announce:{draft.pk}"

    def it_keys_every_results_draft_for_one_snapshot_on_the_snapshot():
        snapshot = _snapshot()
        felix = snapshot.draft_results_announcement(_author("felix"))
        robin = snapshot._new_results_draft(_author("robin"))
        assert felix._site_delivery_period() == f"announce:results:{snapshot.pk}"
        assert robin._site_delivery_period() == felix._site_delivery_period()


def describe_make_results_draft():
    """The snapshot job's results draft: made once per snapshot, with nobody as its author."""

    def it_makes_one_draft_identical_to_a_clicked_one_with_a_blank_author():
        snapshot = _snapshot()
        made = snapshot.make_results_draft()
        clicked = _snapshot(label="October 2026").draft_results_announcement(_author())
        made.refresh_from_db()
        assert made.author is None
        assert made.funding_snapshot == snapshot
        assert made.title == "September 2026 Voting Results"
        assert made.body == snapshot.results_announcement_body()
        same = (
            "audience",
            "push_message",
            "push_enabled",
            "send_email",
            "discord_enabled",
            "discord_channel",
            "mention",
            "show_sender",
            "mark_as_urgent",
        )
        expected = {field: getattr(clicked, field) for field in same}
        expected["push_message"] = snapshot.results_announcement_push
        assert {field: getattr(made, field) for field in same} == expected
        assert (made.sent_at, made.send_requested_at) == (None, None)

    def it_stamps_the_snapshot_without_pushing_it_to_airtable():
        snapshot = _snapshot()
        with patch("airtable_sync.service.sync_snapshot_to_airtable") as sync:
            snapshot.make_results_draft()
        sync.assert_not_called()
        stamped = snapshot.results_draft_created_at
        snapshot.refresh_from_db()
        assert snapshot.results_draft_created_at is not None
        assert snapshot.results_draft_created_at == stamped

    def it_makes_nothing_the_second_time():
        snapshot = _snapshot()
        snapshot.make_results_draft()
        assert snapshot.make_results_draft() is None
        assert AnnouncementDraft.objects.count() == 1

    def it_never_makes_a_draft_an_admin_opened_and_then_deleted():
        """The click, delete, tick sequence: the click stamps, so the tick after the delete makes nothing."""
        snapshot = _snapshot()
        snapshot.draft_results_announcement(_author()).delete()
        assert FundingSnapshot.make_newest_results_draft() is None
        assert not AnnouncementDraft.objects.exists()

    def it_never_makes_it_again_after_it_is_deleted():
        snapshot = _snapshot()
        snapshot.make_results_draft().delete()
        assert snapshot.make_results_draft() is None
        assert FundingSnapshot.objects.get(pk=snapshot.pk).make_results_draft() is None
        assert not AnnouncementDraft.objects.exists()

    def it_stamps_and_makes_nothing_when_an_admin_opened_one_first():
        snapshot = _snapshot()
        # Built directly, not clicked: a click now stamps the snapshot itself, and this is the
        # unstamped open draft the release before this one leaves behind.
        opened = snapshot._new_results_draft(_author())
        assert FundingSnapshot.objects.get(pk=snapshot.pk).results_draft_created_at is None
        assert snapshot.make_results_draft() is None
        assert list(AnnouncementDraft.objects.all()) == [opened]
        snapshot.refresh_from_db()
        assert snapshot.results_draft_created_at is not None
        opened.delete()
        assert snapshot.make_results_draft() is None

    def it_makes_nothing_and_stamps_nothing_once_the_results_went_out():
        snapshot = _snapshot()
        FundingSnapshot.objects.filter(pk=snapshot.pk).update(results_sent_at=timezone.now())
        assert snapshot.make_results_draft() is None
        assert not AnnouncementDraft.objects.exists()
        assert FundingSnapshot.objects.get(pk=snapshot.pk).results_draft_created_at is None

    def it_makes_nothing_and_stamps_nothing_while_the_results_are_sending():
        snapshot = _snapshot()
        snapshot._new_results_draft(_author()).queue_send()
        assert snapshot.make_results_draft() is None
        assert AnnouncementDraft.objects.count() == 1
        assert FundingSnapshot.objects.get(pk=snapshot.pk).results_draft_created_at is None

    def it_makes_nothing_and_stamps_nothing_without_per_guild_results():
        snapshot = FundingSnapshotFactory(cycle_label="Legacy", results={})
        assert snapshot.make_results_draft() is None
        assert not AnnouncementDraft.objects.exists()
        assert FundingSnapshot.objects.get(pk=snapshot.pk).results_draft_created_at is None

    def it_locks_the_snapshot_row(monkeypatch):
        from django.db.models import QuerySet

        locked: list[str] = []
        real_lock = QuerySet.select_for_update

        def lock(self, *args, **kwargs):
            locked.append(self.model.__name__)
            return real_lock(self, *args, **kwargs)

        monkeypatch.setattr(QuerySet, "select_for_update", lock)
        _snapshot().make_results_draft()
        assert locked == ["FundingSnapshot"]

    def it_lets_whoever_sends_it_become_the_sender(mailoutbox):
        """The made draft names nobody; Send through the composer saves first, so the sender is named."""
        from core.models import SiteActivity

        reader = _activated("reader")
        sender = _author("sam")
        made = _snapshot().make_results_draft()
        made.author = sender
        made.save(update_fields=["author"])
        made.queue_send()
        call_command("send_queued_announcements")
        made.refresh_from_db()
        assert made.sent_at is not None
        assert "From Felix" in mailoutbox[0].body
        assert reader.email in mailoutbox[0].to
        assert SiteActivity.objects.filter(actor=sender).exists()


def describe_make_newest_results_draft():
    def _aged(snapshot: FundingSnapshot, days: int) -> FundingSnapshot:
        FundingSnapshot.objects.filter(pk=snapshot.pk).update(snapshot_at=timezone.now() - timedelta(days=days))
        snapshot.refresh_from_db()
        return snapshot

    def it_makes_the_newest_snapshots_draft_only():
        august = _aged(_snapshot(label="August 2026"), 30)
        september = _snapshot()
        made = FundingSnapshot.make_newest_results_draft()
        assert made.funding_snapshot == september
        assert not AnnouncementDraft.objects.filter(funding_snapshot=august).exists()

    def it_makes_none_for_an_older_snapshot_after_the_newest_is_sent():
        august = _aged(_snapshot(label="August 2026"), 30)
        september = _snapshot()
        FundingSnapshot.make_newest_results_draft()
        FundingSnapshot.objects.filter(pk=september.pk).update(results_sent_at=timezone.now())
        assert FundingSnapshot.make_newest_results_draft() is None
        assert not AnnouncementDraft.objects.filter(funding_snapshot=august).exists()

    def it_makes_nothing_without_any_snapshot():
        assert FundingSnapshot.make_newest_results_draft() is None
