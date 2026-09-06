"""Event registry — the catalogue of notifiable events (design §2.1).

Generalizes ``core/triggers.py``. Each :class:`EventType` declares a stable
``key`` (one vocabulary = activity kind + audit label + preference key), a
``label`` / ``description`` / ``category`` for the settings + admin UIs, a named
``recipient`` resolver reference (resolved lazily in :mod:`core.events.resolvers`,
§3), a list of ``channels`` each with a default state (``on`` / ``off`` /
``forced``), and an optional ``activity_kind`` (the :class:`core.models.SiteActivity`
kind written when the event is emitted).

Event **definitions** live here in code (versioned). Event **copy** is DB-backed
and admin-editable in a later phase — Phase 1 carries only the structural spine.

Phase-1 invariant: this registry is SEEDED from the legacy
:data:`core.triggers.TRIGGERS` catalogue so the two vocabularies line up exactly.
The legacy ``core/triggers.py`` stays present and working; nothing here replaces
``dispatch()`` yet.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum

from core import triggers


class Channel(str, Enum):
    """The delivery channels a recipient can receive an event on.

    ``IN_APP`` / ``EMAIL`` / ``PUSH`` wrap the existing mechanisms (the bell, the
    ``core.email.send`` choke-point, ``core/push.py``). ``SCHEDULED_EMAIL`` /
    ``DIGEST`` / ``DISCORD`` are declared now so the interface is stable; their
    adapters are registered shells in Phase 1 and built out in Phase 2.
    """

    IN_APP = "in_app"
    EMAIL = "email"
    PUSH = "push"
    SCHEDULED_EMAIL = "scheduled_email"
    DIGEST = "digest"
    DISCORD = "discord"
    # DISCORD_DM is a per-RECIPIENT channel (the bot DMs each opted-in, linked member),
    # distinct from DISCORD which is a per-event broadcast to a webhook. Default OFF —
    # a member opts in on the settings page and links their Discord account first.
    DISCORD_DM = "discord_dm"


class ChannelDefault(str, Enum):
    """The default state of a channel for an event, before any user preference.

    * ``ON`` — opted in by default; a user may opt out.
    * ``OFF`` — opted out by default; a user may opt in.
    * ``FORCED`` — always delivered; the user cannot opt out (essentials and
      operational mail per Decision 1: receipts, security, booking/orientation
      updates, class reminders).
    """

    ON = "on"
    OFF = "off"
    FORCED = "forced"


class Recipients(str, Enum):
    """Named references to the role × scope recipient resolvers (§3).

    The :class:`EventType` stores the *reference*; :mod:`core.events.resolvers`
    owns the callables. Keeping the reference as data (not a direct callable)
    avoids an import cycle (resolvers import models; the registry is imported very
    early) and keeps event definitions declarative.
    """

    FOG_ADMINS = "fog_admins"
    GUILD_LEADERSHIP = "guild_leadership"
    GUILD_LEADERSHIP_OR_ADMINS = "guild_leadership_or_admins"
    # Capability-scoped audiences (§ admin capabilities): the holders of a capability ONLY
    # (a plain admin who doesn't hold it gets nothing until granted — see
    # ``core.events.resolvers._capability_recipients``).
    CLASS_APPROVERS = "class_approvers"
    GUILD_LEADERSHIP_OR_CLASS_APPROVERS = "guild_leadership_or_class_approvers"
    SPACE_APPROVERS = "space_approvers"
    DISCOUNT_APPROVERS = "discount_approvers"
    EVENTS_APPROVERS = "events_approvers"
    GUILD_LEADERSHIP_OR_EVENTS_APPROVERS = "guild_leadership_or_events_approvers"
    BILLING_APPROVERS = "billing_approvers"
    # Composed union: fog admins OR REFUNDS capability holders — everyone who may issue
    # a refund (exactly the set ``refund_authority_required`` admits).
    REFUND_AUTHORITY = "refund_authority"
    GUILD_LEAD = "guild_lead"
    GUILD_MEMBERS = "guild_members"
    GUILD_ORIENTERS = "guild_orienters"
    # Composed (never a union): equipment in context -> the equipment's managers;
    # else the guild's orienters (personal-slot narrowing preserved).
    GUILD_ORIENTERS_OR_EQUIPMENT_MANAGERS = "guild_orienters_or_equipment_managers"
    ORIENTATION_RUNNER = "orientation_runner"
    REGISTRANT = "registrant"
    INSTRUCTOR = "instructor"
    CLASS_ROSTER = "class_roster"
    NEXT_WAITLISTED = "next_waitlisted"
    TAB_MEMBER = "tab_member"
    INVITER = "inviter"
    INVITEE = "invitee"
    LEASE_TENANT = "lease_tenant"
    ALL_ACTIVE_MEMBERS = "all_active_members"
    ALL_GUILD_LEADS = "all_guild_leads"
    EVENT_AUDIENCE = "event_audience"
    ALL_VOTERS = "all_voters"
    EVERYONE_WITH_LOGIN = "everyone_with_login"
    RELEASE_AUDIENCE = "release_audience"
    SINGLE_USER = "single_user"
    # Equipment managers: per-equipment staff rows ∪ the owning guild's leadership ∪
    # EQUIPMENT capability holders, deduped (a union of the three manage tiers).
    EQUIPMENT_MANAGERS = "equipment_managers"


@dataclass(frozen=True)
class ChannelSpec:
    """One channel and its default state for an event."""

    channel: Channel
    default: ChannelDefault

    @property
    def is_forced(self) -> bool:
        return self.default is ChannelDefault.FORCED


@dataclass(frozen=True)
class EventType:
    """One notifiable event — the unit the spine fans out.

    Args:
        key: Stable identifier; shared with the legacy trigger key, the activity
            label, and the preference key (one vocabulary).
        label: Human-facing title for settings / admin catalogue.
        description: One-line explanation for the same surfaces.
        category: Grouping for the settings + admin catalogue.
        recipient: Named resolver reference (§3); the resolver maps the event's
            ``context`` to ``[(User, reason)]``.
        channels: The channels this event fans out to, each with a default state.
        activity_kind: The :class:`core.models.SiteActivity` kind written when the
            event is emitted, or ``None`` if the event logs no activity row.
        email_shell: Which branded shell wraps this event's email HTML — ``"dark"``
            (the transactional navy card) or ``"light"`` (the hybrid release-style
            shell: logo hero band over a white body). Presentation only; the copy
            fragment stays admin-editable either way.
    """

    key: str
    label: str
    description: str
    category: str
    recipient: Recipients
    channels: tuple[ChannelSpec, ...] = field(default_factory=tuple)
    activity_kind: str | None = None
    email_shell: str = "light"

    def channel(self, channel: Channel) -> ChannelSpec | None:
        """Return this event's spec for ``channel``, or ``None`` if not declared."""
        for spec in self.channels:
            if spec.channel is channel:
                return spec
        return None

    def has_channel(self, channel: Channel) -> bool:
        return self.channel(channel) is not None

    @property
    def channel_list(self) -> list[Channel]:
        return [spec.channel for spec in self.channels]


# --- Channel-spec shorthands -------------------------------------------------

_IN_APP_ON = ChannelSpec(Channel.IN_APP, ChannelDefault.ON)
_EMAIL_ON = ChannelSpec(Channel.EMAIL, ChannelDefault.ON)
_EMAIL_OFF = ChannelSpec(Channel.EMAIL, ChannelDefault.OFF)
_EMAIL_FORCED = ChannelSpec(Channel.EMAIL, ChannelDefault.FORCED)
_PUSH_OFF = ChannelSpec(Channel.PUSH, ChannelDefault.OFF)
_PUSH_ON = ChannelSpec(Channel.PUSH, ChannelDefault.ON)

# Events that default Push ON — the "relatively important" ones worth a phone buzz:
# time-sensitive, actionable, or directly affecting the member. Every OTHER in-app event
# still OFFERS Push (a toggle on the settings matrix) but defaults OFF, so routine /
# workflow / FYI notices (meeting minutes approved, orientation completed, approval
# queues, chatty guild updates) never buzz a phone unless the member opts in.
_PUSH_ON_BY_DEFAULT: frozenset[str] = frozenset(
    {
        # Classes — your booking is affected, or a seat just opened
        "class_cancelled",
        "refund_issued",
        "class_reminder",
        "waitlist_spot_available",
        # Billing — money moved or is about to
        "tab_charged",
        "tab_charge_failed",
        "tab_approaching_limit",
        "tab_entry_added",
        # Spaces — your lease / space request outcome
        "lease_expiring",
        "space.request_approved",
        "space.request_declined",
        # Announcements you're meant to see
        "site_announcement",
        "guild_announcement",
        "class_announcement",
        # Events — reminders, plus the outcome of an event you proposed
        "event.reminder",
        "event.happening_now",
        "event.approved",
        "event.declined",
        "event.changes_requested",
        # Voting — act before it closes / results are in
        "voting.closing_soon",
        "voting.vote_soon",
        "voting.results_published",
        # Teaching — your class's review outcome (instructor)
        "instructor_class_approved",
        "instructor_changes_requested",
        # Membership — someone accepted the invite you sent
        "invite_accepted",
        # Equipment — your reservation is set (time-sensitive, carries the invite)
        "equipment.reservation_confirmed",
    }
)


def _with_push(event: EventType) -> EventType:
    """Offer Push on every in-app event, defaulting ON only for the important ones.

    Native push mirrors the in-app bell: any event that writes a bell row also offers a
    Push channel on the settings matrix. It defaults ON for the curated
    :data:`_PUSH_ON_BY_DEFAULT` set (time-sensitive / actionable / member-affecting) and
    OFF for everything else — routine, workflow, and FYI notices don't buzz a phone unless
    the member opts in. Events with no in-app bell (forced-email / broadcast-only) get no
    Push toggle. An existing Push spec is replaced in place so channel order is preserved.
    """
    if event.channel(Channel.IN_APP) is None:
        return event
    default = _PUSH_ON if event.key in _PUSH_ON_BY_DEFAULT else _PUSH_OFF
    if event.channel(Channel.PUSH) is not None:
        channels = tuple(default if spec.channel is Channel.PUSH else spec for spec in event.channels)
    else:
        channels = (*event.channels, default)
    return replace(event, channels=channels)


_DISCORD_DM_OFF = ChannelSpec(Channel.DISCORD_DM, ChannelDefault.OFF)
# Default-ON DM — reserved for per-person verdicts the member explicitly asked for (their
# event proposal's outcome): a Discord-originated proposer must hear back without hunting
# for an opt-in. The adapter no-ops for unlinked members; anyone can opt out in settings.
_DISCORD_DM_ON = ChannelSpec(Channel.DISCORD_DM, ChannelDefault.ON)


def _channels_from_trigger(trigger: triggers.Trigger) -> tuple[ChannelSpec, ...]:
    """Translate a legacy :class:`core.triggers.Trigger`'s flags into channel specs.

    Mirrors today's ``dispatch()`` semantics exactly so the seeded registry is a
    faithful structural copy:

    * In-app is always present and on (``dispatch`` always writes a bell row).
    * Email is omitted entirely for ``no_email`` triggers (in-app / push / Discord
      DM only), ``FORCED`` for ``force_email`` triggers, else default from
      ``email_default`` (on/off).
    * Push default from ``push_default`` (on/off).
    * Discord DM is always offered, default OFF — every member may opt into a
      personal DM for any of these events once they've linked their Discord account.
    """
    specs: list[ChannelSpec] = [_IN_APP_ON]
    if trigger.no_email:
        pass  # this trigger sends no email at all — no EMAIL channel is declared
    elif trigger.force_email:
        specs.append(_EMAIL_FORCED)
    else:
        specs.append(_EMAIL_ON if trigger.email_default else _EMAIL_OFF)
    specs.append(ChannelSpec(Channel.PUSH, ChannelDefault.ON if trigger.push_default else ChannelDefault.OFF))
    specs.append(_DISCORD_DM_OFF)
    return tuple(specs)


# Map each legacy trigger key to the resolver that best matches how it is sent
# today (audit-D §2). Triggers not listed fall back to ALL_ACTIVE_MEMBERS (the
# default broadcast audience, matching ``notifications.active_member_users``).
# These are the Phase-1 seed wiring; the migration phase refines per-site.
_TRIGGER_RESOLVERS: dict[str, Recipients] = {
    # Classes — member-side
    "class_published": Recipients.ALL_ACTIVE_MEMBERS,
    "class_reminder": Recipients.REGISTRANT,
    "registration_confirmed": Recipients.REGISTRANT,
    # class_cancelled is the site-wide "a class was cancelled" broadcast that
    # ClassOffering.archive() emits with no per-member context — it resolves ALL active
    # members (matching the pre-migration ``dispatch(active_member_users())``), not a
    # single registrant.
    "class_cancelled": Recipients.ALL_ACTIVE_MEMBERS,
    "waitlist_spot_available": Recipients.NEXT_WAITLISTED,
    "waitlist_confirmed": Recipients.REGISTRANT,
    "refund_issued": Recipients.REGISTRANT,
    # Classes — instructor-side
    "instructor_class_approved": Recipients.INSTRUCTOR,
    "instructor_changes_requested": Recipients.INSTRUCTOR,
    "instructor_new_registration": Recipients.INSTRUCTOR,
    # A guild-led class routes to that guild's leadership; a lead-less category (guild
    # is None in context) routes to the CMS Administrators (composition, not union).
    "class_review_requested": Recipients.GUILD_LEADERSHIP_OR_CLASS_APPROVERS,
    # The admin validation stage always routes to the CMS Administrators.
    "class_validation_requested": Recipients.CLASS_APPROVERS,
    # Guild activity
    "guild_announcement": Recipients.ALL_ACTIVE_MEMBERS,
    "orientation_requested": Recipients.GUILD_ORIENTERS_OR_EQUIPMENT_MANAGERS,
    "orientation_update": Recipients.REGISTRANT,
    # guild_joined notifies the guild LEAD only today (audit-D audience J); the
    # orientation Decision-7 fan-out fix is scoped to orientation events, not this one.
    "guild_joined": Recipients.GUILD_LEAD,
    # Billing / tab
    # refund_failed is the admin-facing async-refund-failure alert — it routes to
    # the Billing Administrators (holders only), like billing.charge_failed_admin.
    "refund_failed": Recipients.BILLING_APPROVERS,
    "tab_charged": Recipients.TAB_MEMBER,
    "tab_charge_failed": Recipients.TAB_MEMBER,
    "tab_entry_added": Recipients.TAB_MEMBER,
    "tab_approaching_limit": Recipients.TAB_MEMBER,
    # Membership
    "invite_accepted": Recipients.INVITER,
    "new_member_joined": Recipients.FOG_ADMINS,
    # Spaces / leases
    "lease_expiring": Recipients.LEASE_TENANT,
    # Admin broadcasts
    "site_announcement": Recipients.ALL_ACTIVE_MEMBERS,
}

# Map each legacy trigger key to its SiteActivity kind where one exists today
# (audit-E). Triggers with no corresponding activity kind get ``None`` (no
# activity row is written when they are emitted).
_TRIGGER_ACTIVITY_KINDS: dict[str, str | None] = {
    "class_published": None,  # see the class_cancelled note below (CmsActivity mirror is the source)
    "class_reminder": None,
    # ``registration_confirmed`` / ``waitlist_confirmed`` / ``class_review_requested`` /
    # ``instructor_class_approved`` log NO SiteActivity via emit: the classes app writes its
    # own ``CmsActivity`` row at each of these workflow points, and ``classes.activity.log``
    # already MIRRORS that CmsActivity into the matching SiteActivity kind
    # (registration_created→class_registered, waitlist_joined→class_waitlist_joined,
    # class_submitted, class_approved — see ``classes/activity._SITE_KIND_MAP``). If emit
    # also logged the SiteActivity here it would write the row twice. Keeping these ``None``
    # makes the CmsActivity mirror the single source of the SiteActivity, exactly as today.
    "registration_confirmed": None,
    # ``class_cancelled`` / ``class_published`` / ``refund_issued`` log NO SiteActivity
    # via emit: the classes app already writes the CmsActivity at each workflow point and
    # ``classes.activity.log`` MIRRORS it into the matching SiteActivity kind
    # (class_archived→class_cancelled, class_published→class_published,
    # registration_refunded→refund_issued — see ``classes.activity._SITE_KIND_MAP``). If
    # emit also logged the SiteActivity here it would write the row twice; keeping these
    # ``None`` makes the CmsActivity mirror the single source, exactly as before the
    # dispatch→emit migration.
    "class_cancelled": None,
    "waitlist_spot_available": None,
    "waitlist_confirmed": None,
    "refund_issued": None,
    # refund_failed logs no SiteActivity: the refund service writes the CmsActivity
    # (REGISTRATION_REFUND_FAILED) itself, deliberately unmirrored to the site feed.
    "refund_failed": None,
    "instructor_class_approved": None,
    "instructor_changes_requested": None,
    "instructor_new_registration": None,
    "class_review_requested": None,
    "class_validation_requested": None,
    "guild_announcement": "guild_announcement",
    "orientation_requested": "orientation_requested",
    "orientation_update": None,
    "guild_joined": "guild_joined",
    "tab_charged": "tab_charged",
    "tab_charge_failed": "tab_charge_failed",
    # ``tab_entry_added`` logs NO SiteActivity via emit: ``Tab.add_entry`` already writes
    # the TAB_ENTRY_ADDED SiteActivity (attributed to the adding admin). Keeping this
    # ``None`` makes that the single source after the dispatch→emit migration.
    "tab_entry_added": None,
    "tab_approaching_limit": None,
    "invite_accepted": "invite_accepted",
    "new_member_joined": "member_signup",
    "lease_expiring": None,
    "site_announcement": "site_announcement",
}


def _seed_from_triggers() -> list[EventType]:
    """Build the event catalogue from the legacy trigger catalogue.

    Every legacy trigger becomes one :class:`EventType` with the same key, label,
    description, and category, its resolver from ``_TRIGGER_RESOLVERS`` (defaulting
    to ALL_ACTIVE_MEMBERS), its channels translated from the trigger's flags, and
    its activity kind from ``_TRIGGER_ACTIVITY_KINDS``. This guarantees the new
    registry is a faithful, exhaustive copy of the existing catalogue.
    """
    events: list[EventType] = []
    for trigger in triggers.TRIGGERS:
        events.append(
            EventType(
                key=trigger.key,
                label=trigger.label,
                description=trigger.description,
                category=trigger.category,
                recipient=_TRIGGER_RESOLVERS.get(trigger.key, Recipients.ALL_ACTIVE_MEMBERS),
                channels=_channels_from_trigger(trigger),
                activity_kind=_TRIGGER_ACTIVITY_KINDS.get(trigger.key),
            )
        )
    return events


# --- Phase 6: net-new user-facing events on the spine (design §4, Decisions 4-5) -
#
# These events are NOT seeded from the legacy trigger catalogue — they are real new
# broadcasts/emails the redesign adds. Two of them (``guild_announcement`` /
# ``site_announcement``) re-use the existing seeded keys (already curated in
# ``copy.py``) but REPLACE the Phase-1 seed entry so they carry the correct
# role×scope resolver and the Discord broadcast channel. The other four are entirely
# new keys (dotted, matching the design's vocabulary).
#
# Channel-spec shorthands for the new events:
_DISCORD_ON = ChannelSpec(Channel.DISCORD, ChannelDefault.ON)

# New event keys (single vocabulary — these strings ARE the preference / audit keys).
CLASS_PUBLISHED = "class_published"  # re-uses the seeded key + ADDS the Discord broadcast channel
MEMBER_INVITED = "member.invited"
MEMBER_LOGIN_INVITE = "member.login_invite"
GUILD_ANNOUNCEMENT = "guild_announcement"  # re-uses the seeded key + curated copy
SITE_ANNOUNCEMENT = "site_announcement"  # re-uses the seeded key + curated copy
CLASS_ANNOUNCEMENT = "class_announcement"  # an instructor posts to their class's confirmed roster
VOTING_CLOSING_SOON = "voting.closing_soon"
VOTING_VOTE_SOON = "voting.vote_soon"
VOTING_OFFICERS_CLOSING_SOON = "voting.officers_closing_soon"
VOTING_RESULTS_PUBLISHED = "voting.results_published"
VOTING_RESULTS_READY = "voting.results_ready"
VOTING_DISCORD_REMINDER = "voting.discord_reminder"
VOTING_RESULTS_DISCORD = "voting.results_discord"
RELEASE_PUBLISHED = "release.published"
EVENT_GUILD_PUBLISHED = "event.guild_published"
EVENT_COMMUNITY_PUBLISHED = "event.community_published"
EVENT_LEAD_MEETING_PUBLISHED = "event.lead_meeting_published"
GUILD_ANNOUNCEMENT_SUBMITTED = "guild_announcement.submitted"
# Instructor-raised staff notices on a live class (class-lifecycle spec PR 2).
CLASS_CANCELLED_ADMIN_NOTICE = "class_cancelled_admin_notice"
CLASS_CHANGE_REQUESTED = "class_change_requested"
GUILD_ANNOUNCEMENT_APPROVED = "guild_announcement.approved"
GUILD_ANNOUNCEMENT_CHANGES_REQUESTED = "guild_announcement.changes_requested"
GUILD_ANNOUNCEMENT_DECLINED = "guild_announcement.declined"
EVENT_SUBMITTED = "event.submitted"
EVENT_APPROVED = "event.approved"
EVENT_CHANGES_REQUESTED = "event.changes_requested"
EVENT_DECLINED = "event.declined"
EVENT_REMINDER = "event.reminder"
EVENT_HAPPENING_NOW = "event.happening_now"
SPACE_LEASE_REQUESTED = "space.lease_requested"
SPACE_CUBBY_REQUESTED = "space.cubby_requested"
SPACE_REQUEST_APPROVED = "space.request_approved"
SPACE_REQUEST_DECLINED = "space.request_declined"
DISCORD_GUILDS_IMPORTED = "discord_guilds_imported"
ORIENTATION_COMPLETED = "orientation.completed"  # dotted, matches the new-event vocabulary
MEETING_ITEM_PROPOSED = "meeting.item_proposed"
MEETING_ITEM_DECIDED = "meeting.item_decided"
MEETING_MINUTES_APPROVED = "meeting.minutes_approved"
MEETING_COUNCIL_MINUTES_APPROVED = "meeting.council_minutes_approved"
DISCOUNT_CODE_REQUESTED = "discount_code.requested"  # a new code awaits approval (Discount Admins)
BILLING_CHARGE_FAILED_ADMIN = "billing.charge_failed_admin"  # a member's tab charge failed (Billing Admins)
WAITLIST_PROMOTED = "waitlist_promoted"  # staff hand-picked a waitlister into the class (plain "you're in")
WAITLIST_PROMOTED_PAY = "waitlist_promoted_pay"  # promoted with a balance due — "you're in" + pay link
REGISTRATION_REMOVED = "registration_removed"  # staff removed a registrant (seat-holder or waitlister)
GUILD_WELCOME = "guild_welcome"  # transactional per-guild join welcome — email only via email_to, no matrix row
EQUIPMENT_RESERVATION_CONFIRMED = "equipment.reservation_confirmed"  # your reservation is set (+ .ics)
EQUIPMENT_RESERVATION_CANCELLED_BY_MANAGER = "equipment.reservation_cancelled_by_manager"  # with the reason
EQUIPMENT_RESERVATION_MADE = "equipment.reservation_made"  # awareness ping to the equipment's managers

# event.reminder keeps Discord OFF (the bell is enough; per-offset channel posts would
# clutter the guild channel) but declares it so a lead can flip it on later; happening-now
# posts a one-shot "starting now" to the guild channel.
_DISCORD_OFF = ChannelSpec(Channel.DISCORD, ChannelDefault.OFF)


_NEW_EVENTS: list[EventType] = [
    # 0. class_published — a newly published class/workshop, broadcast site-wide to all
    #    active members. REPLACES the Phase-1 seed entry to ADD the Discord broadcast
    #    channel (in-app stays ON, email stays OFF, push stays OFF). It is site-wide, so
    #    it posts to the central/global webhook only — no guild webhook (its emit carries
    #    no ``guild`` in context). ``activity_kind`` stays None: the classes app writes the
    #    CmsActivity and ``classes.activity.log`` MIRRORS it into the matching SiteActivity
    #    kind (see ``_TRIGGER_ACTIVITY_KINDS`` above), so emit must not log a duplicate.
    EventType(
        key=CLASS_PUBLISHED,
        label="New class published",
        description="A new class or workshop goes live.",
        category="Classes",
        recipient=Recipients.ALL_ACTIVE_MEMBERS,
        channels=(_IN_APP_ON, _EMAIL_OFF, _PUSH_OFF, _DISCORD_ON),
        activity_kind=None,
    ),
    # 1. member.invited — the invitee MUST receive it (forced email). In-app would
    #    have nowhere to land (the invitee has no account yet), so email only.
    EventType(
        key=MEMBER_INVITED,
        label="You're invited to Past Lives",
        description="A makerspace invitation was sent to a prospective member.",
        category="Membership",
        recipient=Recipients.INVITEE,
        channels=(_EMAIL_FORCED,),
        # No activity row from emit: ``Invite.create_and_send`` already logs the
        # MEMBER_INVITED SiteActivity with the inviting admin as the actor (the
        # email-sending path doesn't know who that is). Keeping this ``None`` makes
        # that the single, correctly-attributed source of the activity row.
        activity_kind=None,
    ),
    # 1b. member.login_invite — an existing, not-signed-in member is emailed a
    #    first-time sign-in link (distinct from member.invited, which rejects existing
    #    members). The member already has an account (provisioned), but the whole point
    #    is to reach someone who hasn't logged in — an in-app bell they'll never check
    #    is noise — so this is a forced email only, addressed to the single member.
    EventType(
        key=MEMBER_LOGIN_INVITE,
        label="Sign in to Past Lives for the first time",
        description="An existing member who hasn't signed in yet was emailed a sign-in link.",
        category="Membership",
        recipient=Recipients.SINGLE_USER,
        channels=(_EMAIL_FORCED,),
        # No activity row from emit — the hub view that triggers this stamps its own
        # audit/activity (Phase 2); keeping this None avoids a duplicate.
        activity_kind=None,
    ),
    # 2. guild.announcement — a guild lead/staff posts; the guild's members hear it.
    #    In-app on, email opt-out, Discord broadcast on. REPLACES the Phase-1 seed
    #    entry (which routed to ALL_ACTIVE_MEMBERS with no Discord).
    EventType(
        key=GUILD_ANNOUNCEMENT,
        label="Guild announcement",
        description="A guild you follow posted an announcement. Pick which guilds in your hub Settings.",
        category="Guilds",
        recipient=Recipients.GUILD_MEMBERS,
        channels=(_IN_APP_ON, _EMAIL_ON, _DISCORD_ON),
        activity_kind="guild_announcement",
    ),
    # 3. site.announcement — an admin broadcasts site-wide to all active members.
    #    In-app on, email opt-out, Discord broadcast on. REPLACES the Phase-1 seed
    #    entry (adds the Discord channel).
    EventType(
        key=SITE_ANNOUNCEMENT,
        label="Makerspace-wide announcement",
        description="Staff posted a site-wide notice.",
        category="Announcements",
        recipient=Recipients.ALL_ACTIVE_MEMBERS,
        channels=(_IN_APP_ON, _EMAIL_ON, _DISCORD_ON),
        activity_kind="site_announcement",
    ),
    # 3b. class.announcement — an instructor posts to their class's confirmed roster.
    #     In-app on, email opt-out, push on by default (via _with_push). No Discord
    #     broadcast and no activity row: a class announcement is a direct notice to
    #     enrolled students, not a public site-wide post.
    EventType(
        key=CLASS_ANNOUNCEMENT,
        label="Class announcement",
        description="An instructor of a class you're in posted an update.",
        category="Classes",
        recipient=Recipients.CLASS_ROSTER,
        channels=(_IN_APP_ON, _EMAIL_ON),
        activity_kind=None,
    ),
    # 4. voting.closing_soon — scheduled N days (VotingSettings.reminder_lead_days)
    #    before the month-end close, to each member who has voted, carrying their own
    #    recorded 1st/2nd/3rd. Per-member (REGISTRANT), so email + in-app only — a
    #    personalized per-member email is not a broadcast (no Discord).
    EventType(
        key=VOTING_CLOSING_SOON,
        label="Polls closing soon",
        description="A few days before the monthly funding vote closes, to members who've voted.",
        category="Voting",
        recipient=Recipients.REGISTRANT,
        channels=(_IN_APP_ON, _EMAIL_ON),
        activity_kind=None,
        email_shell="light",
    ),
    # 4b. voting.vote_soon — the nudge to members who've signed in but never voted.
    #    Per-member (REGISTRANT), email + in-app.
    EventType(
        key=VOTING_VOTE_SOON,
        label="Vote soon",
        description="A reminder to members who've signed in but haven't cast a funding vote yet.",
        category="Voting",
        recipient=Recipients.REGISTRANT,
        channels=(_IN_APP_ON, _EMAIL_ON),
        activity_kind=None,
        email_shell="light",
    ),
    # 4c. voting.officers_closing_soon — the heads-up to guild leadership (leads, guild
    #    staff, and FOG guild officers) fired at the same lead as the member reminders,
    #    carrying turnout so officers can rally their guilds before close. One shared
    #    message (no per-member ballot), so a broadcast-style recipient list is correct.
    EventType(
        key=VOTING_OFFICERS_CLOSING_SOON,
        label="Officer heads-up: vote closing",
        description="A turnout heads-up to guild leadership before the monthly funding vote closes.",
        category="Voting",
        recipient=Recipients.ALL_GUILD_LEADS,
        channels=(_IN_APP_ON, _EMAIL_ON),
        activity_kind=None,
        email_shell="light",
    ),
    # 5. voting.results_published — the personalized member results email, sent only on
    #    the admin's Send results click (FundingSnapshot.send_results loops raw_votes
    #    and emits once per voter). Per-member (REGISTRANT); the snapshot-taken activity
    #    is logged once in take(), so this writes no activity row.
    EventType(
        key=VOTING_RESULTS_PUBLISHED,
        label="Guild funding results published",
        description="This month's votes are counted and the allocation is published.",
        category="Voting",
        recipient=Recipients.REGISTRANT,
        channels=(_IN_APP_ON, _EMAIL_ON),
        activity_kind=None,
    ),
    # 5b. voting.results_ready — the admin-facing "results are in, review & send" ping,
    #    emitted by take(). Email + in-app to FOG admins; logs no activity (take does).
    EventType(
        key=VOTING_RESULTS_READY,
        label="Funding results ready to send",
        description="A funding snapshot was taken — review the numbers and send results to members.",
        category="Voting",
        recipient=Recipients.FOG_ADMINS,
        channels=(_IN_APP_ON, _EMAIL_ON),
        activity_kind=None,
    ),
    # 6. release.published — "a new version has been released!" to everyone with a
    #    login (∪ active members ∪ admins; everyone_with_login already supersets
    #    these but the union resolver is explicit + robust). Email opt-out, in-app
    #    on, Discord broadcast on. Mirrors the GitHub-Actions Discord changelog post.
    EventType(
        key=RELEASE_PUBLISHED,
        label="A new version is out",
        description="A new version of the Past Lives app was released.",
        category="Announcements",
        recipient=Recipients.RELEASE_AUDIENCE,
        channels=(_IN_APP_ON, _EMAIL_ON, _DISCORD_ON),
        activity_kind=None,
    ),
    # 7. event.guild_published — a guild lead/staffer posts their guild's meeting/event.
    #    Goes to the guild's members; in-app on, email ON by default (owner call, copy-review
    #    2026-08-18: a member who joined a guild should hear about its events by email unless
    #    they opt out), Discord on. Carries ``guild`` in context, so the routing sibling
    #    dual-routes it to the central channel AND the guild's own webhook.
    EventType(
        key=EVENT_GUILD_PUBLISHED,
        label="New guild event",
        description="A guild you follow scheduled a meeting or event.",
        category="Events",
        recipient=Recipients.GUILD_MEMBERS,
        channels=(_IN_APP_ON, _EMAIL_ON, _DISCORD_ON),
        activity_kind=None,
    ),
    # 8. event.community_published — an admin posts a site-wide community event (One Mic
    #    Night, Potluck). Every active member; in-app on, email ON by default (owner call,
    #    copy-review 2026-08-18), Discord on (central).
    EventType(
        key=EVENT_COMMUNITY_PUBLISHED,
        label="New community event",
        description="A makerspace-wide community event was scheduled.",
        category="Events",
        recipient=Recipients.ALL_ACTIVE_MEMBERS,
        channels=(_IN_APP_ON, _EMAIL_ON, _DISCORD_ON),
        activity_kind=None,
    ),
    # 9. event.lead_meeting_published — an admin posts the cross-guild Guild Lead Meeting.
    #    Notifies every guild lead/officer/staffer site-wide; in-app on, email ON by default
    #    (owner call, copy-review 2026-08-18), Discord on (central). The event still shows on
    #    the Calendar for all members.
    EventType(
        key=EVENT_LEAD_MEETING_PUBLISHED,
        label="Guild Lead Meeting scheduled",
        description="A cross-guild leadership meeting was scheduled.",
        category="Events",
        recipient=Recipients.ALL_GUILD_LEADS,
        channels=(_IN_APP_ON, _EMAIL_ON, _DISCORD_ON),
        activity_kind=None,
    ),
    # 10. guild_announcement.submitted — a member proposed an announcement for a guild; it
    #     lands in the reviewer queue. Goes to the guild's leadership (lead + staff); admins
    #     also see it in the queue. A per-person workflow reply: in-app + email, no Discord.
    EventType(
        key=GUILD_ANNOUNCEMENT_SUBMITTED,
        label="Announcement proposal submitted",
        description="A member proposed a guild announcement that needs review.",
        category="Guilds",
        recipient=Recipients.GUILD_LEADERSHIP,
        channels=(_IN_APP_ON, _EMAIL_ON),
        activity_kind=None,
    ),
    # 11. guild_announcement.approved — the proposer hears their announcement is posted.
    EventType(
        key=GUILD_ANNOUNCEMENT_APPROVED,
        label="Your announcement was approved",
        description="A reviewer approved a member's proposed announcement and it's now posted.",
        category="Guilds",
        recipient=Recipients.SINGLE_USER,
        channels=(_IN_APP_ON, _EMAIL_ON),
        activity_kind=None,
    ),
    # 12. guild_announcement.changes_requested — the proposer is asked to edit + resubmit.
    EventType(
        key=GUILD_ANNOUNCEMENT_CHANGES_REQUESTED,
        label="Changes requested on your announcement",
        description="A reviewer asked the proposer to adjust their announcement and resubmit.",
        category="Guilds",
        recipient=Recipients.SINGLE_USER,
        channels=(_IN_APP_ON, _EMAIL_ON),
        activity_kind=None,
    ),
    # 13. guild_announcement.declined — the proposal was turned down.
    EventType(
        key=GUILD_ANNOUNCEMENT_DECLINED,
        label="Your announcement wasn't posted",
        description="A reviewer declined a member's proposed announcement.",
        category="Guilds",
        recipient=Recipients.SINGLE_USER,
        channels=(_IN_APP_ON, _EMAIL_ON),
        activity_kind=None,
    ),
    # 14. event.submitted — a member proposed a Calendar event; it lands in the
    #     review queue. Goes to the guild's leadership OR (site-wide → admins). A per-person
    #     workflow reply: in-app + email, no Discord broadcast.
    EventType(
        key=EVENT_SUBMITTED,
        label="Event proposal submitted",
        description="A member proposed a Calendar event that needs review.",
        category="Events",
        recipient=Recipients.GUILD_LEADERSHIP_OR_EVENTS_APPROVERS,
        channels=(_IN_APP_ON, _EMAIL_ON),
        activity_kind=None,
    ),
    # 15. event.approved — the proposer hears their event is live on the calendar.
    EventType(
        key=EVENT_APPROVED,
        label="Your event was approved",
        description="A reviewer approved a member's proposed event and it's now published.",
        category="Events",
        recipient=Recipients.SINGLE_USER,
        channels=(_IN_APP_ON, _EMAIL_ON, _DISCORD_DM_ON),
        activity_kind=None,
    ),
    # 16. event.changes_requested — the proposer is asked to edit + resubmit.
    EventType(
        key=EVENT_CHANGES_REQUESTED,
        label="Changes requested on your event",
        description="A reviewer asked the proposer to adjust their event and resubmit.",
        category="Events",
        recipient=Recipients.SINGLE_USER,
        channels=(_IN_APP_ON, _EMAIL_ON, _DISCORD_DM_ON),
        activity_kind=None,
    ),
    # 17. event.declined — the proposal was turned down.
    EventType(
        key=EVENT_DECLINED,
        label="Your event wasn't approved",
        description="A reviewer declined a member's proposed event.",
        category="Events",
        recipient=Recipients.SINGLE_USER,
        channels=(_IN_APP_ON, _EMAIL_ON, _DISCORD_DM_ON),
        activity_kind=None,
    ),
    # 18. event.reminder — a 7/3/1-day-before nudge for an upcoming community event, to the
    #     same audience the launch announcement reached (by scope, via event_audience). In-app
    #     on; email + Discord OFF (the bell is enough — Discord flippable later).
    EventType(
        key=EVENT_REMINDER,
        label="Event reminder",
        description="A reminder before a community event you're invited to starts.",
        category="Events",
        recipient=Recipients.EVENT_AUDIENCE,
        channels=(_IN_APP_ON, _EMAIL_OFF, _DISCORD_OFF),
        activity_kind=None,
    ),
    # 19. event.happening_now — a single "starting now" ping to the same launch audience.
    #     In-app on, email off, Discord ON (a one-shot "starting now" in the channel is useful).
    EventType(
        key=EVENT_HAPPENING_NOW,
        label="Event starting now",
        description="A ping when a community event you're invited to begins.",
        category="Events",
        recipient=Recipients.EVENT_AUDIENCE,
        channels=(_IN_APP_ON, _EMAIL_OFF, _DISCORD_ON),
        activity_kind=None,
    ),
    # 20. discord_guilds_imported — the one "we set up your N guilds" confirmation sent
    #     right after a member links Discord and their reacted/app guilds are imported.
    #     Addressed with an explicit ``email_to`` (transactional: sends regardless of
    #     broadcast preferences — the member just proved account control via Discord
    #     OAuth), so this is EMAIL-only with no in-app bell row (the resolver only governs
    #     the un-used in-app/push fan-out — keep those off). The REGISTRANT resolver reads
    #     ``context["member"]``.
    EventType(
        key=DISCORD_GUILDS_IMPORTED,
        label="Your Past Lives guilds are set up",
        description="After linking Discord, a one-time confirmation of the guilds we set you up in.",
        category="Guilds",
        recipient=Recipients.REGISTRANT,
        channels=(_EMAIL_FORCED,),
        activity_kind=None,
    ),
    # 20b. guild_welcome — the per-guild join welcome email. Transactional: addressed with an
    #      explicit ``email_to`` (sends regardless of preferences — the member deliberately
    #      joined), so it declares NO channel at all. The REGISTRANT resolver reads
    #      ``context["member"]`` = None, so the unused in-app/push fan-out finds nobody, and
    #      declaring no EMAIL channel keeps it off the member settings matrix (like the
    #      orientation thank-you, which piggybacks on orientation_update). ``activity_kind``
    #      stays None — member_joined_guild's guild_joined emit already logs GUILD_JOINED.
    EventType(
        key=GUILD_WELCOME,
        label="Welcome to the guild",
        description="A warm welcome when a member joins one of your guilds.",
        category="Guilds",
        recipient=Recipients.REGISTRANT,
        channels=(),
        activity_kind=None,
    ),
    # 21. orientation.completed — a member finished their orientation; welcome them to the
    #     guild. Goes to the guild's existing members (GUILD_MEMBERS); in-app on + the guild's
    #     own Discord channel on (no email — a light social nudge, not an inbox item). Carries
    #     ``guild`` in context, so the routing sibling posts to the guild's own webhook.
    #     ``activity_kind`` stays None: ``complete_orientation`` already logs the
    #     ORIENTATION_COMPLETED SiteActivity, so emit must NOT log a duplicate (mirrors the
    #     class_published precedent above).
    EventType(
        key=ORIENTATION_COMPLETED,
        label="Orientation completed — welcome",
        description="A member finished their orientation; welcome them to the guild.",
        category="Orientations",
        recipient=Recipients.GUILD_MEMBERS,
        channels=(_IN_APP_ON, _DISCORD_ON),
        activity_kind=None,
    ),
    # 22-25. space.* — the interactive space map's request flow. All four are personal
    #     workflow notices (a named member asked; a named member is answered), never a
    #     broadcast, so they are in-app + email with no Discord. ``activity_kind`` is
    #     "space_request" on all four so the spine writes exactly one SiteActivity row per
    #     event — the views never log their own.
    #
    # 22. space.lease_requested — a member wants to lease a studio. Studios are org-owned,
    #     so this always routes to the makerspace admins.
    EventType(
        key=SPACE_LEASE_REQUESTED,
        label="Studio space requested",
        description="A member asked for a studio space from the space map.",
        category="Spaces & Equipment",
        recipient=Recipients.SPACE_APPROVERS,
        channels=(_IN_APP_ON, _EMAIL_ON),
        activity_kind="space_request",
    ),
    # 23. space.cubby_requested — a member wants a shelf. Routes to the makerspace admins,
    #     same inbox as a studio lease: the request flow was deliberately lightened so a
    #     single audience triages everything. Reversible — restore GUILD_LEADERSHIP_OR_ADMINS
    #     here (and the guild-lead branch in SpaceRequest.review_audience_label) to send a
    #     guild-owned shelf back to that guild's lead + staff.
    EventType(
        key=SPACE_CUBBY_REQUESTED,
        label="Shelf requested",
        description="A member asked for a shelf from the space map.",
        category="Spaces & Equipment",
        recipient=Recipients.SPACE_APPROVERS,
        channels=(_IN_APP_ON, _EMAIL_ON),
        activity_kind="space_request",
    ),
    # 24. space.request_approved — the member hears yes. Approving does not create a lease;
    #     the copy says a human will be in touch to finalize it.
    EventType(
        key=SPACE_REQUEST_APPROVED,
        label="Your space request was approved",
        description="A reviewer approved a member's studio or cubby request.",
        category="Spaces & Equipment",
        recipient=Recipients.SINGLE_USER,
        channels=(_IN_APP_ON, _EMAIL_ON),
        activity_kind="space_request",
    ),
    # 25. space.request_declined — the member hears no, with the reviewer's reason.
    EventType(
        key=SPACE_REQUEST_DECLINED,
        label="Update on your space request",
        description="A reviewer declined a member's studio or cubby request.",
        category="Spaces & Equipment",
        recipient=Recipients.SINGLE_USER,
        channels=(_IN_APP_ON, _EMAIL_ON),
        activity_kind="space_request",
    ),
    # 26. voting.discord_reminder — a single @everyone Discord post (no email/in-app) fired
    #     in the same tick as the per-member reminder sources. Shows live standings + close date.
    #     Discord-only because the email side is handled by the per-member events (closing_soon
    #     / vote_soon); emitting one broadcast here avoids N webhook calls for N members.
    EventType(
        key=VOTING_DISCORD_REMINDER,
        label="Voting reminder to #general-member-chat",
        description="A @everyone Discord post with current standings, sent before polls close.",
        category="Voting",
        recipient=Recipients.ALL_ACTIVE_MEMBERS,
        channels=(_DISCORD_ON,),
        activity_kind=None,
    ),
    # 27. voting.results_discord — a single @everyone Discord post (no email/in-app) announcing
    #     this month's allocation. Emitted once per send_results call alongside the per-member
    #     results emails so the public channel hears the outcome.
    EventType(
        key=VOTING_RESULTS_DISCORD,
        label="Funding results to #general-member-chat",
        description="A @everyone Discord post announcing this month's guild funding allocation.",
        category="Voting",
        recipient=Recipients.ALL_ACTIVE_MEMBERS,
        channels=(_DISCORD_ON,),
        activity_kind=None,
    ),
    # 28. meeting.item_proposed — a member proposed an agenda item for an upcoming meeting;
    #     it lands on the workspace's review strip. Goes to the guild's leadership plus
    #     admins (mirrors guild_announcement.submitted). For a council meeting ``guild`` is
    #     None → the resolver composes to admins only (documented guild_leadership behavior);
    #     any lead can still act from the workspace. A per-person workflow ping: in-app +
    #     email, no Discord.
    EventType(
        key=MEETING_ITEM_PROPOSED,
        label="Agenda item proposed",
        description="A member proposed an agenda item for an upcoming meeting.",
        category="Meetings",
        recipient=Recipients.GUILD_LEADERSHIP_OR_EVENTS_APPROVERS,
        channels=(_IN_APP_ON, _EMAIL_ON),
        activity_kind=None,
    ),
    # 29. meeting.item_decided — the proposer hears the outcome either way (added to the
    #     agenda, or declined with an optional note) — including the auto-decline when the
    #     minutes are approved. A transactional reply: in-app + email, no Discord.
    EventType(
        key=MEETING_ITEM_DECIDED,
        label="Your agenda item was decided",
        description="Leadership decided on a member's proposed agenda item.",
        category="Meetings",
        recipient=Recipients.SINGLE_USER,
        channels=(_IN_APP_ON, _EMAIL_ON),
        activity_kind=None,
    ),
    # 30. meeting.minutes_approved — a guild meeting's minutes were approved and locked.
    #     The guild's members, in-app on, email opt-in. NO Discord by owner decision
    #     (2026-09-03): an approval is routine housekeeping, not channel news.
    #     The spine writes the meeting_approved activity row (Meeting.approve doesn't).
    EventType(
        key=MEETING_MINUTES_APPROVED,
        label="Meeting minutes approved",
        description="A guild you follow approved and locked a meeting's minutes.",
        category="Meetings",
        recipient=Recipients.GUILD_MEMBERS,
        channels=(_IN_APP_ON, _EMAIL_OFF),
        activity_kind="meeting_approved",
    ),
    # 31. meeting.council_minutes_approved — the cross-guild council meeting's minutes were
    #     approved. All guild leads/staff/officers, in-app on, email opt-in. NO Discord by
    #     owner decision (2026-09-03), same as meeting.minutes_approved.
    EventType(
        key=MEETING_COUNCIL_MINUTES_APPROVED,
        label="Council minutes approved",
        description="The cross-guild council meeting's minutes were approved and locked.",
        category="Meetings",
        recipient=Recipients.ALL_GUILD_LEADS,
        channels=(_IN_APP_ON, _EMAIL_OFF),
        activity_kind="meeting_approved",
    ),
    # 32. discount_code.requested — a new discount code was created and awaits approval.
    #     Routes to the Discount Code Administrators (capability holders only). A per-person
    #     admin ping: in-app + email, no Discord. The
    #     DiscountCode.save creating-branch already logs the CmsActivity, so emit logs none.
    EventType(
        key=DISCOUNT_CODE_REQUESTED,
        label="Discount code needs approval",
        description="A member created a discount code that needs approval before it can be used.",
        category="Teaching",
        recipient=Recipients.DISCOUNT_APPROVERS,
        channels=(_IN_APP_ON, _EMAIL_ON),
        activity_kind=None,
    ),
    # 33. billing.charge_failed_admin — a member's monthly tab charge failed. The admin-facing
    #     half of the failure notice (the member's own ``tab_charge_failed`` bell row is a
    #     separate emit). Routes to the Billing Administrators (holders only): in-app + email,
    #     no Discord. ``activity_kind`` is None — the member-facing
    #     ``tab_charge_failed`` emit logs the single TAB_CHARGE_FAILED SiteActivity.
    EventType(
        key=BILLING_CHARGE_FAILED_ADMIN,
        label="Tab charge failed (admin alert)",
        description="A member's monthly tab charge failed — the admin heads-up to follow up.",
        category="Billing",
        recipient=Recipients.BILLING_APPROVERS,
        channels=(_IN_APP_ON, _EMAIL_ON),
        activity_kind=None,
    ),
    # Roster management — staff promote / remove notices to the registrant. The email
    # goes to the registration's raw address via ``email_to`` (guest-safe, never
    # pref-gated); the REGISTRANT resolver posts the bell row to the linked member
    # when one exists — exactly the ``waitlist_spot_available`` pattern. No activity
    # row from emit: the classes app writes its own CmsActivity at each workflow point.
    EventType(
        key=WAITLIST_PROMOTED,
        label="Added from the waitlist",
        description="Staff added you to a class straight from the waitlist — you're in.",
        category="Classes",
        recipient=Recipients.REGISTRANT,
        channels=(_IN_APP_ON, _EMAIL_ON),
        activity_kind=None,
    ),
    EventType(
        key=WAITLIST_PROMOTED_PAY,
        label="Added from the waitlist (payment due)",
        description="Staff added you to a paid class from the waitlist — your seat is held; a payment link is included.",
        category="Classes",
        recipient=Recipients.REGISTRANT,
        channels=(_IN_APP_ON, _EMAIL_ON),
        activity_kind=None,
    ),
    EventType(
        key=REGISTRATION_REMOVED,
        label="Removed from a class",
        description="Staff removed your registration or waitlist spot for a class.",
        category="Classes",
        recipient=Recipients.REGISTRANT,
        channels=(_IN_APP_ON, _EMAIL_ON),
        activity_kind=None,
    ),
    # --- Equipment reservations (equipment-reservations spec §8, PR 2) ----------
    # equipment.reservation_confirmed — the member's own booking receipt. Operational
    # mail like orientation updates: in-app on + email FORCED, push on (via
    # _PUSH_ON_BY_DEFAULT). The email carries a calendar invite (.ics) via the emit
    # attachments. Discord stays absent on the two personal events — a personal reservation
    # is not a broadcast (and the greeting rule stays un-walked-into).
    EventType(
        key=EQUIPMENT_RESERVATION_CONFIRMED,
        label="Reservation confirmed",
        description="Your equipment reservation is set. Comes with a calendar invite.",
        category="Spaces & Equipment",
        recipient=Recipients.SINGLE_USER,
        channels=(_IN_APP_ON, _EMAIL_FORCED),
        activity_kind=None,
    ),
    # equipment.reservation_cancelled_by_manager — the member hears a manager freed
    # their time, with the required reason. Forced operational mail; member self
    # cancel emits nothing (no approver exists to care).
    EventType(
        key=EQUIPMENT_RESERVATION_CANCELLED_BY_MANAGER,
        label="Reservation cancelled by a manager",
        description="A manager cancelled your equipment reservation and told you why.",
        category="Spaces & Equipment",
        recipient=Recipients.SINGLE_USER,
        channels=(_IN_APP_ON, _EMAIL_FORCED),
        activity_kind=None,
    ),
    # equipment.reservation_made — awareness, not action (no approval exists), to the
    # equipment's managers: in-app on, email opt-in. Grouped under Staff & leadership
    # on the settings page via the EQUIPMENT_MANAGERS recipient. The DISCORD broadcast
    # posts to the #reservations channel ONLY: the event is pinned to the Site Settings
    # discord_reservations_webhook_url (core.events.discord.SITE_CONFIG_EVENT_WEBHOOKS —
    # blank silences it, never the central notify webhook), and the emit context carries
    # no "guild" key, so the guild dual-route in emit._guild_broadcast never fires.
    EventType(
        key=EQUIPMENT_RESERVATION_MADE,
        label="New equipment reservation",
        description="A member reserved time on equipment you manage.",
        category="Spaces & Equipment",
        recipient=Recipients.EQUIPMENT_MANAGERS,
        channels=(_IN_APP_ON, _EMAIL_OFF, _DISCORD_ON),
        activity_kind=None,
    ),
    # class_cancelled_admin_notice — an instructor cancelled their own live class and
    # paid registrations need refunds. Money never moves on an instructor click, so the
    # people who CAN refund (fog admins OR REFUNDS holders, the REFUND_AUTHORITY union)
    # get an in-app row + email pointing at the class's Registrations tab. Never fires
    # for a free class or an admin's own cancel. Per-recipient only, no broadcast.
    # Grouped under Staff & leadership on the settings page via its recipient.
    EventType(
        key=CLASS_CANCELLED_ADMIN_NOTICE,
        label="Instructor cancelled a paid class",
        description="An instructor cancelled a live class that has paid registrations; refunds are needed.",
        category="Classes",
        recipient=Recipients.REFUND_AUTHORITY,
        channels=(_IN_APP_ON, _EMAIL_ON),
        activity_kind=None,
    ),
    # class_change_requested — an instructor asked for a structural change (title, dates,
    # price, capacity) to their live class, which only an admin may make. Routes to the
    # CMS Administrators with the note and a CTA to the admin edit page. Per-recipient
    # only, no broadcast; each request is its own dedupe period.
    EventType(
        key=CLASS_CHANGE_REQUESTED,
        label="Instructor asked for a class change",
        description="An instructor asked an admin to change a live class's title, dates, price, or capacity.",
        category="Classes",
        recipient=Recipients.CLASS_APPROVERS,
        channels=(_IN_APP_ON, _EMAIL_ON),
        activity_kind=None,
    ),
]


def _assemble_events() -> list[EventType]:
    """Seed from legacy triggers, then apply the Phase-6 net-new events.

    A new event whose key matches a seeded one REPLACES it (so the announcement
    events carry their role×scope resolver + Discord channel instead of the Phase-1
    structural copy); a new event with a fresh key is appended. The result keeps the
    legacy catalogue order, with brand-new keys after it.
    """
    events = _seed_from_triggers()
    by_key = {e.key: i for i, e in enumerate(events)}
    for new_event in _NEW_EVENTS:
        if new_event.key in by_key:
            events[by_key[new_event.key]] = new_event
        else:
            events.append(new_event)
    return [_with_push(event) for event in events]


EVENTS: list[EventType] = _assemble_events()

_BY_KEY: dict[str, EventType] = {e.key: e for e in EVENTS}


def get_event(key: str) -> EventType:
    """Return the :class:`EventType` for ``key``. Raises ``KeyError`` if unknown.

    Fails loudly (no silent fallback) so an unregistered event key surfaces at
    emit time rather than silently delivering nothing.
    """
    return _BY_KEY[key]


def all_events() -> list[EventType]:
    """All registered events, in catalogue (legacy trigger) order."""
    return list(EVENTS)
