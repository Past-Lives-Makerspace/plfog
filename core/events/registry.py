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
    GUILD_ORIENTERS = "guild_orienters"
    ORIENTATION_RUNNER = "orientation_runner"
    REGISTRANT = "registrant"
    INSTRUCTOR = "instructor"
    NEXT_WAITLISTED = "next_waitlisted"
    TAB_MEMBER = "tab_member"
    INVITER = "inviter"
    LEASE_TENANT = "lease_tenant"
    ALL_ACTIVE_MEMBERS = "all_active_members"
    ALL_VOTERS = "all_voters"
    EVERYONE_WITH_LOGIN = "everyone_with_login"
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


def _channels_from_trigger(trigger: triggers.Trigger) -> tuple[ChannelSpec, ...]:
    """Translate a legacy :class:`core.triggers.Trigger`'s flags into channel specs.

    Mirrors today's ``dispatch()`` semantics exactly so the seeded registry is a
    faithful structural copy:

    * In-app is always present and on (``dispatch`` always writes a bell row).
    * Email is ``FORCED`` for ``force_email`` triggers, else default from
      ``email_default`` (on/off).
    * Push default from ``push_default`` (on/off).
    """
    specs: list[ChannelSpec] = [_IN_APP_ON]
    if trigger.force_email:
        specs.append(_EMAIL_FORCED)
    else:
        specs.append(_EMAIL_ON if trigger.email_default else _EMAIL_OFF)
    specs.append(ChannelSpec(Channel.PUSH, ChannelDefault.ON if trigger.push_default else ChannelDefault.OFF))
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
    "class_cancelled": Recipients.REGISTRANT,
    "class_details_changed": Recipients.REGISTRANT,
    "waitlist_spot_available": Recipients.NEXT_WAITLISTED,
    "waitlist_confirmed": Recipients.REGISTRANT,
    "refund_issued": Recipients.REGISTRANT,
    # Classes — instructor-side
    "instructor_class_approved": Recipients.INSTRUCTOR,
    "instructor_changes_requested": Recipients.INSTRUCTOR,
    "instructor_new_registration": Recipients.INSTRUCTOR,
    "instructor_class_at_capacity": Recipients.INSTRUCTOR,
    "class_review_requested": Recipients.GUILD_LEADERSHIP,
    "class_validation_requested": Recipients.FOG_ADMINS,
    # Guild voting
    "voting_cycle_open": Recipients.ALL_VOTERS,
    "voting_closing_soon": Recipients.ALL_VOTERS,
    "funding_results_published": Recipients.ALL_VOTERS,
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
    "lease_activated": Recipients.LEASE_TENANT,
    # Admin broadcasts
    "site_announcement": Recipients.ALL_ACTIVE_MEMBERS,
    # Security — forced, no toggle
    "new_login": Recipients.SINGLE_USER,
}

# Map each legacy trigger key to its SiteActivity kind where one exists today
# (audit-E). Triggers with no corresponding activity kind get ``None`` (no
# activity row is written when they are emitted).
_TRIGGER_ACTIVITY_KINDS: dict[str, str | None] = {
    "class_published": "class_published",
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
    "class_cancelled": "class_cancelled",
    "class_details_changed": None,
    "waitlist_spot_available": None,
    "waitlist_confirmed": None,
    "refund_issued": "refund_issued",
    "instructor_class_approved": None,
    "instructor_changes_requested": None,
    "instructor_new_registration": None,
    "instructor_class_at_capacity": None,
    "class_review_requested": None,
    "class_validation_requested": None,
    "voting_cycle_open": None,
    "voting_closing_soon": None,
    "funding_results_published": "funding_snapshot_taken",
    "guild_announcement": "guild_announcement",
    "orientation_requested": "orientation_requested",
    "orientation_update": None,
    "guild_joined": "guild_joined",
    "tab_charged": "tab_charged",
    "tab_charge_failed": "tab_charge_failed",
    "tab_entry_added": "tab_entry_added",
    "tab_approaching_limit": None,
    "invite_accepted": "invite_accepted",
    "new_member_joined": "member_signup",
    "lease_expiring": None,
    "lease_activated": "lease_activated",
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


EVENTS: list[EventType] = _seed_from_triggers()

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
