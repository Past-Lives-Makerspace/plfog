"""Default notification copy + documented merge fields (design §2.3, Decision 6).

This module is the **code-side seed** for the DB-backed copy catalogue. It owns,
per event:

* **default copy** per channel (``subject`` / ``body_text`` / ``body_html``) written
  with documented ``{{ merge_field }}`` placeholders;
* the **documented placeholder set** each event exposes (the only names the
  constrained renderer will substitute — see :mod:`core.events.rendering`);
* a **sample context** used to drive the admin live preview;
* a human **audience description** (resolved from the event's recipient resolver)
  for the catalogue.

The DB is the source of truth at send time; this module only *seeds* it (via the
``seed_notification_templates`` command) and only re-seeds rows the copy team has
not overridden. Nothing here is consumed by the existing senders — only the new
:func:`core.events.emit.emit` path reads the seeded copy.

Design choice — **defaults are generated, then selectively curated.** Every
registered event gets a serviceable default derived from its ``label`` /
``description`` so the catalogue is exhaustive on day one; the events with a richer
documented placeholder vocabulary (registrations, tabs, orientations, the new
voting/release/announcement emails) carry hand-authored copy in :data:`_CURATED`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.events.registry import Channel, EventType, Recipients, all_events, get_event

# Channels that carry authored copy. Discord reuses the in-app/email copy (it is a
# broadcast embed built from title+body); scheduled-email reuses the email copy.
# Authored rows are seeded for these three; the adapters fall back across them.
COPY_CHANNELS: tuple[Channel, ...] = (Channel.IN_APP, Channel.EMAIL, Channel.DISCORD)


@dataclass(frozen=True)
class ChannelCopy:
    """Default copy for one channel of one event."""

    subject: str = ""
    body_text: str = ""
    body_html: str = ""


@dataclass(frozen=True)
class EventCopy:
    """The seedable copy + documentation for one event.

    Args:
        placeholders: The documented merge-field names this event exposes. The
            constrained renderer substitutes ONLY these; anything else is flagged.
        sample_context: A representative context for the live preview — maps each
            placeholder to an example value.
        channels: Per-channel default copy (subject/body).
    """

    placeholders: tuple[str, ...]
    sample_context: dict[str, str]
    channels: dict[Channel, ChannelCopy] = field(default_factory=dict)

    def copy_for(self, channel: Channel) -> ChannelCopy:
        """Default copy for ``channel``, falling back across related channels.

        Discord and scheduled-email reuse email copy; email falls back to in-app
        copy; in-app falls back to a minimal title-only block. This keeps every
        declared channel seedable without authoring three near-identical bodies.
        """
        if channel in self.channels:
            return self.channels[channel]
        if channel in (Channel.DISCORD, Channel.SCHEDULED_EMAIL) and Channel.EMAIL in self.channels:
            return self.channels[Channel.EMAIL]
        if Channel.EMAIL in self.channels:
            return self.channels[Channel.EMAIL]
        if Channel.IN_APP in self.channels:
            return self.channels[Channel.IN_APP]
        return ChannelCopy()


# --- Audience descriptions (resolved from the recipient resolver) -------------

_AUDIENCE_DESCRIPTIONS: dict[Recipients, str] = {
    Recipients.FOG_ADMINS: "All FOG admins (site-wide).",
    Recipients.GUILD_LEADERSHIP: "The guild's lead and all of its staff.",
    Recipients.GUILD_LEAD: "The guild's lead only.",
    Recipients.GUILD_ORIENTERS: "The guild's lead and everyone holding the orienter role.",
    Recipients.ORIENTATION_RUNNER: "The staffer who claimed/ran the orientation.",
    Recipients.REGISTRANT: "The member the event is about (the registrant).",
    Recipients.INSTRUCTOR: "The class's instructor.",
    Recipients.NEXT_WAITLISTED: "The next member in line on the waitlist.",
    Recipients.TAB_MEMBER: "The member whose billing tab this concerns.",
    Recipients.INVITER: "The person who sent the invitation.",
    Recipients.LEASE_TENANT: "The member tenant of the lease.",
    Recipients.ALL_ACTIVE_MEMBERS: "Every active member.",
    Recipients.ALL_VOTERS: "Every member eligible to vote.",
    Recipients.EVERYONE_WITH_LOGIN: "Everyone with a login (members and past members).",
    Recipients.SINGLE_USER: "A single specific user.",
}


def audience_description(event: EventType) -> str:
    """A human, one-line description of who an event reaches (for the catalogue)."""
    return _AUDIENCE_DESCRIPTIONS[event.recipient]


# --- Curated copy for the events with a richer documented vocabulary ----------
#
# Placeholders are the documented per-event merge fields; sample_context drives the
# live preview. Keep the placeholder set and the sample context in lock-step — every
# placeholder used in the copy MUST appear in both (the seed command + a test assert
# this so unknown-variable markers never ship in the defaults).

_CURATED: dict[str, EventCopy] = {
    "registration_confirmed": EventCopy(
        placeholders=("member_name", "class_title", "class_starts_at", "class_url"),
        sample_context={
            "member_name": "Robin Vale",
            "class_title": "Intro to Lost-Wax Casting",
            "class_starts_at": "Saturday, July 12 at 1:00 PM",
            "class_url": "https://pastlives.example/classes/42/",
        },
        channels={
            Channel.IN_APP: ChannelCopy(
                subject="You're registered for {{ class_title }}",
                body_text="Your spot in {{ class_title }} is confirmed.",
            ),
            Channel.EMAIL: ChannelCopy(
                subject="You're registered: {{ class_title }}",
                body_text=(
                    "Hi {{ member_name }},\n\n"
                    "Your registration for {{ class_title }} is confirmed. "
                    "It starts {{ class_starts_at }}.\n\n"
                    "Details: {{ class_url }}\n\n"
                    "See you there!\nPast Lives Makerspace"
                ),
                body_html=(
                    "<p>Hi {{ member_name }},</p>"
                    "<p>Your registration for <strong>{{ class_title }}</strong> is confirmed. "
                    "It starts {{ class_starts_at }}.</p>"
                    '<p><a href="{{ class_url }}">View the class</a></p>'
                    "<p>See you there!<br>Past Lives Makerspace</p>"
                ),
            ),
        },
    ),
    "class_reminder": EventCopy(
        placeholders=("member_name", "class_title", "class_starts_at", "class_url"),
        sample_context={
            "member_name": "Robin Vale",
            "class_title": "Intro to Lost-Wax Casting",
            "class_starts_at": "tomorrow at 1:00 PM",
            "class_url": "https://pastlives.example/classes/42/",
        },
        channels={
            Channel.IN_APP: ChannelCopy(
                subject="Reminder: {{ class_title }}",
                body_text="{{ class_title }} starts {{ class_starts_at }}.",
            ),
            Channel.EMAIL: ChannelCopy(
                subject="Reminder: {{ class_title }} is coming up",
                body_text=(
                    "Hi {{ member_name }},\n\n"
                    "Just a reminder that {{ class_title }} starts {{ class_starts_at }}.\n\n"
                    "Details: {{ class_url }}\n\nPast Lives Makerspace"
                ),
                body_html=(
                    "<p>Hi {{ member_name }},</p>"
                    "<p>Just a reminder that <strong>{{ class_title }}</strong> starts {{ class_starts_at }}.</p>"
                    '<p><a href="{{ class_url }}">View the class</a></p>'
                    "<p>Past Lives Makerspace</p>"
                ),
            ),
        },
    ),
    "class_cancelled": EventCopy(
        placeholders=("member_name", "class_title", "class_starts_at"),
        sample_context={
            "member_name": "Robin Vale",
            "class_title": "Intro to Lost-Wax Casting",
            "class_starts_at": "Saturday, July 12",
        },
        channels={
            Channel.IN_APP: ChannelCopy(
                subject="{{ class_title }} was cancelled",
                body_text="{{ class_title }} on {{ class_starts_at }} has been cancelled.",
            ),
            Channel.EMAIL: ChannelCopy(
                subject="Cancelled: {{ class_title }}",
                body_text=(
                    "Hi {{ member_name }},\n\n"
                    "Unfortunately {{ class_title }} on {{ class_starts_at }} has been cancelled. "
                    "Any payment will be refunded.\n\nPast Lives Makerspace"
                ),
                body_html=(
                    "<p>Hi {{ member_name }},</p>"
                    "<p>Unfortunately <strong>{{ class_title }}</strong> on {{ class_starts_at }} "
                    "has been cancelled. Any payment will be refunded.</p>"
                    "<p>Past Lives Makerspace</p>"
                ),
            ),
        },
    ),
    "tab_charged": EventCopy(
        placeholders=("member_name", "amount", "tab_url"),
        sample_context={
            "member_name": "Robin Vale",
            "amount": "$24.00",
            "tab_url": "https://pastlives.example/tab/",
        },
        channels={
            Channel.IN_APP: ChannelCopy(
                subject="Your tab was charged",
                body_text="We charged {{ amount }} to your tab.",
            ),
            Channel.EMAIL: ChannelCopy(
                subject="Receipt: {{ amount }} charged to your tab",
                body_text=(
                    "Hi {{ member_name }},\n\n"
                    "We charged {{ amount }} to your tab. View your tab: {{ tab_url }}\n\n"
                    "Past Lives Makerspace"
                ),
                body_html=(
                    "<p>Hi {{ member_name }},</p>"
                    "<p>We charged <strong>{{ amount }}</strong> to your tab.</p>"
                    '<p><a href="{{ tab_url }}">View your tab</a></p>'
                    "<p>Past Lives Makerspace</p>"
                ),
            ),
        },
    ),
    "tab_charge_failed": EventCopy(
        placeholders=("member_name", "amount", "tab_url"),
        sample_context={
            "member_name": "Robin Vale",
            "amount": "$24.00",
            "tab_url": "https://pastlives.example/tab/",
        },
        channels={
            Channel.IN_APP: ChannelCopy(
                subject="A charge failed",
                body_text="We couldn't charge {{ amount }} — please update your payment method.",
            ),
            Channel.EMAIL: ChannelCopy(
                subject="Action needed: a charge failed",
                body_text=(
                    "Hi {{ member_name }},\n\n"
                    "We couldn't charge {{ amount }} to your tab. Please update your payment "
                    "method: {{ tab_url }}\n\nPast Lives Makerspace"
                ),
                body_html=(
                    "<p>Hi {{ member_name }},</p>"
                    "<p>We couldn't charge <strong>{{ amount }}</strong> to your tab. "
                    "Please update your payment method.</p>"
                    '<p><a href="{{ tab_url }}">Update payment method</a></p>'
                    "<p>Past Lives Makerspace</p>"
                ),
            ),
        },
    ),
    "orientation_requested": EventCopy(
        placeholders=("member_name", "guild_name", "slot_starts_at", "orientation_url"),
        sample_context={
            "member_name": "Robin Vale",
            "guild_name": "Metal Guild",
            "slot_starts_at": "Friday, July 11 at 6:00 PM",
            "orientation_url": "https://pastlives.example/orientations/7/",
        },
        channels={
            Channel.IN_APP: ChannelCopy(
                subject="Orientation needs a runner",
                body_text="{{ member_name }} requested a {{ guild_name }} orientation for {{ slot_starts_at }}.",
            ),
            Channel.EMAIL: ChannelCopy(
                subject="A {{ guild_name }} orientation needs a runner",
                body_text=(
                    "{{ member_name }} requested a {{ guild_name }} orientation for {{ slot_starts_at }}.\n\n"
                    "Claim it: {{ orientation_url }}\n\nPast Lives Makerspace"
                ),
                body_html=(
                    "<p><strong>{{ member_name }}</strong> requested a {{ guild_name }} orientation "
                    "for {{ slot_starts_at }}.</p>"
                    '<p><a href="{{ orientation_url }}">Claim it</a></p>'
                    "<p>Past Lives Makerspace</p>"
                ),
            ),
        },
    ),
    "voting_closing_soon": EventCopy(
        placeholders=("member_name", "closes_at", "voting_url"),
        sample_context={
            "member_name": "Robin Vale",
            "closes_at": "in 2 days",
            "voting_url": "https://pastlives.example/guilds/voting/",
        },
        channels={
            Channel.IN_APP: ChannelCopy(
                subject="Voting closes soon",
                body_text="Guild voting closes {{ closes_at }} — cast your vote.",
            ),
            Channel.EMAIL: ChannelCopy(
                subject="Last call: guild voting closes {{ closes_at }}",
                body_text=(
                    "Hi {{ member_name }},\n\n"
                    "Guild funding voting closes {{ closes_at }}. Make sure your vote is in: "
                    "{{ voting_url }}\n\nPast Lives Makerspace"
                ),
                body_html=(
                    "<p>Hi {{ member_name }},</p>"
                    "<p>Guild funding voting closes {{ closes_at }}. Make sure your vote is in.</p>"
                    '<p><a href="{{ voting_url }}">Cast your vote</a></p>'
                    "<p>Past Lives Makerspace</p>"
                ),
            ),
        },
    ),
    "funding_results_published": EventCopy(
        placeholders=("member_name", "allocation_summary", "voting_url"),
        sample_context={
            "member_name": "Robin Vale",
            "allocation_summary": "Metal Guild $600 · Fiber Guild $400 · Print Guild $250",
            "voting_url": "https://pastlives.example/guilds/voting/history/",
        },
        channels={
            Channel.IN_APP: ChannelCopy(
                subject="Funding results are in",
                body_text="This month's guild allocations have been published.",
            ),
            Channel.EMAIL: ChannelCopy(
                subject="This month's guild funding results",
                body_text=(
                    "Hi {{ member_name }},\n\n"
                    "The votes are counted. This month's allocations:\n\n"
                    "{{ allocation_summary }}\n\nFull breakdown: {{ voting_url }}\n\n"
                    "Past Lives Makerspace"
                ),
                body_html=(
                    "<p>Hi {{ member_name }},</p>"
                    "<p>The votes are counted. This month's allocations:</p>"
                    "<p>{{ allocation_summary }}</p>"
                    '<p><a href="{{ voting_url }}">See the full breakdown</a></p>'
                    "<p>Past Lives Makerspace</p>"
                ),
            ),
        },
    ),
    "guild_announcement": EventCopy(
        placeholders=("guild_name", "announcement_title", "announcement_body", "guild_url"),
        sample_context={
            "guild_name": "Metal Guild",
            "announcement_title": "New anvil this Saturday",
            "announcement_body": "We're installing the new anvil — come help and learn.",
            "guild_url": "https://pastlives.example/guilds/3/",
        },
        channels={
            Channel.IN_APP: ChannelCopy(
                subject="{{ guild_name }}: {{ announcement_title }}",
                body_text="{{ announcement_body }}",
            ),
            Channel.EMAIL: ChannelCopy(
                subject="{{ guild_name }}: {{ announcement_title }}",
                body_text="{{ announcement_body }}\n\nVisit the guild: {{ guild_url }}\n\nPast Lives Makerspace",
                body_html=(
                    "<h2>{{ announcement_title }}</h2>"
                    "<p>{{ announcement_body }}</p>"
                    '<p><a href="{{ guild_url }}">Visit {{ guild_name }}</a></p>'
                    "<p>Past Lives Makerspace</p>"
                ),
            ),
        },
    ),
    "site_announcement": EventCopy(
        placeholders=("announcement_title", "announcement_body", "site_url"),
        sample_context={
            "announcement_title": "Holiday hours",
            "announcement_body": "The space is closed July 4th. Back to normal hours July 5th.",
            "site_url": "https://pastlives.example/",
        },
        channels={
            Channel.IN_APP: ChannelCopy(
                subject="{{ announcement_title }}",
                body_text="{{ announcement_body }}",
            ),
            Channel.EMAIL: ChannelCopy(
                subject="{{ announcement_title }}",
                body_text="{{ announcement_body }}\n\n{{ site_url }}\n\nPast Lives Makerspace",
                body_html=(
                    "<h2>{{ announcement_title }}</h2>"
                    "<p>{{ announcement_body }}</p>"
                    '<p><a href="{{ site_url }}">Past Lives Makerspace</a></p>'
                ),
            ),
        },
    ),
    "new_login": EventCopy(
        placeholders=("member_name", "device", "login_at"),
        sample_context={
            "member_name": "Robin Vale",
            "device": "Chrome on macOS",
            "login_at": "today at 9:14 AM",
        },
        channels={
            Channel.IN_APP: ChannelCopy(
                subject="New sign-in to your account",
                body_text="A new sign-in from {{ device }} {{ login_at }}.",
            ),
            Channel.EMAIL: ChannelCopy(
                subject="New sign-in to your Past Lives account",
                body_text=(
                    "Hi {{ member_name }},\n\n"
                    "We noticed a new sign-in from {{ device }} {{ login_at }}. "
                    "If this was you, no action is needed.\n\nPast Lives Makerspace"
                ),
                body_html=(
                    "<p>Hi {{ member_name }},</p>"
                    "<p>We noticed a new sign-in from <strong>{{ device }}</strong> {{ login_at }}. "
                    "If this was you, no action is needed.</p>"
                    "<p>Past Lives Makerspace</p>"
                ),
            ),
        },
    ),
}


# --- Generated fallback for every other registered event ----------------------


def _generic_copy(event: EventType) -> EventCopy:
    """A serviceable default for an event with no curated copy.

    Uses the event's ``label`` as the subject and its ``description`` as the body,
    with a single documented ``{{ member_name }}`` greeting placeholder so the
    catalogue is exhaustive and previewable without bespoke authoring for the
    long tail of operational events.
    """
    placeholders = ("member_name",)
    sample = {"member_name": "Robin Vale"}
    in_app = ChannelCopy(subject=event.label, body_text=event.description)
    email = ChannelCopy(
        subject=event.label,
        body_text=f"Hi {{{{ member_name }}}},\n\n{event.description}\n\nPast Lives Makerspace",
        body_html=f"<p>Hi {{{{ member_name }}}},</p><p>{event.description}</p><p>Past Lives Makerspace</p>",
    )
    return EventCopy(
        placeholders=placeholders,
        sample_context=sample,
        channels={Channel.IN_APP: in_app, Channel.EMAIL: email},
    )


def event_copy(event_key: str) -> EventCopy:
    """The :class:`EventCopy` (curated or generated) for ``event_key``.

    Raises:
        KeyError: If ``event_key`` is not a registered event (fails loudly).
    """
    event = get_event(event_key)
    if event_key in _CURATED:
        return _CURATED[event_key]
    return _generic_copy(event)


def placeholders_for(event_key: str) -> tuple[str, ...]:
    """The documented merge-field names ``event_key`` exposes."""
    return event_copy(event_key).placeholders


def sample_context_for(event_key: str) -> dict[str, str]:
    """The preview sample context for ``event_key`` (placeholder → example value)."""
    return dict(event_copy(event_key).sample_context)


def default_copy_for(event_key: str, channel: Channel) -> ChannelCopy:
    """The default copy for ``event_key`` on ``channel`` (curated or generated)."""
    return event_copy(event_key).copy_for(channel)


def seedable_rows() -> list[tuple[str, Channel, ChannelCopy]]:
    """Every ``(event_key, channel, default_copy)`` the seed command should create.

    One row per (registered event × :data:`COPY_CHANNELS`). The catalogue is
    exhaustive: every event the registry knows gets seeded copy on every authored
    channel, whether curated or generated.
    """
    rows: list[tuple[str, Channel, ChannelCopy]] = []
    for event in all_events():
        copy = event_copy(event.key)
        for channel in COPY_CHANNELS:
            rows.append((event.key, channel, copy.copy_for(channel)))
    return rows
