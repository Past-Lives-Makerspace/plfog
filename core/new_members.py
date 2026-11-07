"""The New Members tab under Manage > Site Activity (#1115).

Each member the Airtable pull brought in lately, with where their welcome email stands and
whether they have signed in, and a lookup that says where a missing person fell out: in the
portal, in Airtable but not imported yet, in Airtable but never welcomed, or in neither.

The sibling of ``core.email_log_filters`` (#1112) and ``core.activity_feed_filters`` (#1114),
so ``core.views.site_activity`` only wires it up. Every GET param is ``nm_`` prefixed: all
four tabs render from one request, and a shared name would let this tab filter another.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from django import forms
from django.conf import settings
from django.db.models import Prefetch, Q
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.text import Truncator
from pyairtable.api.types import RecordDict
from pyairtable.formulas import quoted

from core.management.commands.run_scheduled_tasks import DAILY_UTC_HOUR
from core.models import ScheduledJobState, TransactionalEmailLog
from core.scheduled_jobs import AIRTABLE_PULL_UTC_HOUR
from membership.models import Member

logger = logging.getLogger(__name__)

WINDOW_CHOICES = (30, 60, 90)
DEFAULT_WINDOW = 30
WELCOME_JOB = "welcome_new_members"
AIRTABLE_PULL_JOB = "airtable_pull"
# The event Member.send_login_invite emits, so the trigger_kind its email is logged under.
LOGIN_INVITE_KIND = "member.login_invite"
# An admin is waiting on the page: give up on Airtable quickly and never retry.
AIRTABLE_TIMEOUT = (3, 5)
LOOKUP_LIMIT = 10
# The Airtable Members fields the lookup matches against (airtable_sync/config.py maps them).
AIRTABLE_SEARCH_FIELDS = ("Member Name", "Legal name (if different)", "Email")
WHEN_FORMAT = r"D, M j \a\t g:i A"


class AirtableUnavailableError(Exception):
    """Airtable could not be searched: not configured, unreachable, slow or refusing."""


def next_run_at(now: datetime, utc_hour: int) -> datetime:
    """The next start of ``utc_hour`` (UTC) after ``now``, when a once a day job next runs."""
    run = now.astimezone(UTC).replace(hour=utc_hour, minute=0, second=0, microsecond=0)
    return run if run > now else run + timedelta(days=1)


def local_when(moment: datetime) -> str:
    """A moment in the site's time zone (Pacific), as "Sat, Nov 7 at 5:00 AM"."""
    return date_format(timezone.localtime(moment), WHEN_FORMAT)


class NewMembersFilterForm(forms.Form):
    """The tab's window (30, 60 or 90 days), its Never Signed In switch and the lookup box.

    Every field is optional and judged on its own: a window typed into the URL that is not
    offered falls back to 30 days rather than dropping the other fields.
    """

    nm_days = forms.TypedChoiceField(
        required=False,
        choices=[(days, f"{days} days") for days in WINDOW_CHOICES],
        coerce=int,
    )
    nm_never = forms.BooleanField(required=False, label="Never signed in")
    # No max_length: an over-long value would fail validation and be dropped, running no
    # search while looking like one. A long value simply matches nothing.
    nm_q = forms.CharField(required=False)

    def __init__(self, data: Mapping[str, Any]) -> None:
        super().__init__(data)
        # Cleaned at once: the view only ever reads the result.
        self.is_valid()

    @property
    def days(self) -> int:
        """The chosen window in days, 30 unless 60 or 90 was picked."""
        return int(self.cleaned_data.get("nm_days") or DEFAULT_WINDOW)

    @property
    def never_only(self) -> bool:
        """Whether only members who never signed in are listed."""
        return bool(self.cleaned_data.get("nm_never"))

    @property
    def query(self) -> str:
        """The name or email typed into the lookup, stripped, or blank."""
        return str(self.cleaned_data.get("nm_q") or "")


# Each welcome state's pill: sent is good news, failed is a problem, not sent needs a look.
WELCOME_TONES = {"sent": "ok", "failed": "danger", "not_sent": "warn", "scheduled": "neutral"}


@dataclass(frozen=True)
class Welcome:
    """Where one member's welcome email stands: ``state`` styles it, ``text`` says it."""

    state: str  # "sent", "failed", "not_sent" or "scheduled"
    text: str

    @property
    def tone(self) -> str:
        """The ``hub-pill--*`` modifier the tab shows this state in."""
        return WELCOME_TONES[self.state]


@dataclass(frozen=True)
class NewMemberRow:
    """One row of the tab: the member, the address their email goes to, the welcome, the first sign in."""

    member: Member
    email: str
    welcome: Welcome

    @property
    def last_login(self) -> datetime | None:
        """When they last signed in, or None when they never have (or have no account yet)."""
        user = self.member.user
        return user.last_login if user is not None else None


def _latest_login_invites(addresses: Iterable[str]) -> dict[str, TransactionalEmailLog]:
    """The newest sign in link email logged to each address, in one query."""
    logs = TransactionalEmailLog.objects.filter(trigger_kind=LOGIN_INVITE_KIND, to_email__in=set(addresses))
    # Oldest first, so each address ends up holding its newest row.
    return {log.to_email: log for log in logs.order_by("created_at", "pk")}


def _addresses_owned_elsewhere(addresses: Iterable[str]) -> set[str]:
    """Which of these lowercased addresses already belong to another member's account, in one query.

    ``provision_user_for_member``'s own rule: it reuses the first User whose email matches
    (``email__iexact``, so the lowest pk) and gives up when that User is linked to a different
    member. The addresses come from unlinked members, so any linked owner is a different one.
    """
    from django.contrib.auth import get_user_model
    from django.db.models.functions import Lower

    users = (
        get_user_model()
        .objects.annotate(lower_email=Lower("email"))
        .filter(lower_email__in={address for address in addresses if address})
        .order_by("pk")
        .values_list("lower_email", "member__pk")
    )
    first_owner: dict[str, int | None] = {}
    for email, member_pk in users:
        first_owner.setdefault(email, member_pk)
    return {email for email, member_pk in first_owner.items() if member_pk is not None}


def _provisioning_address(member: Member) -> str:
    """The address ``provision_user_for_member`` would make an account for: blank for a linked member."""
    if member.user_id is not None:
        return ""
    return (member._pre_signup_email or "").strip().lower()


def welcome_for(
    member: Member,
    latest_invite: TransactionalEmailLog | None,
    *,
    job_on: bool,
    next_run: datetime,
    address_taken: bool = False,
) -> Welcome:
    """Where ``member``'s welcome stands, by the conditions the welcome job itself applies.

    The "not sent" reasons are ``Member.objects.awaiting_welcome_email()`` read one condition
    at a time, so the tab can never promise an email the job would not send. ``member`` must
    carry ``with_email_status``'s ``has_email``. ``address_taken`` is an unlinked member whose
    address another member's account already holds: the job cannot make them an account, so
    it skips them every morning and logs no email.
    """
    if member.welcome_email_sent_at is not None:
        return Welcome("sent", f"Sent {local_when(member.welcome_email_sent_at)}")
    if latest_invite is not None and latest_invite.status == TransactionalEmailLog.Status.FAILED:
        reason = Truncator(latest_invite.error_message).chars(160) or "no reason was recorded"
        return Welcome("failed", f"Failed: {reason}")
    if not member.has_email:
        return Welcome("not_sent", "Not sent: no email on file")
    if member.status != Member.Status.ACTIVE:
        return Welcome("not_sent", f"Not sent: not Active ({member.get_status_display()})")
    if member.member_type != Member.MemberType.STANDARD:
        return Welcome("not_sent", "Not sent: not a paying member")
    if address_taken:
        return Welcome("not_sent", "Not sent: this email belongs to another member's account")
    if not job_on:
        return Welcome("not_sent", "Not sent: welcome emails are turned off")
    return Welcome("scheduled", f"Goes out at the next morning run, {local_when(next_run)}")


def new_member_rows(*, days: int, never_only: bool, now: datetime) -> list[NewMemberRow]:
    """Members the Airtable pull added in the last ``days`` days, newest first, in a fixed number of queries.

    Args:
        days: The window, counted back from ``now``.
        never_only: Keep only members who have never signed in.
        now: The current time.
    """
    from allauth.account.models import EmailAddress

    members = (
        Member.objects.with_email_status()
        .filter(airtable_record_id__isnull=False, created_at__gte=now - timedelta(days=days))
        .select_related("user")
        # primary_email reads this list instead of a query per member.
        .prefetch_related(
            Prefetch(
                "user__emailaddress_set",
                queryset=EmailAddress.objects.filter(primary=True),
                to_attr="_primary_emailaddresses",
            )
        )
        .order_by("-created_at", "-pk")
    )
    if never_only:
        members = members.filter(Q(user__isnull=True) | Q(user__last_login__isnull=True))
    addressed = [(member, member.primary_email) for member in members]
    invites = _latest_login_invites(email for _member, email in addressed if email)
    taken = _addresses_owned_elsewhere(_provisioning_address(member) for member, _email in addressed)
    job_on = ScheduledJobState.objects.is_enabled(WELCOME_JOB)
    next_run = next_run_at(now, DAILY_UTC_HOUR)
    return [
        NewMemberRow(
            member,
            email,
            welcome_for(
                member,
                invites.get(email),
                job_on=job_on,
                next_run=next_run,
                address_taken=_provisioning_address(member) in taken,
            ),
        )
        for member, email in addressed
    ]


def count_line(total: int, *, days: int, never_only: bool) -> str:
    """Count the rows: "3 new members in the last 30 days". Blank when empty, since the empty line says so."""
    if not total:
        return ""
    noun = "new member" if total == 1 else "new members"
    who = " who never signed in" if never_only else ""
    return f"{total} {noun}{who} in the last {days} days"


def empty_message(*, days: int, never_only: bool) -> str:
    """The line shown when the window has no rows."""
    if never_only:
        return f"Everyone added from Airtable in the last {days} days has signed in."
    return f"Nobody was added from Airtable in the last {days} days."


# ── The lookup ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class LookupLine:
    """One line of a lookup's answer: a pill naming the outcome, the sentence, and an optional link.

    ``tone`` is the pill's ``hub-pill--*`` modifier and ``label`` its words.
    """

    text: str
    tone: str
    label: str
    url: str = ""
    link_text: str = ""


def member_search_formula(text: str) -> str:
    """An Airtable formula matching ``text`` inside a member's name or email, ignoring case.

    ``quoted`` escapes backslashes and quotes, so what an admin types stays a string literal.
    """
    needle = f"LOWER({quoted(text)})"
    return "OR({})".format(", ".join(f"FIND({needle}, LOWER({{{field}}} & ''))" for field in AIRTABLE_SEARCH_FIELDS))


def search_airtable_members(text: str) -> list[RecordDict]:
    """Airtable Members records whose name or email contains ``text``, read live.

    Raises:
        AirtableUnavailableError: Airtable is not configured, or the call failed for any
            reason (a timeout, a refusal, a bad token). The lookup still answers from the portal.
    """
    from airtable_sync.client import get_table
    from airtable_sync.config import MEMBERS_TABLE_ID

    if not (settings.AIRTABLE_API_TOKEN and settings.AIRTABLE_BASE_ID):
        raise AirtableUnavailableError("Airtable is not configured.")
    try:
        table = get_table(MEMBERS_TABLE_ID, timeout=AIRTABLE_TIMEOUT, retry=False)
        return table.all(formula=member_search_formula(text), max_records=LOOKUP_LIMIT)
    except Exception as exc:
        logger.warning("New Members lookup could not search Airtable", exc_info=True)
        raise AirtableUnavailableError(str(exc)) from exc


def _airtable_line(record: RecordDict, *, next_import: datetime, imports_on: bool) -> LookupLine:
    """Where one Airtable person the portal has not imported stands, by the import's own rules.

    The pull skips a record with no email and builds the rest through
    ``member_from_airtable``, so a Status or Role it does not map leaves the model's default.
    """
    from airtable_sync.config import member_from_airtable

    fields = record["fields"]
    name = fields.get("Member Name") or "This person"
    imported = member_from_airtable(fields, record_id=record["id"])
    if not imported["_pre_signup_email"].strip():
        return LookupLine(
            f"{name} is in Airtable with no email, so the import skips them. Add their email in Airtable.",
            tone="danger",
            label="No email",
        )
    status = imported.get("status", Member._meta.get_field("status").default)
    member_type = imported.get("member_type", Member._meta.get_field("member_type").default)
    if status != Member.Status.ACTIVE:
        return LookupLine(
            f"{name} is in Airtable but not Active ({fields['Status']}), so no welcome email is sent.",
            tone="neutral",
            label="No welcome email",
        )
    if member_type != Member.MemberType.STANDARD:
        return LookupLine(
            f"{name} is in Airtable but not a paying member ({fields['Role']}), so no welcome email is sent.",
            tone="neutral",
            label="No welcome email",
        )
    when = f"The next import runs {local_when(next_import)}." if imports_on else "Imports from Airtable are paused."
    return LookupLine(
        f"{name} is in Airtable but not imported yet. {when}",
        tone="warn",
        label="Not imported yet",
    )


def look_up(text: str, *, now: datetime) -> list[LookupLine]:
    """Search the portal and Airtable for ``text`` and say in one line each where every match stands.

    Only an admin's search runs this, never a tab load, because it calls Airtable.
    """
    portal = {member.pk: member for member in Member.objects.matching_name_or_email(text)[:LOOKUP_LIMIT]}
    try:
        records: list[RecordDict] | None = search_airtable_members(text)
    except AirtableUnavailableError:
        records = None

    not_imported: list[RecordDict] = []
    if records:
        imported = {
            m.airtable_record_id: m for m in Member.objects.filter(airtable_record_id__in=[r["id"] for r in records])
        }
        # A person imported under a name or address the search missed is still in the portal.
        portal.update({member.pk: member for member in imported.values()})
        not_imported = [record for record in records if record["id"] not in imported]

    lines: list[LookupLine] = []
    if records is None:
        lines.append(
            LookupLine(
                "Couldn't search Airtable. Only the portal was searched.", tone="warn", label="Airtable search failed"
            )
        )
    lines += [
        LookupLine(
            f"{member.display_name} is in the portal.",
            tone="ok",
            label="In the portal",
            url=reverse("hub_admin_member_edit", args=[member.pk]),
            link_text="Open Their Page",
        )
        for member in sorted(portal.values(), key=lambda m: m.display_name.casefold())
    ]
    next_import = next_run_at(now, AIRTABLE_PULL_UTC_HOUR)
    imports_on = ScheduledJobState.objects.is_enabled(AIRTABLE_PULL_JOB)
    lines += [_airtable_line(record, next_import=next_import, imports_on=imports_on) for record in not_imported]
    if not portal and not not_imported:
        lines.append(
            LookupLine("Not in the portal.", tone="danger", label="Not found")
            if records is None
            else LookupLine(
                "Not in Airtable or the portal. Add them in Airtable first.", tone="danger", label="Not found"
            )
        )
    return lines
