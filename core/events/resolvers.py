"""Recipient resolvers — role × scope (design §3, Decisions 2 + 7).

Each resolver maps an event's ``context`` to ``[(User, reason)]`` where ``reason``
records the role / why (used by templates and digests). This is the single tested
home that replaces the ~25 ad-hoc recipient resolutions across the app.

Phase-1 invariant: these are NEW, additively. Nothing here is called from an
existing send site yet — they are wired into :func:`core.events.emit.emit` and
unit-tested. The legacy ad-hoc resolvers (``classes/emails._admin_recipients`` &
friends) stay in place until the migration phase.

Members are mapped to Users via :func:`_member_user`, which returns the linked
``User`` only when it carries a usable email — members without a usable account or
email are dropped (they cannot receive a per-recipient channel).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from django.conf import settings

from core.events.registry import Recipients

if TYPE_CHECKING:
    from django.contrib.auth.models import User

    from membership.models import Guild, Member, OrientationBooking


# A resolved recipient: the User and a short human reason for why they're included.
Recipient = tuple["User", str]


class ResolverFn(Protocol):
    """A recipient resolver: ``context`` mapping → list of ``(User, reason)``."""

    def __call__(self, context: dict[str, Any]) -> list[Recipient]: ...


# --- Member → User mapping ---------------------------------------------------


def _member_user(member: Member, reason: str) -> Recipient | None:
    """Map a Member to a ``(User, reason)`` recipient, or ``None`` if unusable.

    A member can only receive a per-recipient channel when they have a linked
    ``User`` that carries a usable email (login + email both hang off the User).
    Unlinked members (Airtable-imported, never signed up) and linked-but-emailless
    accounts are dropped — they have nowhere to deliver to.
    """
    user = member.user
    if user is None or not user.pk:
        return None
    if not (user.email or "").strip():
        return None
    return (user, reason)


def _dedupe(recipients: list[Recipient | None]) -> list[Recipient]:
    """Drop ``None`` entries and de-duplicate by user pk, keeping first reason."""
    out: list[Recipient] = []
    seen: set[int] = set()
    for recipient in recipients:
        if recipient is None:
            continue
        user, _reason = recipient
        if user.pk in seen:
            continue
        seen.add(user.pk)
        out.append(recipient)
    return out


def _members_to_recipients(members: list[Member], reason: str) -> list[Recipient]:
    return _dedupe([_member_user(m, reason) for m in members])


# --- Context accessors (fail loudly per CLAUDE.md error-handling rules) -------


def _require(context: dict[str, Any], key: str) -> Any:
    """Fetch a required context value, raising loudly when absent.

    Per the project's fail-loudly rule, a resolver that needs (say) a ``guild``
    must be given one — a missing key is a programming error at the call site,
    not a silently-empty audience.
    """
    return context[key]


# --- Role × scope resolvers (§3) ---------------------------------------------


def fog_admins(context: dict[str, Any]) -> list[Recipient]:
    """GLOBAL — site-wide admin mail (all approvals, billing, validation, new-member).

    = every Member with the Admin FOG role, unioned with any addresses configured
    via ``CLASS_ADMIN_NOTIFY_EMAILS`` (mapped to their owning User where one
    exists). Mirrors today's ``classes.emails._admin_recipients`` but typed and
    User-based. Guild leads do NOT get global admin mail (Decision 2).
    """
    from allauth.account.models import EmailAddress
    from django.contrib.auth.models import User

    from membership.models import Member

    admins = list(Member.objects.filter(fog_role=Member.FogRole.ADMIN).select_related("user"))
    recipients: list[Recipient | None] = [_member_user(m, "fog_admin") for m in admins]

    raw = getattr(settings, "CLASS_ADMIN_NOTIFY_EMAILS", "") or ""
    for chunk in raw.split(","):
        email = chunk.strip()
        if not email:
            continue
        address = EmailAddress.objects.filter(email__iexact=email).select_related("user").first()
        user: User | None = address.user if address is not None else None
        if user is None:
            user = User.objects.filter(email__iexact=email).first()
        if user is not None and user.pk and (user.email or "").strip():
            recipients.append((user, "fog_admin_configured"))
    return _dedupe(recipients)


def guild_leadership(context: dict[str, Any]) -> list[Recipient]:
    """GUILD-SCOPED — the guild's lead plus every staff member (deduped).

    Used for both email and in-app (fixes the orientation asymmetry, audit-D §3).
    Reuses :meth:`membership.models.Guild.leadership_members`. An explicit ``None``
    guild (a lead-less category whose review routes to admins by email only) resolves
    to no per-user recipient — the email goes to an explicit ``email_to`` address set —
    while a missing ``guild`` key still fails loudly (a programming error).
    """
    guild: Guild | None = _require(context, "guild")
    if guild is None:
        return []
    return _members_to_recipients(guild.leadership_members(), "guild_leadership")


def guild_leadership_or_admins(context: dict[str, Any]) -> list[Recipient]:
    """UNION — the in-context guild's leadership PLUS every FOG admin (deduped).

    The single-stage routing audience for a member's event proposal (§7): a guild
    event routes to that guild's lead + staff *and* any admin; a site-wide proposal
    (``context["guild"]`` is ``None``) resolves to admins only, because
    :func:`guild_leadership` returns ``[]`` for a null guild. Composes the two existing
    resolvers exactly as :func:`release_audience` composes its members.
    """
    return _dedupe([*guild_leadership(context), *fog_admins(context)])  # type: ignore[list-item]


# --- Capability-scoped resolvers (§ admin capabilities) ----------------------


def _capability_recipients(capability: str) -> list[Recipient]:
    """Every member holding ``capability`` — the sole recipients of its notifications.

    A capability is the master switch: holding it both routes the matching approval/alert
    notifications here AND grants the action. A ``fog_role=ADMIN`` member who does NOT hold
    the capability receives nothing — they can self-grant it on their member page if they
    want in. Holders are tagged ``capability:<name>`` so the settings page can badge the row.
    """
    from membership.models import Member

    holders = list(Member.objects.filter(admin_capabilities__capability=capability).select_related("user").distinct())
    return _members_to_recipients(holders, f"capability:{capability}")


def class_approvers(context: dict[str, Any]) -> list[Recipient]:
    """Class Administrators — holders only; a plain admin gets nothing until granted."""
    from membership.models import AdminCapability

    return _capability_recipients(AdminCapability.Capability.CLASS_APPROVER)


def guild_leadership_or_class_approvers(context: dict[str, Any]) -> list[Recipient]:
    """COMPOSITION — a guild's leadership when present, else the Class Administrators.

    A guild-led class routes to that guild's lead + staff ONLY (via
    :func:`guild_leadership`); a lead-less category (``context["guild"]`` is ``None``)
    routes to :func:`class_approvers`. This is composition, not the union
    :func:`guild_leadership_or_admins` performs — a guild-led review never also blasts
    every admin. A missing ``guild`` key still fails loudly (a programming error).
    """
    guild: Guild | None = _require(context, "guild")
    if guild is not None:
        return guild_leadership(context)
    return class_approvers(context)


def space_approvers(context: dict[str, Any]) -> list[Recipient]:
    """Space & Cubby Administrators — holders only; a plain admin gets nothing until granted."""
    from membership.models import AdminCapability

    return _capability_recipients(AdminCapability.Capability.SPACE_APPROVER)


def discount_approvers(context: dict[str, Any]) -> list[Recipient]:
    """Discount Code Administrators — holders only; a plain admin gets nothing until granted."""
    from membership.models import AdminCapability

    return _capability_recipients(AdminCapability.Capability.DISCOUNT_APPROVER)


def events_approvers(context: dict[str, Any]) -> list[Recipient]:
    """Calendar Administrators — holders only; a plain admin gets nothing until granted."""
    from membership.models import AdminCapability

    return _capability_recipients(AdminCapability.Capability.EVENTS_APPROVER)


def guild_leadership_or_events_approvers(context: dict[str, Any]) -> list[Recipient]:
    """COMPOSITION — a guild's leadership when present, else the Calendar Administrators.

    The proposal-queue audience for a Community Calendar event or a meeting agenda item:
    a guild-scoped proposal routes to that guild's lead + staff ONLY; a site-wide/council
    proposal (``context["guild"]`` is ``None``) routes to :func:`events_approvers`. Same
    composition shape as :func:`guild_leadership_or_class_approvers`.
    """
    guild: Guild | None = _require(context, "guild")
    if guild is not None:
        return guild_leadership(context)
    return events_approvers(context)


def billing_approvers(context: dict[str, Any]) -> list[Recipient]:
    """Billing Administrators — holders only; a plain admin gets nothing until granted."""
    from membership.models import AdminCapability

    return _capability_recipients(AdminCapability.Capability.BILLING_APPROVER)


def guild_lead(context: dict[str, Any]) -> list[Recipient]:
    """GUILD-SCOPED — the lead only (staff excluded), for lead-only events."""
    guild: Guild = _require(context, "guild")
    lead = guild.guild_lead
    if lead is None:
        return []
    return _members_to_recipients([lead], "guild_lead")


def guild_members(context: dict[str, Any]) -> list[Recipient]:
    """GUILD-SCOPED — every active member who has joined the guild (Decision 4).

    The audience for a guild announcement: everyone in the guild's roster, addressed
    via their linked User. Unlike :meth:`membership.models.Guild.roster_members` this
    is NOT directory-privacy filtered — a directory-hidden member still belongs to the
    guild and still gets the guild's announcements (privacy governs the public roster,
    not whether you hear from your own guild). A guild with no members resolves to no
    recipient.
    """
    from membership.models import Member

    guild: Guild = _require(context, "guild")
    members = list(
        Member.objects.filter(
            guild_memberships__guild=guild,
            status=Member.Status.ACTIVE,
        )
        .select_related("user")
        .distinct()
    )
    return _members_to_recipients(members, "guild_member")


def guild_orienters(context: dict[str, Any]) -> list[Recipient]:
    """GUILD-SCOPED — the lead plus members holding the ORIENTER staff role.

    Used for "an orientation needs a runner" fan-out (Decision 7): every orienter
    is pinged, not just the lead. The lead is always included (the lead can run
    orientations and carries full authority).
    """
    from membership.models import GuildStaffMembership

    guild: Guild = _require(context, "guild")
    members: list[Member] = []
    seen: set[int] = set()
    if guild.guild_lead_id is not None and guild.guild_lead is not None:
        members.append(guild.guild_lead)
        seen.add(guild.guild_lead_id)
    orienter_staff = guild.staff_memberships.select_related("member").filter(role=GuildStaffMembership.Role.ORIENTER)
    for staff in orienter_staff:
        if staff.member_id not in seen:
            members.append(staff.member)
            seen.add(staff.member_id)
    return _members_to_recipients(members, "guild_orienter")


def orientation_runner(context: dict[str, Any]) -> list[Recipient]:
    """The staffer who actually claimed / ran the orientation (Decision 7).

    Resolves from the booking's ``oriented_by`` member — the person credited with
    running it — fixing the bug where the lead was always mis-credited. Empty when
    no runner has been recorded yet.
    """
    booking: OrientationBooking = _require(context, "booking")
    runner = booking.oriented_by
    if runner is None:
        return []
    return _members_to_recipients([runner], "orientation_runner")


def registrant(context: dict[str, Any]) -> list[Recipient]:
    """The member the event is about (registration, orientation update, tab, …).

    Accepts a ``member`` in context, or a ``booking`` / ``registration`` whose
    ``member`` is used. The single per-member target for confirmations & updates.

    A guest registration (``registration.member is None``) yields no per-user
    recipient — its email is addressed by an explicit ``email_to`` at the call site
    (the dedicated confirmation always went to ``registration.email``), so the in-app
    fan-out correctly finds nobody.
    """
    member = _registrant_member(context)
    if member is None:
        return []
    return _members_to_recipients([member], "registrant")


def _registrant_member(context: dict[str, Any]) -> Member | None:
    if "member" in context:
        return context["member"]
    if "booking" in context:
        return context["booking"].member
    if "registration" in context:
        return context["registration"].member
    raise KeyError("registrant resolver needs one of: member, booking, registration")


def instructor(context: dict[str, Any]) -> list[Recipient]:
    """The instructor of a class offering (instructor-side notifications)."""
    if "instructor" in context:
        member = context["instructor"]
    else:
        offering = _require(context, "offering")
        member = offering.instructor
    if member is None:
        return []
    return _members_to_recipients([member], "instructor")


def next_waitlisted(context: dict[str, Any]) -> list[Recipient]:
    """The next member in line when a waitlist spot opens.

    Accepts an explicit ``member`` (the promoted registrant) in context — the
    caller knows who was next. Empty when none is supplied (no one waitlisted).
    """
    member = context.get("member")
    if member is None:
        return []
    return _members_to_recipients([member], "next_waitlisted")


def tab_member(context: dict[str, Any]) -> list[Recipient]:
    """The member whose billing tab the event concerns (charge, failure, entry)."""
    if "member" in context:
        member = context["member"]
    else:
        tab = _require(context, "tab")
        member = tab.member
    return _members_to_recipients([member], "tab_member")


def inviter(context: dict[str, Any]) -> list[Recipient]:
    """The user who sent an invitation (notified when it's accepted).

    ``Invite.invited_by`` is a ``User`` (the admin who sent it), not a Member, so
    this resolver yields that User directly. An explicit ``user`` in context wins.
    """
    if "user" in context:
        user = context["user"]
    else:
        invite = _require(context, "invite")
        user = invite.invited_by
    if user is None or not user.pk or not (user.email or "").strip():
        return []
    return [(user, "inviter")]


def invitee(context: dict[str, Any]) -> list[Recipient]:
    """The person being INVITED — addressed by raw email, not a user (Decision 5).

    An invitee has no account yet (that's the point of the invite), so there is no
    ``User`` to fan out in-app to: the invite is an email-only, forced send whose
    recipient is the raw ``invite.email`` carried as the event's ``email_to``. This
    resolver therefore yields NO per-user recipient — the email rides the explicit
    ``email_to`` path in :func:`core.events.emit.emit`. Kept as a named resolver so the
    event is declarative and the audience is documented in the catalogue.
    """
    return []


def lease_tenant(context: dict[str, Any]) -> list[Recipient]:
    """The member tenant of a lease (lease expiring / activated).

    A lease's tenant is a generic FK (Member or Guild); only Member tenants are
    addressable as a recipient — guild-tenant leases resolve to no recipient here
    (a guild broadcast is a separate event).
    """
    from membership.models import Member

    if "member" in context:
        member = context["member"]
    else:
        lease = _require(context, "lease")
        tenant = lease.tenant
        member = tenant if isinstance(tenant, Member) else None
    if member is None:
        return []
    return _members_to_recipients([member], "lease_tenant")


def all_active_members(context: dict[str, Any]) -> list[Recipient]:
    """Every *activated* active member with a usable account — the default broadcast audience.

    Reuses ``core.notifications.active_member_users`` (``member__status="active"``)
    and keeps only Users carrying an email AND who have signed in at least once.

    ACTIVATION GATE (``last_login__isnull=False``): provisioning mints a login-capable
    User for every member, but we never broadcast (site/guild announcements, community
    events) to an account that has never logged in — logging in is what "activates" a
    member. Same rule the voting reminders use (``membership/voting.py``).
    """
    from core.notifications import active_member_users

    out: list[Recipient] = []
    seen: set[int] = set()
    for user in active_member_users().filter(last_login__isnull=False).iterator():
        if user.pk in seen or not (user.email or "").strip():
            continue
        seen.add(user.pk)
        out.append((user, "active_member"))
    return out


def all_guild_leads(context: dict[str, Any]) -> list[Recipient]:
    """CROSS-GUILD — every active guild lead, officer, or staffer site-wide.

    The audience for the Guild Lead Meeting. Unlike :func:`guild_leadership` (per-guild,
    reads ``context["guild"]``) this is the union across all guilds and takes no guild —
    that's the whole point of a cross-guild leadership series. Members without a usable
    account/email are dropped like every other resolver.
    """
    from django.db.models import Q

    from membership.models import Member

    leads = (
        Member.objects.active()
        .filter(
            Q(led_guilds__isnull=False) | Q(guild_staff_roles__isnull=False) | Q(fog_role=Member.FogRole.GUILD_OFFICER)
        )
        # ACTIVATION GATE — never broadcast to an account that has never signed in.
        .filter(user__last_login__isnull=False)
        .select_related("user")
        .distinct()
    )
    return _members_to_recipients(list(leads), "all_guild_leads")


def event_audience(context: dict[str, Any]) -> list[Recipient]:
    """The launch-announcement audience for a community event, by scope.

    Mirrors ``CommunityEvent._ANNOUNCE_EVENT``: a guild event → the guild's members;
    a leadership meeting → all guild leads; any other site-wide event → all active
    members. Composes the three existing resolvers (like :func:`guild_leadership_or_admins`),
    so one ``event.reminder`` / ``event.happening_now`` key serves all three scopes.

    The activation gate therefore *matches the launch announcement's*, per scope:
    ``all_active_members`` / ``all_guild_leads`` drop never-signed-in accounts, while
    ``guild_members`` keeps a provisioned-but-never-logged-in guild member (deliberate
    parity with ``event.guild_published``).
    """
    from membership.models import CommunityEvent

    guild: Guild | None = _require(context, "guild")  # value may be None (site-wide); missing key is a bug
    if guild is not None:
        return guild_members(context)
    if context["event_type"] == CommunityEvent.EventType.LEAD_MEETING:
        return all_guild_leads(context)
    return all_active_members(context)


def all_voters(context: dict[str, Any]) -> list[Recipient]:
    """Every member eligible to vote — paying active members with a usable account.

    Voting eligibility tracks paying membership (``Member.objects.paying``). New in
    this work for the voting 48h reminder + results emails (Decision 5).
    """
    from membership.models import Member

    # ACTIVATION GATE — a never-signed-in member can't vote and shouldn't be emailed.
    voters = (
        Member.objects.paying()
        .filter(status=Member.Status.ACTIVE, user__last_login__isnull=False)
        .select_related("user")
    )
    return _members_to_recipients(list(voters), "voter")


def everyone_with_login(context: dict[str, Any]) -> list[Recipient]:
    """Every User who has actually signed in at least once (active, usable email).

    Broader than active members: includes anyone with an account regardless of
    membership status. New in this work for the release / changelog email
    (Decision 5).

    ACTIVATION GATE (``last_login__isnull=False``): provisioning mints a login-capable
    User for every member, but a member is only "activated" once they have actually
    signed in. We never broadcast to an account that has never logged in — they have no
    bell to read and didn't ask to hear from us yet. Same rule the voting reminders use.
    """
    from django.contrib.auth.models import User

    out: list[Recipient] = []
    seen: set[int] = set()
    for user in User.objects.filter(is_active=True, last_login__isnull=False).iterator():
        if user.pk in seen or not (user.email or "").strip():
            continue
        seen.add(user.pk)
        out.append((user, "has_login"))
    return out


def release_audience(context: dict[str, Any]) -> list[Recipient]:
    """Everyone who should hear about a release (Decision 5).

    The design's audience is ``all_active_members ∪ everyone_with_login ∪ admins``.
    :func:`everyone_with_login` already supersets active members and any admin who can
    log in, but the union is made explicit + deduped here so the audience is robust
    even if an admin's account is somehow inactive (an admin still hears about a
    release). The reason records which membership pulled them in (first wins).
    """
    combined = everyone_with_login(context) + all_active_members(context) + fog_admins(context)
    return _dedupe(combined)  # type: ignore[arg-type]


def single_user(context: dict[str, Any]) -> list[Recipient]:
    """A single explicit User passed in context (e.g. security new-login).

    The event already knows exactly who to notify; this resolver just wraps it.
    """
    user = _require(context, "user")
    if user is None or not user.pk or not (user.email or "").strip():
        return []
    return [(user, "self")]


# --- Registry ----------------------------------------------------------------

_RESOLVERS: dict[Recipients, ResolverFn] = {
    Recipients.FOG_ADMINS: fog_admins,
    Recipients.GUILD_LEADERSHIP: guild_leadership,
    Recipients.GUILD_LEADERSHIP_OR_ADMINS: guild_leadership_or_admins,
    Recipients.CLASS_APPROVERS: class_approvers,
    Recipients.GUILD_LEADERSHIP_OR_CLASS_APPROVERS: guild_leadership_or_class_approvers,
    Recipients.SPACE_APPROVERS: space_approvers,
    Recipients.DISCOUNT_APPROVERS: discount_approvers,
    Recipients.EVENTS_APPROVERS: events_approvers,
    Recipients.GUILD_LEADERSHIP_OR_EVENTS_APPROVERS: guild_leadership_or_events_approvers,
    Recipients.BILLING_APPROVERS: billing_approvers,
    Recipients.GUILD_LEAD: guild_lead,
    Recipients.GUILD_MEMBERS: guild_members,
    Recipients.GUILD_ORIENTERS: guild_orienters,
    Recipients.ORIENTATION_RUNNER: orientation_runner,
    Recipients.REGISTRANT: registrant,
    Recipients.INSTRUCTOR: instructor,
    Recipients.NEXT_WAITLISTED: next_waitlisted,
    Recipients.TAB_MEMBER: tab_member,
    Recipients.INVITER: inviter,
    Recipients.INVITEE: invitee,
    Recipients.LEASE_TENANT: lease_tenant,
    Recipients.ALL_ACTIVE_MEMBERS: all_active_members,
    Recipients.ALL_GUILD_LEADS: all_guild_leads,
    Recipients.EVENT_AUDIENCE: event_audience,
    Recipients.ALL_VOTERS: all_voters,
    Recipients.EVERYONE_WITH_LOGIN: everyone_with_login,
    Recipients.RELEASE_AUDIENCE: release_audience,
    Recipients.SINGLE_USER: single_user,
}


def resolve(reference: Recipients, context: dict[str, Any]) -> list[Recipient]:
    """Resolve a recipient reference against an event context.

    Fails loudly if the reference has no registered resolver (a registry wiring
    error), rather than silently returning no recipients.
    """
    return _RESOLVERS[reference](context)


def get_resolver(reference: Recipients) -> ResolverFn:
    """Return the resolver callable for a reference. Raises ``KeyError`` if unknown."""
    return _RESOLVERS[reference]
