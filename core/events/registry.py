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

from dataclasses import dataclass, field
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
    GUILD_LEAD = "guild_lead"
    GUILD_MEMBERS = "guild_members"
    GUILD_ORIENTERS = "guild_orienters"
    ORIENTATION_RUNNER = "orientation_runner"
    REGISTRANT = "registrant"
    INSTRUCTOR = "instructor"
    NEXT_WAITLISTED = "next_waitlisted"
    TAB_MEMBER = "tab_member"
    INVITER = "inviter"
    INVITEE = "invitee"
    LEASE_TENANT = "lease_tenant"
    ALL_ACTIVE_MEMBERS = "all_active_members"
    ALL_GUILD_LEADS = "all_guild_leads"
    ALL_VOTERS = "all_voters"
    EVERYONE_WITH_LOGIN = "everyone_with_login"
    RELEASE_AUDIENCE = "release_audience"
    SINGLE_USER = "single_user"


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
    """

    key: str
    label: str
    description: str
    category: str
    recipient: Recipients
    channels: tuple[ChannelSpec, ...] = field(default_factory=tuple)
    activity_kind: str | None = None

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
_DISCORD_DM_OFF = ChannelSpec(Channel.DISCORD_DM, ChannelDefault.OFF)


def _channels_from_trigger(trigger: triggers.Trigger) -> tuple[ChannelSpec, ...]:
    """Translate a legacy :class:`core.triggers.Trigger`'s flags into channel specs.

    Mirrors today's ``dispatch()`` semantics exactly so the seeded registry is a
    faithful structural copy:

    * In-app is always present and on (``dispatch`` always writes a bell row).
    * Email is ``FORCED`` for ``force_email`` triggers, else default from
      ``email_default`` (on/off).
    * Push default from ``push_default`` (on/off).
    * Discord DM is always offered, default OFF — every member may opt into a
      personal DM for any of these events once they've linked their Discord account.
    """
    specs: list[ChannelSpec] = [_IN_APP_ON]
    if trigger.force_email:
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
    "class_review_requested": Recipients.GUILD_LEADERSHIP,
    "class_validation_requested": Recipients.FOG_ADMINS,
    # Guild activity
    "guild_announcement": Recipients.ALL_ACTIVE_MEMBERS,
    "orientation_requested": Recipients.GUILD_ORIENTERS,
    "orientation_update": Recipients.REGISTRANT,
    # guild_joined notifies the guild LEAD only today (audit-D audience J); the
    # orientation Decision-7 fan-out fix is scoped to orientation events, not this one.
    "guild_joined": Recipients.GUILD_LEAD,
    # Billing / tab
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
    # Security — forced, no toggle
    "new_login": Recipients.SINGLE_USER,
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
    # new_login logs NO activity via emit: ``core.signals._on_login`` writes the
    # LOGIN SiteActivity row unconditionally for EVERY login (the new_login event
    # only fires on a never-seen device signature). If emit also logged LOGIN, a
    # new-signature login would write the row twice. Keeping this ``None`` makes the
    # signal's unconditional log the single source of the LOGIN activity.
    "new_login": None,
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
VOTING_CLOSING_SOON = "voting.closing_soon"
VOTING_VOTE_SOON = "voting.vote_soon"
VOTING_RESULTS_PUBLISHED = "voting.results_published"
VOTING_RESULTS_READY = "voting.results_ready"
RELEASE_PUBLISHED = "release.published"
EVENT_GUILD_PUBLISHED = "event.guild_published"
EVENT_COMMUNITY_PUBLISHED = "event.community_published"
EVENT_LEAD_MEETING_PUBLISHED = "event.lead_meeting_published"


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
        description="A guild you're in posted an announcement.",
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
    #    Goes to the guild's members; in-app on, email OFF by default (calendar + Discord
    #    + bell is enough), Discord on. Carries ``guild`` in context, so the routing
    #    sibling dual-routes it to the central channel AND the guild's own webhook.
    EventType(
        key=EVENT_GUILD_PUBLISHED,
        label="New guild event",
        description="A guild you're in scheduled a meeting or event.",
        category="Events",
        recipient=Recipients.GUILD_MEMBERS,
        channels=(_IN_APP_ON, _EMAIL_OFF, _DISCORD_ON),
        activity_kind=None,
    ),
    # 8. event.community_published — an admin posts a site-wide community event (One Mic
    #    Night, Potluck). Every active member; in-app on, email OFF, Discord on (central).
    EventType(
        key=EVENT_COMMUNITY_PUBLISHED,
        label="New community event",
        description="A makerspace-wide community event was scheduled.",
        category="Events",
        recipient=Recipients.ALL_ACTIVE_MEMBERS,
        channels=(_IN_APP_ON, _EMAIL_OFF, _DISCORD_ON),
        activity_kind=None,
    ),
    # 9. event.lead_meeting_published — an admin posts the cross-guild Guild Lead Meeting.
    #    Notifies every guild lead/officer/staffer site-wide; in-app on, email OFF, Discord
    #    on (central). The event still shows on the Community Calendar for all members.
    EventType(
        key=EVENT_LEAD_MEETING_PUBLISHED,
        label="Guild Lead Meeting scheduled",
        description="A cross-guild leadership meeting was scheduled.",
        category="Events",
        recipient=Recipients.ALL_GUILD_LEADS,
        channels=(_IN_APP_ON, _EMAIL_OFF, _DISCORD_ON),
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
    return events


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
