"""Discord leadership roles follow the app (#1148): guild lead roles, leadership and Guild Officers.

The app manages a fixed set of Discord roles, all ids kept in the database and never in code:
each guild's ``Guild.discord_lead_role_id``, and ``SiteConfiguration.discord_leadership_role_id``
and ``discord_guild_officers_role_id`` (blank turns that role off). Who should hold them:

========================================================  ==========================================
Who                                                       Roles
========================================================  ==========================================
The lead (``Guild.guild_lead``) or a Co-Lead of a guild   that guild's lead role, leadership, Guild Officers
Any other staff role (Secretary, Treasurer, Orientor,     Guild Officers
custom title)
Listed on a People tab of the Leadership Directory        leadership, Guild Officers
========================================================  ==========================================

A member holds the union. Only visible guilds count (``is_active`` and not soft deleted): a
hidden guild's lead and staff lose what it gave them. ``Member.fog_role`` gives nothing: App
Admin is an access level, and admins whose Discord leadership was taken away on purpose must
not get it back (Felix, 2026-11-09). :func:`desired_role_ids` is the only place this mapping lives.

:func:`reconcile_member` reads the member's roles once, then PUTs each role they should hold
and do not, and DELETEs each managed role they hold and should not; a role outside the managed
set (guild member roles, Member, anything else) is never touched, and a second run sends
nothing. Member access roles (``discord_member_role_id`` and every guild's
``discord_role_ids``) are never managed, even if an id was typed into both places. Every Discord
call is the best-effort idiom of :mod:`core.events.discord_roles`, with a 429 waited out and tried
once more (this is background work, never a web request), and a blank bot token (staging) sends
nothing. A call that still fails, or a 403 (the bot's role sits below a managed role, the likeliest
setup mistake), is not raised where it happens: the task bodies raise :class:`LeadershipRoleSyncError`
at the end, after every other change was tried, so the task is recorded FAILED and the Webmasters
are alerted.

Callers never reconcile inline: :func:`reconcile_on_commit` enqueues the
:func:`reconcile_leadership_roles` task once the save commits, once per member per transaction.
Correctness relies on one serial worker: two workers running at once could run two reconciles
for one member out of order.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from django.db import transaction
from django.tasks import task

from core.events.discord_roles import RoleOutcome, read_member_roles, role_outcome, server_member_role_ids
from core.tasks import enqueue_on_commit

if TYPE_CHECKING:
    from membership.models import Member

LEADERSHIP_NAME = "leadership"


class LeadershipRoleSyncError(Exception):
    """A Discord call a role task needed still failed after its retry; raised so the task fails loudly."""


GUILD_OFFICERS_NAME = "Guild Officers"


@dataclass(frozen=True)
class LeadershipFacts:
    """What one member is in the app that earns a managed role.

    Attributes:
        led_guild_ids: Visible guilds they lead or are a Co-Lead of.
        other_staff: They hold another staff role (Secretary, Treasurer, Orientor or a custom
            title) on a visible guild.
        listed: They are listed on a People tab of the Leadership Directory.
    """

    led_guild_ids: frozenset[int] = frozenset()
    other_staff: bool = False
    listed: bool = False


NO_FACTS = LeadershipFacts()


@dataclass(frozen=True)
class LeadershipRoleSettings:
    """The configured role ids, read once.

    Attributes:
        leadership_role_id: ``SiteConfiguration.discord_leadership_role_id`` ("" is off).
        guild_officers_role_id: ``SiteConfiguration.discord_guild_officers_role_id`` ("" is off).
        lead_role_ids: Guild pk to its ``discord_lead_role_id``, for every guild that has one,
            hidden and deleted guilds too, so their lead roles stay managed and are taken back.
        guild_names: Guild pk to name, for :meth:`role_name`.
        access_role_ids: Roles that give members access and are never managed here:
            ``discord_member_role_id`` and every guild's ``discord_role_ids``.
    """

    leadership_role_id: str = ""
    guild_officers_role_id: str = ""
    lead_role_ids: dict[int, str] = field(default_factory=dict)
    guild_names: dict[int, str] = field(default_factory=dict)
    access_role_ids: frozenset[str] = frozenset()

    @classmethod
    def load(cls) -> LeadershipRoleSettings:
        """Read the ids from Site Settings and every guild."""
        from core.models import SiteConfiguration
        from membership.models import Guild

        config = SiteConfiguration.load()
        lead_role_ids: dict[int, str] = {}
        guild_names: dict[int, str] = {}
        access = {(config.discord_member_role_id or "").strip()}
        for pk, name, role_id, member_role_ids in Guild.all_objects.values_list(
            "pk", "name", "discord_lead_role_id", "discord_role_ids"
        ):
            access.update(str(member_role_id).strip() for member_role_id in member_role_ids or [])
            if role_id.strip():
                lead_role_ids[pk] = role_id.strip()
                guild_names[pk] = name
        return cls(
            leadership_role_id=config.discord_leadership_role_id.strip(),
            guild_officers_role_id=config.discord_guild_officers_role_id.strip(),
            lead_role_ids=lead_role_ids,
            guild_names=guild_names,
            access_role_ids=frozenset(role_id for role_id in access if role_id),
        )

    @property
    def managed(self) -> frozenset[str]:
        """Every role id the app adds and removes; nothing outside it is ever touched.

        A member access role is taken out even when an id was also typed in as a managed
        one, the second guard behind the Site Settings check (:func:`role_id_conflicts`).
        """
        ids = {self.leadership_role_id, self.guild_officers_role_id, *self.lead_role_ids.values()}
        return frozenset(role_id for role_id in ids if role_id) - self.access_role_ids

    def role_name(self, role_id: str) -> str:
        """A readable name for a managed role id, for the command's report."""
        if role_id == self.leadership_role_id:
            return LEADERSHIP_NAME
        if role_id == self.guild_officers_role_id:
            return GUILD_OFFICERS_NAME
        for pk, lead_role_id in self.lead_role_ids.items():
            if lead_role_id == role_id:
                return f"{self.guild_names[pk]} Lead"
        return role_id


def desired_role_ids(facts: LeadershipFacts, settings: LeadershipRoleSettings) -> frozenset[str]:
    """The managed role ids a member with these ``facts`` should hold. The one home of the mapping.

    A lead or Co-Lead gets that guild's lead role, leadership and Guild Officers; any other
    staff role gets Guild Officers; a People tab listing gets leadership and Guild Officers.
    A blank setting contributes nothing.
    """
    wanted: set[str] = set()
    for guild_id in facts.led_guild_ids:
        wanted.add(settings.lead_role_ids.get(guild_id, ""))
    if facts.led_guild_ids or facts.listed:
        wanted.add(settings.leadership_role_id)
    if facts.led_guild_ids or facts.other_staff or facts.listed:
        wanted.add(settings.guild_officers_role_id)
    return frozenset(role_id for role_id in wanted if role_id) & settings.managed


def leadership_facts(member_ids: Iterable[int] | None = None) -> dict[int, LeadershipFacts]:
    """Each member's :class:`LeadershipFacts`, in three queries; a member with none is left out.

    Args:
        member_ids: Only these members; None reads everyone who earns anything.
    """
    from membership.models import Guild, GuildStaffMembership, LeadershipListing, LeadershipTab

    ids = None if member_ids is None else list(member_ids)
    leads = Guild.objects.filter(is_active=True, guild_lead__isnull=False)
    staff = GuildStaffMembership.objects.filter(guild__is_active=True, guild__deleted_at__isnull=True)
    listed = LeadershipListing.objects.filter(is_listed=True, tab__kind=LeadershipTab.Kind.PEOPLE)
    if ids is not None:
        leads = leads.filter(guild_lead_id__in=ids)
        staff = staff.filter(member_id__in=ids)
        listed = listed.filter(member_id__in=ids)

    led: dict[int, set[int]] = {}
    officers: set[int] = set()
    for member_id, guild_id in leads.values_list("guild_lead_id", "pk"):
        led.setdefault(member_id, set()).add(guild_id)
    for member_id, guild_id, role in staff.values_list("member_id", "guild_id", "role"):
        if role == GuildStaffMembership.Role.CO_LEAD:
            led.setdefault(member_id, set()).add(guild_id)
        else:
            officers.add(member_id)
    on_tabs = set(listed.values_list("member_id", flat=True))
    return {
        member_id: LeadershipFacts(
            led_guild_ids=frozenset(led.get(member_id, ())),
            other_staff=member_id in officers,
            listed=member_id in on_tabs,
        )
        for member_id in set(led) | officers | on_tabs
    }


@dataclass
class RoleChanges:
    """What one reconcile or strip did.

    Attributes:
        added: Role ids Discord accepted adding.
        removed: Role ids Discord accepted removing.
        failed: Role ids whose call still failed after its retry (a network error, a 429, a 5xx)
            or was refused (a 403: the bot's role sits below it).
        read_failed: The member's roles could not be read, so nothing was changed.
    """

    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    read_failed: bool = False

    @property
    def ok(self) -> bool:
        """Whether every call that was needed went through (a 404, the member gone, counts as through)."""
        return not self.failed and not self.read_failed

    def raise_if_failed(self, who: str) -> None:
        """Raise :class:`LeadershipRoleSyncError` naming ``who`` when anything failed; call it last."""
        if self.read_failed:
            raise LeadershipRoleSyncError(f"Could not read the Discord roles of {who}.")
        if self.failed:
            raise LeadershipRoleSyncError(f"Discord role changes failed for {who}: {', '.join(self.failed)}.")


def plan_changes(
    facts: LeadershipFacts, current: Iterable[str], settings: LeadershipRoleSettings
) -> tuple[list[str], list[str]]:
    """The managed role ids to add and to remove for a member holding ``current``, each sorted."""
    held = set(current)
    desired = desired_role_ids(facts, settings)
    return sorted(desired - held), sorted((held & settings.managed) - desired)


def server_id() -> str:
    """The configured Discord server id; blank turns the whole sync off."""
    from core.models import SiteConfiguration

    return (SiteConfiguration.load().discord_server_id or "").strip()


#: Outcomes a leadership task reports: a failure after the retry, and a 403 (the bot's role too low).
_REPORTED = frozenset({RoleOutcome.FAILED, RoleOutcome.REFUSED})


def _apply(server: str, user_id: str, to_add: Iterable[str], to_remove: Iterable[str], changes: RoleChanges) -> None:
    """Send each PUT and DELETE, recording what went through and what failed; every one is tried."""
    for method, role_ids, done in (("PUT", to_add, changes.added), ("DELETE", to_remove, changes.removed)):
        for role_id in role_ids:
            outcome = role_outcome(method, server, user_id, role_id, retry=True)
            if outcome is RoleOutcome.DONE:
                done.append(role_id)
            elif outcome in _REPORTED:
                changes.failed.append(role_id)


def _held(server: str, user_id: str, current: Iterable[str] | None, changes: RoleChanges) -> set[str] | None:
    """The roles ``user_id`` holds: ``current`` when given, else one read (a failed read is recorded)."""
    if current is not None:
        return set(current)
    read = read_member_roles(server, user_id, retry=True)
    changes.read_failed = read.failed
    return read.roles


def reconcile_member(
    member: Member,
    *,
    current: Iterable[str] | None = None,
    settings: LeadershipRoleSettings | None = None,
) -> RoleChanges:
    """Make ``member``'s managed Discord roles match :func:`desired_role_ids`. Never raises on Discord.

    Skips a member without a linked Discord account, a blank server id and a setup with no
    managed role, sending nothing. Reads their roles once (unless ``current`` is given, as the
    command does from its one member list), and leaves them alone when that read fails.

    Args:
        member: Who to reconcile.
        current: The role ids they hold now, when the caller already read them.
        settings: The configured ids, when the caller already loaded them.

    Returns:
        What went through and what failed.
    """
    changes = RoleChanges()
    server = server_id()
    user_id = (member.discord_user_id or "").strip()
    if not server or not user_id:
        return changes
    settings = settings or LeadershipRoleSettings.load()
    if not settings.managed:
        return changes
    held = _held(server, user_id, current, changes)
    if held is None:
        return changes
    facts = leadership_facts([member.pk]).get(member.pk, NO_FACTS)
    to_add, to_remove = plan_changes(facts, held, settings)
    _apply(server, user_id, to_add, to_remove, changes)
    return changes


def strip_managed_roles(
    discord_user_id: str,
    *,
    current: Iterable[str] | None = None,
    settings: LeadershipRoleSettings | None = None,
) -> RoleChanges:
    """Take every managed role off a Discord account the app no longer links to anyone. Never raises on Discord.

    For the account of a deleted member, the account a member swapped for another, and (with
    the command's ``--strip-unlinked``) a server member holding a managed role whom no linked
    member answers to. Reads their roles once unless ``current`` is given.
    """
    changes = RoleChanges()
    server = server_id()
    user_id = (discord_user_id or "").strip()
    if not server or not user_id:
        return changes
    settings = settings or LeadershipRoleSettings.load()
    if not settings.managed:
        return changes
    held = _held(server, user_id, current, changes)
    if held is None:
        return changes
    _apply(server, user_id, (), sorted(held & settings.managed), changes)
    return changes


@task
def reconcile_leadership_roles(member_id: int) -> None:
    """Reconcile one member's leadership roles on Discord. Safe to run twice; a gone member is skipped.

    Raises:
        LeadershipRoleSyncError: A Discord call still failed after its retry, raised after
            every other change was tried, so the task is recorded FAILED and alerts.
    """
    from membership.models import Member

    member = Member.objects.filter(pk=member_id).first()
    if member is not None:
        reconcile_member(member).raise_if_failed(f"member {member_id}")


@task
def strip_leadership_roles(discord_user_id: str) -> None:
    """Take every managed role off a Discord account the app stopped linking (deleted, or swapped). Safe twice.

    Keyed by the Discord id, because the member row no longer carries it when this runs.

    Raises:
        LeadershipRoleSyncError: A Discord call still failed after its retry.
    """
    strip_managed_roles(discord_user_id).raise_if_failed(f"Discord account {discord_user_id}")


@task
def remove_retired_role(role_id: str) -> None:
    """Take a guild lead role id that was just replaced off everyone who holds it. Safe to run twice.

    Enqueued when a guild's ``discord_lead_role_id`` changes away from ``role_id``: the
    reconcile only manages ids still configured, so the old one would otherwise stay on its
    holders. Skips a blank id, one still configured anywhere (another guild's lead role,
    leadership, Guild Officers), since then the reconcile owns it, and a member access role
    (``discord_member_role_id``, any guild's ``discord_role_ids``). Reads the member list once.

    Raises:
        LeadershipRoleSyncError: The member list could not be read, or a removal still failed
            after its retry (raised after every holder was tried).
    """
    role_id = (role_id or "").strip()
    server = server_id()
    settings = LeadershipRoleSettings.load()
    if not role_id or not server or role_id in settings.managed or role_id in settings.access_role_ids:
        return
    roster = server_member_role_ids(server, retry=True)
    if roster is None:
        raise LeadershipRoleSyncError(f"Could not read the Discord member list to retire role {role_id}.")
    failed = [
        user_id
        for user_id, roles in roster.items()
        if role_id in roles and role_outcome("DELETE", server, user_id, role_id, retry=True) in _REPORTED
    ]
    if failed:
        raise LeadershipRoleSyncError(f"Could not take role {role_id} off {len(failed)} Discord accounts.")


def strip_on_commit(discord_user_id: str) -> None:
    """Enqueue :func:`strip_leadership_roles` for this Discord id after commit; a blank id does nothing."""
    user_id = (discord_user_id or "").strip()
    if user_id:
        enqueue_on_commit(strip_leadership_roles, user_id)


def role_id_conflicts(
    *,
    member_role_id: str,
    leadership_role_id: str,
    guild_officers_role_id: str,
    guilds: dict[int, tuple[str, list[str], bool]],
    check_guild_rows: bool = True,
) -> tuple[dict[str, str], dict[int, str]]:
    """Why the role ids as they would be saved clash, for the Site Settings save to refuse.

    The managed ids (leadership, Guild Officers, each guild's lead role) must all differ from
    every member access role (the Member role and any guild's member role ids, every guild
    counted), or the sync would hand out or take away access; leadership and Guild Officers
    must differ, and a lead role may be neither. Two visible guilds may not share a lead role
    id; a hidden or deleted guild's old one may be reused.

    Args:
        member_role_id: ``discord_member_role_id``.
        leadership_role_id: ``discord_leadership_role_id``.
        guild_officers_role_id: ``discord_guild_officers_role_id``.
        guilds: Guild pk to (its lead role id, its member role ids, whether it is visible:
            active and not deleted), for every guild. A lead role id two visible guilds share
            is reported on the later one in this order.
        check_guild_rows: Also judge each guild's lead role id (when the guild rows were
            posted). Off, only the site fields are judged, and a stored lead role id equal to
            leadership or Guild Officers is reported on that site field.

    Returns:
        (Site Settings field name to message, guild pk to message for its lead role id).
    """
    access = {member_role_id.strip()} | {
        str(role_id).strip() for _lead, member_ids, _live in guilds.values() for role_id in member_ids
    }
    access.discard("")
    site_errors: dict[str, str] = {}
    guild_errors: dict[int, str] = {}
    access_message = "This id is a member access role (the Member role or a guild's role). Use a different role."
    taken_message = "A guild already uses this id as its lead role. Use a different role."
    leadership = leadership_role_id.strip()
    officers = guild_officers_role_id.strip()
    leads = {raw_lead.strip() for raw_lead, _member_ids, _live in guilds.values()} - {""}
    if leadership in access:
        site_errors["discord_leadership_role_id"] = access_message
    elif not check_guild_rows and leadership in leads:
        site_errors["discord_leadership_role_id"] = taken_message
    if officers in access:
        site_errors["discord_guild_officers_role_id"] = access_message
    elif officers and officers == leadership:
        site_errors["discord_guild_officers_role_id"] = "Guild Officers needs its own role, not the leadership role."
    elif not check_guild_rows and officers in leads:
        site_errors["discord_guild_officers_role_id"] = taken_message
    if check_guild_rows:
        guild_errors = _guild_row_conflicts(guilds, access, (leadership, officers), access_message)
    return site_errors, guild_errors


def _guild_row_conflicts(
    guilds: dict[int, tuple[str, list[str], bool]], access: set[str], site_ids: tuple[str, str], access_message: str
) -> dict[int, str]:
    """Each guild whose lead role id is an access role, a site role, or another visible guild's."""
    errors: dict[int, str] = {}
    seen: set[str] = set()
    for pk, (raw_lead, _member_ids, live) in guilds.items():
        lead = raw_lead.strip()
        if not lead:
            continue
        if lead in access:
            errors[pk] = access_message
        elif lead in site_ids:
            errors[pk] = "A guild lead role needs its own role, not the leadership or Guild Officers role."
        elif live and lead in seen:
            errors[pk] = "Another guild already uses this lead role id."
        if live:
            seen.add(lead)
    return errors


class _ReconcileWhenCommitted:
    """The on-commit callback for one member, recognisable so a second trigger can find it.

    ``__qualname__`` is set on the instance because Django names the callback in its log
    line when a robust callback raises, and an instance has none of its own.
    """

    def __init__(self, member_id: int) -> None:
        self.member_id = member_id
        self.fired = False
        self.__qualname__ = "reconcile_leadership_roles_on_commit"

    def __call__(self) -> None:
        self.fired = True
        enqueue_on_commit(reconcile_leadership_roles, self.member_id)


def _waiting_member_ids() -> set[int]:
    """Members whose reconcile is already waiting on this transaction's commit.

    Reads ``run_on_commit``, which is Django's internal list. If its shape ever changes this
    answers empty, and the only cost is a duplicate task (harmless: the reconcile is safe to
    run twice), never a missed one.
    """
    try:
        return {
            func.member_id
            for _sids, func, _robust in transaction.get_connection().run_on_commit
            if isinstance(func, _ReconcileWhenCommitted) and not func.fired
        }
    except Exception:  # noqa: BLE001 - dedupe is an optimisation; plain registration is always correct.
        return set()


def reconcile_on_commit(*member_ids: int | None) -> None:
    """Enqueue :func:`reconcile_leadership_roles` for each member once the transaction commits.

    Several triggers for one member in one transaction enqueue one task: a member whose
    callback is already waiting on this transaction is not added again. A rolled back save
    drops the callback and enqueues nothing. None ids are ignored, so a caller can pass an
    absent previous lead as is.
    """
    waiting = _waiting_member_ids()
    for member_id in dict.fromkeys(member_ids):
        if member_id is None or member_id in waiting:
            continue
        waiting.add(member_id)
        transaction.on_commit(_ReconcileWhenCommitted(member_id), robust=True)
