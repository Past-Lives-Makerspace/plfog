"""Ex-member Discord removal (#898): the queue, the warning DM, the kick, and the 15-minute pass.

Discord HTTP is mocked with ``respx``. The pass is driven with an explicit ``now`` so the wait
between the warning and the removal (one weekly pass to the next) is exact.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx
from django.core.management import call_command

from core.models import AutomationAction, SiteConfiguration
from core.scheduled_jobs import Trigger, record_run
from membership.discord_removal import RemovalPassError, process_removals, put_back_if_removed, run_cleanup_list
from membership.models import (
    DISCORD_CLEANUP_TEXT,
    DISCORD_REMOVAL_TASK_KEY,
    DiscordRemoval,
    GuildMembership,
    Member,
    RemovalNotOpenError,
)
from tests.membership.factories import GuildFactory, GuildMembershipFactory, MemberFactory, UserFactory

pytestmark = pytest.mark.django_db

PACIFIC = ZoneInfo("America/Los_Angeles")
DM_OPEN = "https://discord.com/api/v10/users/@me/channels"
DM_POST = "https://discord.com/api/v10/channels/dm1/messages"
KICK_RE = r"https://discord\.com/api/v10/guilds/srv1/members/\d+"


@pytest.fixture(autouse=True)
def _discord(settings):
    settings.DISCORD_BOT_TOKEN = "bot-tok"
    config = SiteConfiguration.load()
    config.discord_server_id = "srv1"
    config.save()


def _morning(day: date, hour: int = 10) -> datetime:
    return datetime(day.year, day.month, day.day, hour, 0, tzinfo=PACIFIC)


def _former(discord_user_id: str = "111", **kwargs: object) -> Member:
    """A linked active member who then turns Former, which queues them."""
    member = MemberFactory(discord_user_id=discord_user_id, discord_handle="maker", **kwargs)
    member.status = Member.Status.FORMER
    member.save()
    return member


def _mock_dm(status: int = 200) -> respx.Route:
    respx.post(DM_OPEN).mock(return_value=httpx.Response(200, json={"id": "dm1"}))
    return respx.post(DM_POST).mock(return_value=httpx.Response(status, json={}))


def describe_queueing_at_save():
    def it_queues_a_linked_member_who_turns_former():
        member = _former(cancellation_date=date(2026, 10, 20))
        removal = DiscordRemoval.objects.get()
        assert removal.member == member
        assert removal.discord_user_id == "111"
        assert removal.discord_username == "maker"
        assert removal.source == DiscordRemoval.Source.FORMER
        assert removal.status == DiscordRemoval.Status.QUEUED
        assert removal.warn_on == date(2026, 10, 21)
        action = AutomationAction.objects.get()
        assert action.task_key == DISCORD_REMOVAL_TASK_KEY
        assert action.kind == AutomationAction.Kind.REMOVAL_QUEUED
        assert action.member == member
        assert "October 21, 2026" in action.detail

    def it_counts_from_today_when_there_is_no_cancellation_date(monkeypatch):
        monkeypatch.setattr("django.utils.timezone.localdate", lambda: date(2026, 10, 23))
        _former(cancellation_date=None)
        assert DiscordRemoval.objects.get().warn_on == date(2026, 10, 24)

    def it_queues_when_the_save_names_status_in_update_fields():
        member = MemberFactory(discord_user_id="111")
        member.status = Member.Status.FORMER
        member.save(update_fields=["status"])
        assert DiscordRemoval.objects.count() == 1

    def it_skips_a_save_whose_update_fields_leave_status_out():
        member = MemberFactory(discord_user_id="111")
        member.status = Member.Status.FORMER
        member.save(update_fields=["phone"])
        assert not DiscordRemoval.objects.exists()

    def it_skips_a_member_without_discord():
        _former(discord_user_id="")
        assert not DiscordRemoval.objects.exists()

    def it_skips_a_member_who_was_already_former():
        member = _former()
        member.phone = "555"
        member.save()
        assert DiscordRemoval.objects.count() == 1

    def it_skips_a_member_created_former():
        MemberFactory(discord_user_id="111", status=Member.Status.FORMER)
        assert not DiscordRemoval.objects.exists()

    def it_skips_suspended():
        member = MemberFactory(discord_user_id="111")
        member.status = Member.Status.SUSPENDED
        member.save()
        assert not DiscordRemoval.objects.exists()

    def it_queues_one_open_row_per_discord_account():
        member = _former()
        member.status = Member.Status.ACTIVE
        member.save()
        member.status = Member.Status.FORMER
        member.save()
        assert DiscordRemoval.objects.count() == 1


def describe_queryset():
    def it_finds_due_warnings_and_kicks():
        due = _former(discord_user_id="1", cancellation_date=date(2026, 10, 1))
        _former(discord_user_id="2", cancellation_date=date(2026, 10, 30))
        today = date(2026, 10, 23)
        assert [r.member for r in DiscordRemoval.objects.due_warnings(today)] == [due]
        now = _morning(today)
        row = DiscordRemoval.objects.get(member=due)
        row.status = DiscordRemoval.Status.WARNED
        row.kick_after = now - timedelta(minutes=1)
        row.save()
        assert list(DiscordRemoval.objects.due_kicks(now)) == [row]
        assert not DiscordRemoval.objects.due_kicks(now - timedelta(hours=1)).exists()
        assert DiscordRemoval.objects.open().count() == 2


def describe_removal():
    def it_names_a_cleanup_row_by_username_then_id():
        row = DiscordRemoval(discord_user_id="9", discord_username="ghost", source=DiscordRemoval.Source.CLEANUP)
        assert row.subject == "ghost"
        row.discord_username = ""
        assert row.subject == "9"
        assert str(row) == "Discord removal 9 (queued)"

    @pytest.mark.parametrize(
        ("status", "label"),
        [
            (Member.Status.ACTIVE, "Active"),
            (Member.Status.SUSPENDED, "Suspended"),
            (Member.Status.INVITED, "Invited"),
            (Member.Status.GUEST, "Guest"),
        ],
    )
    def it_keeps_a_member_who_is_no_longer_former(status: str, label: str):
        # Airtable's Paused maps to Suspended and Pending to Invited: only Former is removable.
        member = _former()
        Member.objects.filter(pk=member.pk).update(status=status)
        assert DiscordRemoval.objects.get().keep_reason() == f"No longer Former: now {label}."

    def it_keeps_an_id_connected_to_another_active_member():
        _former()
        MemberFactory(discord_user_id="111", status=Member.Status.ACTIVE)
        assert DiscordRemoval.objects.get().keep_reason() == "Their Discord is connected to an active member."

    def it_goes_ahead_otherwise():
        _former()
        assert DiscordRemoval.objects.get().keep_reason() == ""

    def it_lets_an_admin_cancel_an_open_row():
        _former()
        admin = UserFactory()
        removal = DiscordRemoval.objects.get()
        removal.cancel(by=admin)
        removal.refresh_from_db()
        assert removal.status == DiscordRemoval.Status.CANCELLED
        assert removal.closed_by == admin
        assert removal.closed_at is not None
        action = AutomationAction.objects.get(kind=AutomationAction.Kind.REMOVAL_CANCELLED)
        assert action.actor == admin
        assert action.run is None

    def it_refuses_to_cancel_a_closed_row():
        _former()
        removal = DiscordRemoval.objects.get()
        removal.close("Kept.")
        with pytest.raises(RemovalNotOpenError, match=r"already ran \(Closed\), so nothing was cancelled"):
            removal.cancel(by=UserFactory())

    def it_cancels_under_a_lock_by_pk():
        _former()
        admin = UserFactory()
        removal = DiscordRemoval.objects.cancel_locked(DiscordRemoval.objects.get().pk, by=admin)
        assert (removal.status, removal.closed_by) == (DiscordRemoval.Status.CANCELLED, admin)

    def it_will_not_cancel_a_removal_the_job_already_ran():
        removal = _warned()
        DiscordRemoval.objects.filter(pk=removal.pk).update(status=DiscordRemoval.Status.KICKED)
        with pytest.raises(RemovalNotOpenError, match=r"already ran \(Removed\)"):
            DiscordRemoval.objects.cancel_locked(removal.pk, by=UserFactory())
        removal.refresh_from_db()
        assert removal.status == DiscordRemoval.Status.KICKED
        assert not AutomationAction.objects.filter(kind=AutomationAction.Kind.REMOVAL_CANCELLED).exists()

    def it_raises_for_an_unknown_pk():
        with pytest.raises(DiscordRemoval.DoesNotExist):
            DiscordRemoval.objects.cancel_locked(999999, by=UserFactory())

    def it_names_the_support_email_in_the_warning():
        _former()
        assert "info@pastlives.space" in DiscordRemoval.objects.get().warning_text()
        assert "within the next week" in DiscordRemoval.objects.get().warning_text()

    def it_falls_back_to_an_admin_without_a_support_email():
        config = SiteConfiguration.load()
        config.org_support_email = ""
        config.save()
        _former()
        assert "contact a Past Lives admin" in DiscordRemoval.objects.get().warning_text()


def describe_send_warning():
    @respx.mock
    def it_says_the_next_day_and_waits_a_day_when_the_job_runs_daily():
        from core.factories import ScheduledJobStateFactory

        ScheduledJobStateFactory(task_key="process_discord_removals", cadence="daily")
        _former()
        post = _mock_dm()
        removal = DiscordRemoval.objects.get()
        now = _morning(date(2026, 10, 23))
        assert removal.send_warning(now) is True
        assert "within the next day" in post.calls.last.request.read().decode()
        assert removal.kick_after == now + timedelta(hours=20)

    @respx.mock
    def it_dms_and_waits_until_the_next_weekly_pass():
        _former()
        post = _mock_dm()
        removal = DiscordRemoval.objects.get()
        now = _morning(date(2026, 10, 23))
        assert removal.send_warning(now) is True
        assert "within the next week" in post.calls.last.request.read().decode()
        removal.refresh_from_db()
        assert removal.status == DiscordRemoval.Status.WARNED
        assert removal.warned_at == now
        assert removal.kick_after == now + timedelta(days=6)
        action = AutomationAction.objects.get(kind=AutomationAction.Kind.DISCORD_DM)
        assert (action.result, action.detail) == (AutomationAction.Result.OK, "Delivered.")

    @respx.mock
    def it_still_starts_the_wait_when_dms_are_closed():
        _former()
        _mock_dm(status=403)
        removal = DiscordRemoval.objects.get()
        assert removal.send_warning(_morning(date(2026, 10, 23))) is True
        assert removal.status == DiscordRemoval.Status.WARNED
        action = AutomationAction.objects.get(kind=AutomationAction.Kind.DISCORD_DM)
        assert action.result == AutomationAction.Result.FAILED
        assert action.detail.startswith("DM not delivered:")
        assert "removal still follows" in action.detail

    @respx.mock
    def it_stays_queued_on_a_discord_error():
        _former()
        _mock_dm(status=500)
        removal = DiscordRemoval.objects.get()
        assert removal.send_warning(_morning(date(2026, 10, 23))) is False
        removal.refresh_from_db()
        assert removal.status == DiscordRemoval.Status.QUEUED
        action = AutomationAction.objects.get(kind=AutomationAction.Kind.DISCORD_DM)
        assert action.result == AutomationAction.Result.FAILED
        assert action.detail.startswith("Will retry:")

    @respx.mock
    def it_closes_instead_of_warning_a_kept_member():
        member = _former()
        Member.objects.filter(pk=member.pk).update(status=Member.Status.ACTIVE)
        removal = DiscordRemoval.objects.get()
        assert removal.send_warning(_morning(date(2026, 10, 23))) is True
        assert removal.status == DiscordRemoval.Status.CLOSED
        assert not respx.calls
        closed = AutomationAction.objects.get(kind=AutomationAction.Kind.REMOVAL_CLOSED)
        assert closed.detail == "No longer Former: now Active."


def _warned() -> DiscordRemoval:
    _former()
    removal = DiscordRemoval.objects.get()
    removal.status = DiscordRemoval.Status.WARNED
    removal.kick_after = _morning(date(2026, 10, 23))
    removal.save()
    return removal


def describe_remove_from_server():
    @respx.mock
    def it_removes_them():
        removal = _warned()
        route = respx.delete(url__regex=KICK_RE).mock(return_value=httpx.Response(204))
        now = _morning(date(2026, 10, 24))
        assert removal.remove_from_server("srv1", now) is True
        assert route.calls.last.request.url.path.endswith("/members/111")
        assert route.calls.last.request.headers["X-Audit-Log-Reason"] == "Past Lives: Membership ended"
        removal.refresh_from_db()
        assert (removal.status, removal.kicked_at) == (DiscordRemoval.Status.KICKED, now)
        action = AutomationAction.objects.get(kind=AutomationAction.Kind.DISCORD_KICK)
        assert (action.result, action.detail) == (AutomationAction.Result.OK, "Removed.")

    @respx.mock
    def it_counts_someone_already_gone_as_removed():
        removal = _warned()
        respx.delete(url__regex=KICK_RE).mock(return_value=httpx.Response(404))
        assert removal.remove_from_server("srv1", _morning(date(2026, 10, 24))) is True
        assert removal.status == DiscordRemoval.Status.KICKED
        assert AutomationAction.objects.get(kind=AutomationAction.Kind.DISCORD_KICK).detail == (
            "Already gone from the server."
        )

    @respx.mock
    def it_stops_on_a_refusal():
        removal = _warned()
        respx.delete(url__regex=KICK_RE).mock(return_value=httpx.Response(403, text="Missing Permissions"))
        assert removal.remove_from_server("srv1", _morning(date(2026, 10, 24))) is False
        removal.refresh_from_db()
        assert removal.status == DiscordRemoval.Status.KICK_FAILED
        assert AutomationAction.objects.get(kind=AutomationAction.Kind.DISCORD_KICK).result == "failed"

    @respx.mock
    def it_stays_warned_on_a_discord_error():
        removal = _warned()
        respx.delete(url__regex=KICK_RE).mock(return_value=httpx.Response(503))
        assert removal.remove_from_server("srv1", _morning(date(2026, 10, 24))) is False
        removal.refresh_from_db()
        assert removal.status == DiscordRemoval.Status.WARNED
        assert AutomationAction.objects.get(kind=AutomationAction.Kind.DISCORD_KICK).detail.startswith("Will retry:")

    def it_does_nothing_without_a_bot_token(settings):
        removal = _warned()
        settings.DISCORD_BOT_TOKEN = ""
        assert removal.remove_from_server("srv1", _morning(date(2026, 10, 24))) is True
        assert removal.status == DiscordRemoval.Status.WARNED

    @respx.mock
    def it_closes_instead_of_removing_a_kept_member():
        removal = _warned()
        MemberFactory(discord_user_id="111", status=Member.Status.ACTIVE)
        assert removal.remove_from_server("srv1", _morning(date(2026, 10, 24))) is True
        assert removal.status == DiscordRemoval.Status.CLOSED
        assert not respx.calls

    @respx.mock
    def it_closes_instead_of_removing_a_member_paused_since_the_dm():
        removal = _warned()
        Member.objects.filter(pk=removal.member.pk).update(status=Member.Status.SUSPENDED)
        removal.refresh_from_db()
        assert removal.remove_from_server("srv1", _morning(date(2026, 10, 24))) is True
        assert removal.status == DiscordRemoval.Status.CLOSED
        assert not respx.calls


def describe_process_removals():
    def it_does_nothing_without_a_bot_token(settings):
        settings.DISCORD_BOT_TOKEN = ""
        _former(cancellation_date=date(2026, 10, 1))
        assert process_removals(_morning(date(2026, 10, 23))).ran is False
        assert DiscordRemoval.objects.get().status == DiscordRemoval.Status.QUEUED

    def it_does_nothing_on_staging(settings):
        settings.IS_STAGING = True
        _former(cancellation_date=date(2026, 10, 1))
        assert process_removals(_morning(date(2026, 10, 23))).ran is False

    def it_does_nothing_without_a_server_id():
        config = SiteConfiguration.load()
        config.discord_server_id = ""
        config.save()
        assert process_removals(_morning(date(2026, 10, 23))).ran is False

    @respx.mock
    def it_warns_on_one_weekly_pass_and_removes_on_the_next():
        _former(cancellation_date=date(2026, 10, 22))
        _mock_dm()
        kick = respx.delete(url__regex=KICK_RE).mock(return_value=httpx.Response(204))
        assert process_removals(_morning(date(2026, 10, 22))).handled == 0
        first = _morning(date(2026, 10, 26), hour=6)
        assert process_removals(first).handled == 1
        assert process_removals(first + timedelta(days=5)).handled == 0
        assert not kick.called
        assert process_removals(first + timedelta(days=7)).handled == 1
        assert kick.call_count == 1
        assert DiscordRemoval.objects.get().status == DiscordRemoval.Status.KICKED

    @respx.mock
    def it_sends_the_dm_at_the_early_weekly_run():
        _former(cancellation_date=date(2026, 10, 1))
        post = _mock_dm()
        assert process_removals(_morning(date(2026, 10, 26), hour=5)).handled == 1
        assert post.called

    @respx.mock
    def it_raises_after_the_pass_when_a_step_failed():
        _former(discord_user_id="1", cancellation_date=date(2026, 10, 1))
        _former(discord_user_id="2", cancellation_date=date(2026, 10, 1))
        respx.post(DM_OPEN).mock(side_effect=[httpx.Response(500), httpx.Response(200, json={"id": "dm1"})])
        respx.post(DM_POST).mock(return_value=httpx.Response(200, json={}))
        with pytest.raises(RemovalPassError, match="1 of 2"):
            process_removals(_morning(date(2026, 10, 23)))
        assert DiscordRemoval.objects.filter(status=DiscordRemoval.Status.WARNED).count() == 1

    @respx.mock
    def it_counts_a_failed_kick():
        _warned()
        respx.delete(url__regex=KICK_RE).mock(return_value=httpx.Response(403))
        with pytest.raises(RemovalPassError, match="1 of 1"):
            process_removals(_morning(date(2026, 10, 24)))

    @respx.mock
    def it_skips_a_row_another_pass_moved(monkeypatch):
        _former(cancellation_date=date(2026, 10, 1))
        removal = DiscordRemoval.objects.get()
        real = DiscordRemoval.objects.due_warnings

        def _stale(today: date):
            ids = list(real(today).values_list("pk", flat=True))
            DiscordRemoval.objects.filter(pk=removal.pk).update(status=DiscordRemoval.Status.CANCELLED)
            return DiscordRemoval.objects.filter(pk__in=ids)

        monkeypatch.setattr(DiscordRemoval.objects, "due_warnings", _stale)
        assert process_removals(_morning(date(2026, 10, 23))).handled == 0
        assert not respx.calls

    @respx.mock
    def it_skips_a_kick_another_pass_moved(monkeypatch):
        removal = _warned()
        real = DiscordRemoval.objects.due_kicks

        def _stale(now: datetime):
            ids = list(real(now).values_list("pk", flat=True))
            DiscordRemoval.objects.filter(pk=removal.pk).update(status=DiscordRemoval.Status.KICKED)
            return DiscordRemoval.objects.filter(pk__in=ids)

        monkeypatch.setattr(DiscordRemoval.objects, "due_kicks", _stale)
        assert process_removals(_morning(date(2026, 10, 24))).handled == 0
        assert not respx.calls

    @respx.mock
    def it_records_actions_under_the_open_run():
        _former(cancellation_date=date(2026, 10, 1))
        _mock_dm()
        with record_run(DISCORD_REMOVAL_TASK_KEY, trigger=Trigger.SCHEDULED) as run:
            process_removals(_morning(date(2026, 10, 23)))
        assert AutomationAction.objects.get(kind=AutomationAction.Kind.DISCORD_DM).run == run


def describe_command():
    def it_says_when_it_is_not_configured(settings, capsys):
        settings.DISCORD_BOT_TOKEN = ""
        call_command("process_discord_removals")
        assert "skipped" in capsys.readouterr().out

    def it_reports_the_steps(capsys):
        call_command("process_discord_removals")
        assert "Handled 0 Discord removal step(s)." in capsys.readouterr().out


JOIN = "https://discord.com/api/v10/guilds/srv1/members/111"
ROLE_RE = r"https://discord\.com/api/v10/guilds/srv1/members/111/roles/.+"


def _kicked(**kwargs: object) -> DiscordRemoval:
    removal = _warned()
    removal.status = DiscordRemoval.Status.KICKED
    removal.kicked_at = _morning(date(2026, 10, 24))
    removal.save()
    for key, value in kwargs.items():
        setattr(removal, key, value)
    removal.save()
    return removal


def describe_cleanup_rows():
    def it_queues_one_warned_today(monkeypatch):
        monkeypatch.setattr("django.utils.timezone.localdate", lambda: date(2026, 10, 23))
        removal = DiscordRemoval.objects.queue_for_cleanup("222", "ghost")
        assert (removal.source, removal.warn_on, removal.member) == (
            DiscordRemoval.Source.CLEANUP,
            date(2026, 10, 23),
            None,
        )
        action = AutomationAction.objects.get()
        assert (action.subject, action.detail) == ("ghost", "On the one-time cleanup list.")

    def it_uses_the_tickets_text_and_mentions_felix():
        removal = DiscordRemoval.objects.queue_for_cleanup("222", "ghost")
        assert removal.warning_text() == DISCORD_CLEANUP_TEXT
        assert "<@150063478210691081>" in DISCORD_CLEANUP_TEXT
        assert removal.warning_mentions() == ("150063478210691081",)

    def it_mentions_nobody_in_the_membership_ended_dm():
        _former()
        assert DiscordRemoval.objects.get().warning_mentions() == ()

    @respx.mock
    def it_sends_the_mention_and_waits_three_days():
        post = _mock_dm()
        removal = DiscordRemoval.objects.queue_for_cleanup("222", "ghost")
        now = _morning(date(2026, 10, 23))
        assert removal.send_warning(now) is True
        body = post.calls.last.request.read().decode()
        assert '"allowed_mentions":{"parse":[],"users":["150063478210691081"]}' in body
        assert removal.kick_after == now + timedelta(days=3)


def describe_awaiting_rejoin():
    def it_finds_a_kicked_row_by_member_or_discord_id():
        removal = _kicked()
        other = MemberFactory()
        assert DiscordRemoval.objects.awaiting_rejoin(removal.member, "999") == removal
        assert DiscordRemoval.objects.awaiting_rejoin(other, "111") == removal
        assert DiscordRemoval.objects.awaiting_rejoin(other, "999") is None

    def it_ignores_one_already_undone():
        removal = _kicked(rejoined_at=_morning(date(2026, 10, 25)))
        assert DiscordRemoval.objects.awaiting_rejoin(removal.member, "111") is None


def describe_put_back():
    @respx.mock
    def it_adds_them_and_stamps_it():
        removal = _kicked()
        respx.put(JOIN).mock(return_value=httpx.Response(201, json={}))
        now = _morning(date(2026, 10, 26))
        assert removal.put_back("srv1", "111", "user-tok", now) is True
        assert removal.rejoined_at == now
        action = AutomationAction.objects.get(kind=AutomationAction.Kind.DISCORD_REJOIN)
        assert (action.result, action.detail) == ("ok", "Put back on the server.")

    @respx.mock
    def it_counts_someone_already_back():
        removal = _kicked()
        respx.put(JOIN).mock(return_value=httpx.Response(204))
        assert removal.put_back("srv1", "111", "t", _morning(date(2026, 10, 26))) is True
        assert AutomationAction.objects.get(kind="discord_rejoin").detail == "Already back on the server."

    @respx.mock
    def it_records_a_refusal():
        removal = _kicked()
        respx.put(JOIN).mock(return_value=httpx.Response(403))
        assert removal.put_back("srv1", "111", "t", _morning(date(2026, 10, 26))) is False
        assert removal.rejoined_at is None
        assert AutomationAction.objects.get(kind="discord_rejoin").detail == "Discord answered refused."

    @respx.mock
    def it_records_an_error_without_raising():
        removal = _kicked()
        respx.put(JOIN).mock(return_value=httpx.Response(500, text="boom"))
        assert removal.put_back("srv1", "111", "t", _morning(date(2026, 10, 26))) is False
        assert "500 boom" in AutomationAction.objects.get(kind="discord_rejoin").detail


def describe_put_back_if_removed():
    def _active_again(removal: DiscordRemoval) -> Member:
        member = removal.member
        Member.objects.filter(pk=member.pk).update(status=Member.Status.ACTIVE)
        member.refresh_from_db()
        return member

    @respx.mock
    def it_puts_an_active_member_back_with_every_guild_role():
        member = _active_again(_kicked())
        app_guild = GuildFactory(discord_role_ids=["r1"])
        discord_guild = GuildFactory(discord_role_ids=["r2"])
        GuildMembershipFactory(member=member, guild=app_guild, source=GuildMembership.Source.APP)
        GuildMembershipFactory(member=member, guild=discord_guild, source=GuildMembership.Source.DISCORD)
        join = respx.put(JOIN).mock(return_value=httpx.Response(201, json={}))
        roles = respx.put(url__regex=ROLE_RE).mock(return_value=httpx.Response(204))
        assert put_back_if_removed(member, "111", "user-tok") is True
        assert join.called
        assert sorted(call.request.url.path.rsplit("/", 1)[-1] for call in roles.calls) == ["r1", "r2"]

    @respx.mock
    def it_adds_the_account_that_just_connected_when_it_is_a_different_one():
        member = _active_again(_kicked())
        Member.objects.filter(pk=member.pk).update(discord_user_id="555")
        member.refresh_from_db()
        old = respx.put(JOIN).mock(return_value=httpx.Response(403))
        new = respx.put("https://discord.com/api/v10/guilds/srv1/members/555").mock(
            return_value=httpx.Response(201, json={})
        )
        assert put_back_if_removed(member, "555", "new-account-tok") is True
        assert new.called
        assert not old.called
        assert DiscordRemoval.objects.get(member=member).rejoined_at is not None

    def it_leaves_a_member_who_is_not_active():
        removal = _kicked()
        assert put_back_if_removed(removal.member, "111", "t") is False

    def it_needs_a_token():
        member = _active_again(_kicked())
        assert put_back_if_removed(member, "111", "") is False

    def it_needs_a_removal():
        assert put_back_if_removed(MemberFactory(), "111", "t") is False

    def it_does_nothing_without_a_bot_token(settings):
        member = _active_again(_kicked())
        settings.DISCORD_BOT_TOKEN = ""
        assert put_back_if_removed(member, "111", "t") is False

    def it_does_nothing_without_a_server_id():
        member = _active_again(_kicked())
        config = SiteConfiguration.load()
        config.discord_server_id = ""
        config.save()
        assert put_back_if_removed(member, "111", "t") is False

    @respx.mock
    def it_pushes_no_roles_when_the_re_add_fails():
        member = _active_again(_kicked())
        GuildMembershipFactory(member=member, guild=GuildFactory(discord_role_ids=["r1"]))
        respx.put(JOIN).mock(return_value=httpx.Response(403))
        roles = respx.put(url__regex=ROLE_RE).mock(return_value=httpx.Response(204))
        assert put_back_if_removed(member, "111", "t") is False
        assert not roles.called


def describe_run_cleanup_list():
    IDS = {"ghost": "222", "active": "333", "queued": "444"}

    def _setup() -> None:
        MemberFactory(discord_user_id="333", status=Member.Status.ACTIVE)
        DiscordRemoval.objects.queue_for_cleanup("444", "queued")
        AutomationAction.objects.all().delete()

    def it_reports_without_writing_on_a_dry_run():
        _setup()
        result = run_cleanup_list(["ghost", "active", "queued", "nobody"], IDS, dry_run=True)
        assert result.lines == [
            "ghost: would be warned and queued",
            "active: connected to an active member",
            "queued: already queued",
            "nobody: nobody on the server has this username",
        ]
        assert DiscordRemoval.objects.count() == 1
        assert not AutomationAction.objects.exists()

    @respx.mock
    def it_warns_and_queues_the_rest_and_records_the_skips():
        _setup()
        _mock_dm()
        result = run_cleanup_list(["ghost", "active", "nobody"], IDS, dry_run=False)
        assert result.failed == 0
        assert result.lines[0].startswith("ghost: warned, removal due ")
        row = DiscordRemoval.objects.get(discord_user_id="222")
        assert (row.status, row.source, row.discord_username) == ("warned", "cleanup", "ghost")
        skipped = AutomationAction.objects.filter(result=AutomationAction.Result.SKIPPED)
        assert sorted(a.subject for a in skipped) == ["active", "nobody"]

    @respx.mock
    def it_counts_a_failed_dm_and_leaves_the_row_queued():
        respx.post(DM_OPEN).mock(return_value=httpx.Response(500))
        result = run_cleanup_list(["ghost"], IDS, dry_run=False)
        assert (result.failed, result.lines) == (1, ["ghost: DM failed"])
        assert DiscordRemoval.objects.get().status == DiscordRemoval.Status.QUEUED


def describe_needs_discord_rejoin():
    def it_is_true_for_an_active_member_with_a_removal_not_undone():
        removal = _kicked()
        Member.objects.filter(pk=removal.member.pk).update(status=Member.Status.ACTIVE)
        member = Member.objects.get(pk=removal.member.pk)
        assert member.needs_discord_rejoin is True
        step = next(step for step in member.onboarding.steps if step.key == "discord")
        assert (step.label, step.done) == ("Rejoin the Discord", False)
        assert step.hint == "Connect again to get back on the server"

    def it_is_false_while_they_are_still_former():
        assert _kicked().member.needs_discord_rejoin is False

    def it_is_false_without_a_removal():
        member = MemberFactory(discord_user_id="5")
        assert member.needs_discord_rejoin is False
        step = next(step for step in member.onboarding.steps if step.key == "discord")
        assert (step.label, step.done) == ("Connect your Discord", True)

    def it_matches_an_unlinked_member_by_account_only():
        removal = _kicked()
        Member.objects.filter(pk=removal.member.pk).update(status=Member.Status.ACTIVE, discord_user_id="")
        assert Member.objects.get(pk=removal.member.pk).needs_discord_rejoin is True
        assert DiscordRemoval.objects.awaiting_rejoin(MemberFactory(), "") is None
