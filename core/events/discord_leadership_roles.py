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
nothing. Every Discord call is the best-effort idiom of :mod:`core.events.discord_roles`, so
nothing here raises on a Discord failure and a blank bot token (staging) sends nothing.

Callers never reconcile inline: :func:`reconcile_on_commit` enqueues the
:func:`reconcile_leadership_roles` task once the save commits, once per member per transaction.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from django.db import transaction
from django.tasks import task

from core.events.discord_roles import assign_role, member_role_ids, remove_role, server_member_role_ids
from core.tasks import enqueue_on_commit

if TYPE_CHECKING:
    from membership.models import Member

LEADERSHIP_NAME = "leadership"
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
    """

    leadership_role_id: str = ""
    guild_officers_role_id: str = ""
    lead_role_ids: dict[int, str] = field(default_factory=dict)
    guild_names: dict[int, str] = field(default_factory=dict)

    @classmethod
    def load(cls) -> LeadershipRoleSettings:
        """Read the ids from Site Settings and every guild."""
        from core.models import SiteConfiguration
        from membership.models import Guild

        config = SiteConfiguration.load()
        lead_role_ids: dict[int, str] = {}
        guild_names: dict[int, str] = {}
        for pk, name, role_id in Guild.all_objects.exclude(discord_lead_role_id="").values_list(
            "pk", "name", "discord_lead_role_id"
        ):
            lead_role_ids[pk] = role_id.strip()
            guild_names[pk] = name
        return cls(
            leadership_role_id=config.discord_leadership_role_id.strip(),
            guild_officers_role_id=config.discord_guild_officers_role_id.strip(),
            lead_role_ids=lead_role_ids,
            guild_names=guild_names,
        )

    @property
    def managed(self) -> frozenset[str]:
        """Every role id the app adds and removes; nothing outside it is ever touched."""
        ids = {self.leadership_role_id, self.guild_officers_role_id, *self.lead_role_ids.values()}
        return frozenset(role_id for role_id in ids if role_id)

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
    return frozenset(role_id for role_id in wanted if role_id)


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
    """The role ids one reconcile added and removed (only the calls Discord accepted)."""

    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)


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
        What Discord accepted.
    """
    changes = RoleChanges()
    server = server_id()
    user_id = (member.discord_user_id or "").strip()
    if not server or not user_id:
        return changes
    settings = settings or LeadershipRoleSettings.load()
    if not settings.managed:
        return changes
    held = member_role_ids(server, user_id) if current is None else set(current)
    if held is None:
        return changes
    facts = leadership_facts([member.pk]).get(member.pk, NO_FACTS)
    to_add, to_remove = plan_changes(facts, held, settings)
    for role_id in to_add:
        if assign_role(server, user_id, role_id):
            changes.added.append(role_id)
    for role_id in to_remove:
        if remove_role(server, user_id, role_id):
            changes.removed.append(role_id)
    return changes


@task
def reconcile_leadership_roles(member_id: int) -> None:
    """Reconcile one member's leadership roles on Discord. Safe to run twice; a gone member is skipped."""
    from membership.models import Member

    member = Member.objects.filter(pk=member_id).first()
    if member is not None:
        reconcile_member(member)


@task
def remove_retired_role(role_id: str) -> None:
    """Take a guild lead role id that was just replaced off everyone who holds it. Safe to run twice.

    Enqueued when a guild's ``discord_lead_role_id`` changes away from ``role_id``: the
    reconcile only manages ids still configured, so the old one would otherwise stay on its
    holders. Skips a blank id, and one still configured anywhere (another guild's lead role,
    leadership, Guild Officers), since then the reconcile owns it. Reads the member list once;
    every call is best effort and never raises.
    """
    role_id = (role_id or "").strip()
    server = server_id()
    if not role_id or not server or role_id in LeadershipRoleSettings.load().managed:
        return
    roster = server_member_role_ids(server)
    if roster is None:
        return
    for user_id, roles in roster.items():
        if role_id in roles:
            remove_role(server, user_id, role_id)


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


def reconcile_on_commit(*member_ids: int | None) -> None:
    """Enqueue :func:`reconcile_leadership_roles` for each member once the transaction commits.

    Several triggers for one member in one transaction enqueue one task: a member whose
    callback is already waiting on this transaction is not added again. A rolled back save
    drops the callback and enqueues nothing. None ids are ignored, so a caller can pass an
    absent previous lead as is.
    """
    connection = transaction.get_connection()
    waiting = {
        func.member_id
        for _sids, func, _robust in connection.run_on_commit
        if isinstance(func, _ReconcileWhenCommitted) and not func.fired
    }
    for member_id in dict.fromkeys(member_ids):
        if member_id is None or member_id in waiting:
            continue
        waiting.add(member_id)
        transaction.on_commit(_ReconcileWhenCommitted(member_id), robust=True)
