"""The New Members tab's rows, run times and lookup (#1115), below the view.

Airtable is mocked throughout: ``airtable_sync.client.get_table`` is patched, so no spec
reaches the network.
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth.models import User
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from core.factories import TransactionalEmailLogFactory
from core.models import ScheduledJobState, TransactionalEmailLog
from core.new_members import (
    NewMemberRow,
    AIRTABLE_TIMEOUT,
    LOGIN_INVITE_KIND,
    LOOKUP_LIMIT,
    AirtableUnavailableError,
    NewMembersFilterForm,
    count_line,
    empty_message,
    local_when,
    look_up,
    member_search_formula,
    new_member_rows,
    next_run_at,
    search_airtable_members,
    welcome_for,
)
from membership.models import Member
from tests.membership.factories import MemberEmailFactory, MemberFactory

pytestmark = pytest.mark.django_db

# Friday Nov 6 2026, noon Pacific (after the Nov 1 fall back, so UTC-8).
NOW = datetime(2026, 11, 6, 20, 0, tzinfo=UTC)
_record_ids = itertools.count(1)


def _new_member(*, days_ago: float = 3, **kwargs: object) -> Member:
    """A member the Airtable pull added ``days_ago`` days before NOW."""
    kwargs.setdefault("airtable_record_id", f"recNEW{next(_record_ids):06d}")
    member = MemberFactory(**kwargs)
    Member.objects.filter(pk=member.pk).update(created_at=NOW - timedelta(days=days_ago))
    member.refresh_from_db()
    return member


def _sign_up(member: Member, *, last_login: datetime | None = None) -> User:
    """Give ``member`` an account; a new User links itself to the unlinked member with its address."""
    user = User.objects.create_user(username=f"user{member.pk}", email=member.primary_email)
    User.objects.filter(pk=user.pk).update(last_login=last_login)
    assert Member.objects.get(pk=member.pk).user_id == user.pk
    return user


def _with_status(member: Member) -> Member:
    """``member`` reloaded with ``has_email``, as the rows read it."""
    return Member.objects.with_email_status().get(pk=member.pk)


def _welcome_on() -> None:
    ScheduledJobState.objects.update_or_create(task_key="welcome_new_members", defaults={"enabled": True})


def _invite_log(email: str, *, status: str, error: str = "", at: datetime = NOW) -> TransactionalEmailLog:
    return TransactionalEmailLogFactory(
        to_email=email, trigger_kind=LOGIN_INVITE_KIND, status=status, error_message=error, sent_at=at
    )


@pytest.fixture
def airtable(settings):
    """Airtable configured, with ``get_table`` mocked; set ``.records`` or ``.error`` on the table."""
    settings.AIRTABLE_API_TOKEN = "test-token"
    settings.AIRTABLE_BASE_ID = "appTEST"
    table = MagicMock()
    table.all.return_value = []
    with patch("airtable_sync.client.get_table", return_value=table) as get_table:
        table.get_table = get_table
        yield table


def _record(record_id: str, **fields: str) -> dict[str, object]:
    return {"id": record_id, "fields": fields}


def describe_next_run_at():
    def it_is_later_the_same_day_before_the_hour():
        assert next_run_at(datetime(2026, 11, 6, 9, 30, tzinfo=UTC), 10) == datetime(2026, 11, 6, 10, tzinfo=UTC)

    def it_is_the_next_day_once_the_hour_has_started():
        assert next_run_at(datetime(2026, 11, 6, 10, 0, tzinfo=UTC), 10) == datetime(2026, 11, 7, 10, tzinfo=UTC)
        assert next_run_at(NOW, 13) == datetime(2026, 11, 7, 13, tzinfo=UTC)

    def it_reads_the_same_utc_hour_an_hour_earlier_in_pacific_after_the_fall_back():
        # 13:00 UTC is 6 AM in summer (PDT) and 5 AM after Nov 1 (PST).
        assert local_when(datetime(2026, 7, 6, 13, tzinfo=UTC)) == "Mon, Jul 6 at 6:00 AM"
        assert local_when(datetime(2026, 11, 7, 13, tzinfo=UTC)) == "Sat, Nov 7 at 5:00 AM"
        assert local_when(datetime(2026, 11, 7, 10, tzinfo=UTC)) == "Sat, Nov 7 at 2:00 AM"


def describe_filter_form():
    def it_defaults_to_30_days_everyone_and_no_search():
        form = NewMembersFilterForm({})
        assert (form.days, form.never_only, form.query) == (30, False, "")

    def it_reads_the_window_the_switch_and_the_search():
        form = NewMembersFilterForm({"nm_days": "60", "nm_never": "on", "nm_q": "  someone@example.com "})
        assert (form.days, form.never_only, form.query) == (60, True, "someone@example.com")

    def it_falls_back_to_30_days_for_a_window_it_does_not_offer():
        assert NewMembersFilterForm({"nm_days": "45"}).days == 30

    def it_ignores_the_other_tabs_params():
        form = NewMembersFilterForm({"recipient": "x@example.com", "person": "x", "days": "90"})
        assert (form.days, form.never_only, form.query) == (30, False, "")


def describe_welcome_for():
    def _welcome(member: Member, invite: TransactionalEmailLog | None = None, *, job_on: bool = True):
        return welcome_for(_with_status(member), invite, job_on=job_on, next_run=datetime(2026, 11, 7, 13, tzinfo=UTC))

    def it_says_sent_and_when_once_stamped():
        member = _new_member(welcome_email_sent_at=datetime(2026, 11, 4, 13, 2, tzinfo=UTC))
        welcome = _welcome(member)
        assert (welcome.state, welcome.text) == ("sent", "Sent Wed, Nov 4 at 5:02 AM")

    def it_trusts_the_stamp_over_an_older_failure():
        member = _new_member(welcome_email_sent_at=NOW)
        failed = _invite_log(member.primary_email, status=TransactionalEmailLog.Status.FAILED, error="quota")
        assert _welcome(member, failed).state == "sent"

    def it_says_failed_with_the_reason():
        member = _new_member()
        failed = _invite_log(member.primary_email, status=TransactionalEmailLog.Status.FAILED, error="Mailbox full")
        welcome = _welcome(member, failed)
        assert (welcome.state, welcome.text) == ("failed", "Failed: Mailbox full")

    def it_says_failed_even_with_no_reason_recorded():
        member = _new_member()
        failed = _invite_log(member.primary_email, status=TransactionalEmailLog.Status.FAILED)
        assert _welcome(member, failed).text == "Failed: no reason was recorded"

    def it_reads_a_sent_invite_with_no_stamp_by_the_job_rules():
        member = _new_member()
        sent = _invite_log(member.primary_email, status=TransactionalEmailLog.Status.SENT)
        assert _welcome(member, sent).state == "scheduled"

    def it_says_no_email_on_file():
        welcome = _welcome(_new_member(_pre_signup_email=""))
        assert (welcome.state, welcome.text) == ("not_sent", "Not sent: no email on file")

    def it_says_not_active_and_the_status():
        welcome = _welcome(_new_member(status=Member.Status.FORMER))
        assert welcome.text == "Not sent: not Active (Former)"

    def it_says_not_a_paying_member_for_work_trade():
        welcome = _welcome(_new_member(member_type=Member.MemberType.WORK_TRADE))
        assert welcome.text == "Not sent: not a paying member"

    def it_says_the_address_belongs_to_another_account():
        welcome = welcome_for(
            _with_status(_new_member()),
            None,
            job_on=True,
            next_run=datetime(2026, 11, 7, 13, tzinfo=UTC),
            address_taken=True,
        )
        assert (welcome.state, welcome.text) == ("not_sent", "Not sent: this email belongs to another member's account")

    def it_says_the_automation_is_off():
        welcome = _welcome(_new_member(), job_on=False)
        assert welcome.text == "Not sent: welcome emails are turned off"

    def it_names_the_next_morning_run_in_pacific_time():
        welcome = _welcome(_new_member())
        assert (welcome.state, welcome.text) == ("scheduled", "Goes out at the next morning run, Sat, Nov 7 at 5:00 AM")

    def it_pills_each_state_by_how_it_stands():
        member = _new_member()
        failed = _invite_log(member.primary_email, status=TransactionalEmailLog.Status.FAILED, error="quota")
        assert _welcome(member, failed).tone == "danger"
        assert _welcome(member).tone == "neutral"
        assert _welcome(member, job_on=False).tone == "warn"
        assert _welcome(_new_member(welcome_email_sent_at=NOW)).tone == "ok"

    def it_agrees_with_the_welcome_job_on_who_qualifies():
        member = _new_member()
        assert _welcome(member).state == "scheduled"
        assert Member.objects.awaiting_welcome_email().filter(pk=member.pk).exists()


def describe_new_member_rows():
    def it_lists_airtable_members_in_the_window_newest_first():
        older = _new_member(days_ago=20)
        newer = _new_member(days_ago=2)
        _new_member(days_ago=31)  # outside 30 days
        stale = MemberFactory(airtable_record_id=None)  # added by hand, not from Airtable
        rows = new_member_rows(days=30, never_only=False, now=NOW)
        assert [row.member.pk for row in rows] == [newer.pk, older.pk]
        assert stale.pk not in [row.member.pk for row in rows]

    def it_widens_to_60_days():
        member = _new_member(days_ago=45)
        assert [row.member.pk for row in new_member_rows(days=60, never_only=False, now=NOW)] == [member.pk]
        assert new_member_rows(days=30, never_only=False, now=NOW) == []

    def it_keeps_only_who_never_signed_in():
        never = _new_member()
        no_account = _new_member()
        signed_in = _new_member()
        _sign_up(never)
        _sign_up(signed_in, last_login=NOW)
        rows = new_member_rows(days=30, never_only=True, now=NOW)
        assert {row.member.pk for row in rows} == {never.pk, no_account.pk}
        assert all(row.last_login is None for row in rows)
        everyone = {row.member.pk: row for row in new_member_rows(days=30, never_only=False, now=NOW)}
        assert everyone[signed_in.pk].last_login == NOW

    def it_reads_the_latest_sign_in_link_email_to_each_address():
        _welcome_on()
        recovered = _new_member(_pre_signup_email="recovered@example.com")
        failing = _new_member(_pre_signup_email="failing@example.com")
        _invite_log("recovered@example.com", status="failed", error="quota", at=NOW - timedelta(days=2))
        _invite_log("recovered@example.com", status="sent", at=NOW - timedelta(days=1))
        _invite_log("failing@example.com", status="failed", error="Mailbox full")
        TransactionalEmailLogFactory(to_email="failing@example.com", status="failed", trigger_kind="billing.receipt")
        rows = {row.member.pk: row for row in new_member_rows(days=30, never_only=False, now=NOW)}
        assert rows[recovered.pk].welcome.state == "scheduled"
        assert rows[failing.pk].welcome.text == "Failed: Mailbox full"

    def it_reads_a_linked_members_address_from_their_account():
        from allauth.account.models import EmailAddress

        member = _new_member(_pre_signup_email="before@example.com")
        user = _sign_up(member)
        EmailAddress.objects.filter(user=user).update(primary=False)
        EmailAddress.objects.create(user=user, email="linked@example.com", primary=True, verified=True)
        (row,) = new_member_rows(days=30, never_only=False, now=NOW)
        assert row.email == "linked@example.com"

    def it_runs_the_same_queries_for_one_member_as_for_many():
        def _linked(n: int) -> None:
            _sign_up(_new_member(_pre_signup_email=f"many{n}@example.com"))

        # One linked and one unlinked member: each kind costs its queries once, however many there are.
        _linked(0)
        _new_member()
        with CaptureQueriesContext(connection) as few:
            new_member_rows(days=30, never_only=False, now=NOW)
        for n in range(1, 4):
            _linked(n)
        _new_member()
        with CaptureQueriesContext(connection) as many:
            rows = new_member_rows(days=30, never_only=False, now=NOW)
        assert len(rows) == 6
        assert len(many) == len(few)


def describe_an_address_another_account_holds():
    def _owner(email: str) -> User:
        """An account, with its own member, holding ``email``."""
        user = User.objects.create_user(username=f"owner-{email}", email=email)
        assert Member.objects.filter(user=user).exists()
        return user

    def _rows() -> dict[int, NewMemberRow]:
        return {row.member.pk: row for row in new_member_rows(days=30, never_only=False, now=NOW)}

    def it_says_not_sent_where_the_job_would_skip_them_every_morning(mailoutbox):
        from django.core.management import call_command

        _welcome_on()
        _owner("dup@example.com")
        member = _new_member(_pre_signup_email="dup@example.com")
        call_command("welcome_new_members")
        member.refresh_from_db()
        assert member.welcome_email_sent_at is None
        assert mailoutbox == []
        assert _rows()[member.pk].welcome.text == "Not sent: this email belongs to another member's account"

    def it_matches_the_address_ignoring_case_and_spaces():
        _welcome_on()
        _owner("Dup.Case@Example.com")
        member = _new_member(_pre_signup_email=" dup.case@example.COM ")
        assert _rows()[member.pk].welcome.state == "not_sent"

    def it_leaves_an_account_with_no_member_alone_since_the_job_links_it():
        _welcome_on()
        member = _new_member(_pre_signup_email="spare@example.com")
        spare = User.objects.create_user(username="spare", email="other@example.com")
        Member.objects.filter(user=spare).delete()
        User.objects.filter(pk=spare.pk).update(email="spare@example.com")
        assert _rows()[member.pk].welcome.state == "scheduled"

    def it_never_flags_a_linked_member():
        _welcome_on()
        member = _new_member(_pre_signup_email="mine@example.com")
        _sign_up(member)
        _owner("")  # an account with a blank address must not match a linked member's blank one
        assert _rows()[member.pk].welcome.state == "scheduled"


def describe_count_and_empty_lines():
    def it_counts_the_rows():
        assert count_line(1, days=30, never_only=False) == "1 new member in the last 30 days"
        assert count_line(3, days=60, never_only=True) == "3 new members who never signed in in the last 60 days"

    def it_says_nothing_when_empty():
        assert count_line(0, days=30, never_only=False) == ""

    def it_words_the_empty_line_for_the_filter():
        assert empty_message(days=30, never_only=False) == "Nobody was added from Airtable in the last 30 days."
        assert (
            empty_message(days=90, never_only=True) == "Everyone added from Airtable in the last 90 days has signed in."
        )


def describe_member_search_formula():
    def it_matches_name_legal_name_and_email_ignoring_case():
        formula = member_search_formula("Rae")
        assert formula.startswith("OR(")
        for field in ("{Member Name}", "{Legal name (if different)}", "{Email}"):
            assert f"FIND(LOWER('Rae'), LOWER({field} & ''))" in formula

    def it_escapes_a_quote_so_it_stays_inside_the_string():
        formula = member_search_formula("O'Brien') & TRUE() & ('")
        assert "LOWER('O\\'Brien\\') & TRUE() & (\\'')" in formula

    def it_escapes_a_backslash():
        assert "LOWER('a\\\\b')" in member_search_formula("a\\b")


def describe_search_airtable_members():
    def it_refuses_when_airtable_is_not_configured(settings):
        settings.AIRTABLE_API_TOKEN = ""
        with patch("airtable_sync.client.get_table") as get_table, pytest.raises(AirtableUnavailableError):
            search_airtable_members("someone")
        get_table.assert_not_called()

    def it_asks_the_members_table_with_a_short_timeout_and_no_retries(airtable):
        from airtable_sync.config import MEMBERS_TABLE_ID

        airtable.all.return_value = [_record("recA")]
        assert search_airtable_members("Rae") == [_record("recA")]
        airtable.get_table.assert_called_once_with(MEMBERS_TABLE_ID, timeout=AIRTABLE_TIMEOUT, retry=False)
        airtable.all.assert_called_once_with(formula=member_search_formula("Rae"), max_records=LOOKUP_LIMIT)

    def it_turns_any_failure_into_unavailable(airtable):
        airtable.all.side_effect = TimeoutError("read timed out")
        with pytest.raises(AirtableUnavailableError):
            search_airtable_members("Rae")


def describe_look_up():
    def it_finds_a_portal_member_by_name_and_links_their_page(airtable):
        member = MemberFactory(full_legal_name="Quillon Marsh")
        lines = look_up("quillon", now=NOW)
        assert [(line.tone, line.label, line.text) for line in lines] == [
            ("ok", "In the portal", "Quillon Marsh is in the portal.")
        ]
        assert lines[0].url == reverse("hub_admin_member_edit", args=[member.pk])
        assert lines[0].link_text == "Open Their Page"

    def it_finds_a_portal_member_by_a_staged_alias(airtable):
        member = MemberFactory(full_legal_name="Ottoline Vance")
        MemberEmailFactory(member=member, email="ovance.alias@example.com")
        assert [(line.tone, line.label, line.text) for line in look_up("OVANCE.ALIAS@", now=NOW)] == [
            ("ok", "In the portal", "Ottoline Vance is in the portal.")
        ]

    def it_counts_an_imported_record_as_in_the_portal_under_another_name(airtable):
        MemberFactory(full_legal_name="Bramwell Ash", airtable_record_id="recIMPORTED")
        airtable.all.return_value = [_record("recIMPORTED", **{"Member Name": "Bram", "Email": "b@example.com"})]
        assert [(line.tone, line.label, line.text) for line in look_up("bram@", now=NOW)] == [
            ("ok", "In the portal", "Bramwell Ash is in the portal.")
        ]

    def it_says_not_imported_yet_with_the_next_import_time(airtable):
        airtable.all.return_value = [
            _record(
                "recWAIT",
                **{
                    "Member Name": "Wren Hollis",
                    "Email": "wren@example.com",
                    "Status": "Active",
                    "Role": "Standard Member",
                },
            )
        ]
        assert [(line.tone, line.label, line.text) for line in look_up("Wren", now=NOW)] == [
            (
                "warn",
                "Not imported yet",
                "Wren Hollis is in Airtable but not imported yet. The next import runs Sat, Nov 7 at 2:00 AM.",
            )
        ]

    def it_says_imports_are_paused_instead_of_a_time_when_the_pull_is_off(airtable):
        ScheduledJobState.objects.create(task_key="airtable_pull", enabled=False)
        airtable.all.return_value = [
            _record(
                "recPAUSED",
                **{
                    "Member Name": "Linnet Abbe",
                    "Email": "linnet@example.com",
                    "Status": "Active",
                    "Role": "Standard Member",
                },
            )
        ]
        assert [line.text for line in look_up("Linnet", now=NOW)] == [
            "Linnet Abbe is in Airtable but not imported yet. Imports from Airtable are paused."
        ]

    def it_says_not_active_so_no_welcome(airtable):
        airtable.all.return_value = [
            _record(
                "recFMR",
                **{
                    "Member Name": "Fen Corley",
                    "Email": "fen@example.com",
                    "Status": "Former",
                    "Role": "Standard Member",
                },
            )
        ]
        assert [(line.tone, line.label, line.text) for line in look_up("Fen", now=NOW)] == [
            (
                "neutral",
                "No welcome email",
                "Fen Corley is in Airtable but not Active (Former), so no welcome email is sent.",
            )
        ]

    def it_says_not_a_paying_member_so_no_welcome(airtable):
        airtable.all.return_value = [
            _record(
                "recWT",
                **{"Member Name": "Tam Rook", "Email": "tam@example.com", "Status": "Active", "Role": "Work Trade"},
            )
        ]
        assert [(line.tone, line.label, line.text) for line in look_up("Tam", now=NOW)] == [
            (
                "neutral",
                "No welcome email",
                "Tam Rook is in Airtable but not a paying member (Work Trade), so no welcome email is sent.",
            )
        ]

    def it_says_a_record_with_no_email_is_never_imported(airtable):
        airtable.all.return_value = [_record("recNOMAIL", **{"Status": "Active"})]
        assert [(line.tone, line.label, line.text) for line in look_up("x", now=NOW)] == [
            (
                "danger",
                "No email",
                "This person is in Airtable with no email, so the import skips them. Add their email in Airtable.",
            )
        ]

    def it_says_in_neither_when_nothing_matches(airtable):
        assert [(line.tone, line.label, line.text) for line in look_up("nobody@example.com", now=NOW)] == [
            ("danger", "Not found", "Not in Airtable or the portal. Add them in Airtable first.")
        ]

    def it_says_airtable_could_not_be_reached_and_still_shows_the_portal(airtable):
        airtable.all.side_effect = ConnectionError("down")
        MemberFactory(full_legal_name="Iolo Brand")
        assert [(line.tone, line.label, line.text) for line in look_up("Iolo", now=NOW)] == [
            ("warn", "Airtable search failed", "Couldn't search Airtable. Only the portal was searched."),
            ("ok", "In the portal", "Iolo Brand is in the portal."),
        ]

    def it_says_not_in_the_portal_when_airtable_is_down_and_the_portal_has_nobody(airtable):
        airtable.all.side_effect = ConnectionError("down")
        assert [(line.tone, line.label, line.text) for line in look_up("nobody", now=NOW)] == [
            ("warn", "Airtable search failed", "Couldn't search Airtable. Only the portal was searched."),
            ("danger", "Not found", "Not in the portal."),
        ]
