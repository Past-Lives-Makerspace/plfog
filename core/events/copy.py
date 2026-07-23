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
    Recipients.GUILD_LEADERSHIP_OR_ADMINS: (
        "The guild's lead and staff, plus all admins (admins only for site-wide events)."
    ),
    Recipients.GUILD_LEAD: "The guild's lead only.",
    Recipients.GUILD_MEMBERS: "Every active member of the guild.",
    Recipients.GUILD_ORIENTERS: "The guild's lead and everyone holding the orienter role.",
    Recipients.ORIENTATION_RUNNER: "The staffer who claimed/ran the orientation.",
    Recipients.REGISTRANT: "The member the event is about (the registrant).",
    Recipients.INSTRUCTOR: "The class's instructor.",
    Recipients.NEXT_WAITLISTED: "The next member in line on the waitlist.",
    Recipients.TAB_MEMBER: "The member whose billing tab this concerns.",
    Recipients.INVITER: "The person who sent the invitation.",
    Recipients.INVITEE: "The person being invited (addressed by email; no account yet).",
    Recipients.LEASE_TENANT: "The member tenant of the lease.",
    Recipients.ALL_ACTIVE_MEMBERS: "Every active member.",
    Recipients.ALL_GUILD_LEADS: "Every guild lead, officer, and staffer (cross-guild).",
    Recipients.EVENT_AUDIENCE: (
        "Whoever the event's launch announcement reached — the guild's members, all guild leads, "
        "or every active member, by scope."
    ),
    Recipients.ALL_VOTERS: "Every member eligible to vote.",
    Recipients.EVERYONE_WITH_LOGIN: "Everyone with a login (members and past members).",
    Recipients.RELEASE_AUDIENCE: "Everyone with a login, plus all active members and admins.",
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
    "class_published": EventCopy(
        placeholders=("class_title", "class_url"),
        sample_context={
            "class_title": "Intro to Lost-Wax Casting",
            "class_url": "https://pastlives.example/classes/intro-to-lost-wax-casting/",
        },
        channels={
            Channel.IN_APP: ChannelCopy(
                subject="New class: {{ class_title }}",
                body_text="{{ class_title }} just went live.",
            ),
            Channel.EMAIL: ChannelCopy(
                subject="New class: {{ class_title }}",
                body_text=(
                    "{{ class_title }} just went live at Past Lives.\n\nSee the details and sign up: {{ class_url }}"
                ),
                body_html=(
                    "<p><strong>{{ class_title }}</strong> just went live at Past Lives.</p>"
                    '<p><a href="{{ class_url }}">See the details and sign up</a></p>'
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
    "tab_entry_added": EventCopy(
        placeholders=("member_name", "description", "amount", "tab_url"),
        sample_context={
            "member_name": "Robin Vale",
            "description": "Bandsaw blade",
            "amount": "$18.00",
            "tab_url": "https://pastlives.example/tab/",
        },
        channels={
            Channel.IN_APP: ChannelCopy(
                subject="Tab entry added",
                body_text="{{ description }} — {{ amount }} was added to your tab.",
            ),
            Channel.EMAIL: ChannelCopy(
                subject="{{ amount }} added to your tab",
                body_text=(
                    "Hi {{ member_name }},\n\n"
                    "{{ description }} — {{ amount }} was added to your tab at Past Lives. "
                    "It will be included in your next monthly charge.\n\n"
                    "See everything on your tab: {{ tab_url }}\n\n"
                    "If this doesn't look right, reply to this email and we'll sort it out.\n\n"
                    "Past Lives Makerspace"
                ),
                body_html=(
                    "<p>Hi {{ member_name }},</p>"
                    "<p><strong>{{ description }} — {{ amount }}</strong> was added to your tab at Past Lives. "
                    "It will be included in your next monthly charge.</p>"
                    '<p><a href="{{ tab_url }}">See everything on your tab</a></p>'
                    "<p>If this doesn't look right, reply to this email and we'll sort it out.</p>"
                    "<p>Past Lives Makerspace</p>"
                ),
            ),
        },
    ),
    "tab_approaching_limit": EventCopy(
        placeholders=("member_name", "balance", "limit", "tab_url"),
        sample_context={
            "member_name": "Robin Vale",
            "balance": "$168.00",
            "limit": "$200.00",
            "tab_url": "https://pastlives.example/tab/",
        },
        channels={
            Channel.IN_APP: ChannelCopy(
                subject="Your tab is near its limit",
                body_text="Your tab balance is {{ balance }} of {{ limit }}.",
            ),
            Channel.EMAIL: ChannelCopy(
                subject="Your tab is near its limit ({{ balance }} of {{ limit }})",
                body_text=(
                    "Hi {{ member_name }},\n\n"
                    "Your tab balance is {{ balance }} of your {{ limit }} limit. Once you reach the "
                    "limit your tab locks until it's paid down, so it's worth settling up before then.\n\n"
                    "Pay down your tab: {{ tab_url }}\n\n"
                    "Past Lives Makerspace"
                ),
                body_html=(
                    "<p>Hi {{ member_name }},</p>"
                    "<p>Your tab balance is <strong>{{ balance }}</strong> of your {{ limit }} limit. "
                    "Once you reach the limit your tab locks until it's paid down, so it's worth "
                    "settling up before then.</p>"
                    '<p><a href="{{ tab_url }}">Pay down your tab</a></p>'
                    "<p>Past Lives Makerspace</p>"
                ),
            ),
        },
    ),
    "refund_issued": EventCopy(
        placeholders=("member_name", "class_title", "amount", "registration_url"),
        sample_context={
            "member_name": "Robin",
            "class_title": "Intro to Lost-Wax Casting",
            "amount": "$65.00",
            "registration_url": "https://pastlives.example/classes/my/abc123/",
        },
        channels={
            Channel.IN_APP: ChannelCopy(
                subject="Refund issued",
                body_text="Your {{ amount }} for {{ class_title }} has been refunded.",
            ),
            Channel.EMAIL: ChannelCopy(
                subject="Refund issued for {{ class_title }}",
                body_text=(
                    "Hi {{ member_name }},\n\n"
                    "We've refunded {{ amount }} for {{ class_title }}. Refunds usually land back on "
                    "your original payment method within 5–10 business days.\n\n"
                    "View your booking: {{ registration_url }}\n\n"
                    "Past Lives Makerspace"
                ),
                body_html=(
                    "<p>Hi {{ member_name }},</p>"
                    "<p>We've refunded <strong>{{ amount }}</strong> for {{ class_title }}. Refunds usually "
                    "land back on your original payment method within 5–10 business days.</p>"
                    '<p><a href="{{ registration_url }}">View your booking</a></p>'
                    "<p>Past Lives Makerspace</p>"
                ),
            ),
        },
    ),
    "lease_expiring": EventCopy(
        placeholders=("member_name", "space_name", "end_date"),
        sample_context={
            "member_name": "Robin Vale",
            "space_name": "Studio 4",
            "end_date": "August 21, 2026",
        },
        channels={
            Channel.IN_APP: ChannelCopy(
                subject="Your lease is expiring",
                body_text="Your lease for {{ space_name }} ends on {{ end_date }}.",
            ),
            Channel.EMAIL: ChannelCopy(
                subject="Your {{ space_name }} lease ends {{ end_date }}",
                body_text=(
                    "Hi {{ member_name }},\n\n"
                    "Your lease for {{ space_name }} ends on {{ end_date }} — about a month from now.\n\n"
                    "If you'd like to renew, reply to this email or talk to a staff member at the space "
                    "and we'll get it sorted before the end date.\n\n"
                    "Past Lives Makerspace"
                ),
                body_html=(
                    "<p>Hi {{ member_name }},</p>"
                    "<p>Your lease for <strong>{{ space_name }}</strong> ends on {{ end_date }} — "
                    "about a month from now.</p>"
                    "<p>If you'd like to renew, reply to this email or talk to a staff member at the "
                    "space and we'll get it sorted before the end date.</p>"
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
    # --- Phase 6: net-new events (design §4) ---------------------------------
    "member.invited": EventCopy(
        placeholders=("invitee_email", "signup_url"),
        sample_context={
            "invitee_email": "newcomer@example.com",
            "signup_url": "https://pastlives.example/accounts/signup/?email=newcomer@example.com",
        },
        channels={
            Channel.EMAIL: ChannelCopy(
                subject="You're invited to Past Lives Makerspace",
                body_text=(
                    "You've been invited to join Past Lives Makerspace!\n\n"
                    "Click the link below to create your account:\n\n"
                    "{{ signup_url }}\n\n"
                    "If you didn't expect this invite, you can ignore this email."
                ),
                body_html=(
                    "<p>You've been invited to join <strong>Past Lives Makerspace</strong>!</p>"
                    '<div style="text-align:center; margin:24px 0 0;">'
                    '<a href="{{ signup_url }}" style="display:inline-block; padding:12px 32px; '
                    "background-color:#EEB44B; color:#092E4C; font-size:14px; font-weight:700; "
                    'text-decoration:none; border-radius:6px;">Create your account</a>'
                    "</div>"
                    '<p style="margin:16px 0 0; font-size:13px; color:#96ACBB;">'
                    "If you didn't expect this invite, you can ignore this email.</p>"
                ),
            ),
        },
    ),
    "member.login_invite": EventCopy(
        placeholders=("member_name", "login_url"),
        sample_context={
            "member_name": "Robin Vale",
            "login_url": "https://pastlives.example/accounts/login/code/?email=robin@example.com",
        },
        channels={
            Channel.EMAIL: ChannelCopy(
                subject="Sign in to Past Lives Makerspace",
                body_text=(
                    "Hi {{ member_name }},\n\n"
                    "Your Past Lives Makerspace account is ready — you just haven't signed in yet.\n\n"
                    "Use the link below to sign in for the first time. We'll email you a one-time "
                    "code to finish:\n\n"
                    "{{ login_url }}\n\n"
                    "See you at the space!\nPast Lives Makerspace"
                ),
                body_html=(
                    "<p>Hi {{ member_name }},</p>"
                    "<p>Your <strong>Past Lives Makerspace</strong> account is ready — you just "
                    "haven't signed in yet.</p>"
                    "<p>Use the button below to sign in for the first time. We'll email you a "
                    "one-time code to finish.</p>"
                    '<div style="text-align:center; margin:24px 0 0;">'
                    '<a href="{{ login_url }}" style="display:inline-block; padding:12px 32px; '
                    "background-color:#EEB44B; color:#092E4C; font-size:14px; font-weight:700; "
                    'text-decoration:none; border-radius:6px;">Sign in for the first time</a>'
                    "</div>"
                    '<p style="margin:16px 0 0; font-size:13px; color:#96ACBB;">'
                    "See you at the space! — Past Lives Makerspace</p>"
                ),
            ),
        },
    ),
    "voting.closing_soon": EventCopy(
        placeholders=("member_name", "cycle_label", "closes_on", "vote_1st", "vote_2nd", "vote_3rd", "voting_url"),
        sample_context={
            "member_name": "Robin Vale",
            "cycle_label": "June 2026",
            "closes_on": "June 30, 2026",
            "vote_1st": "Metal Guild",
            "vote_2nd": "Fiber Guild",
            "vote_3rd": "Wood Guild",
            "voting_url": "https://pastlives.example/guilds/voting/",
        },
        channels={
            Channel.IN_APP: ChannelCopy(
                subject="Polls closing soon — {{ cycle_label }}",
                body_text="{{ cycle_label }} guild voting closes {{ closes_on }}. Your current vote: {{ vote_1st }}, {{ vote_2nd }}, {{ vote_3rd }}.",
            ),
            Channel.EMAIL: ChannelCopy(
                subject="Polls closing soon: guild voting closes {{ closes_on }}",
                body_text=(
                    "Hi {{ member_name }},\n\n"
                    "The {{ cycle_label }} guild funding vote closes on {{ closes_on }}. You're currently "
                    "voting — 1st: {{ vote_1st }}, 2nd: {{ vote_2nd }}, 3rd: {{ vote_3rd }}.\n\n"
                    "Change it any time before close: {{ voting_url }}\n\nPast Lives Makerspace"
                ),
                body_html=(
                    "<p>Hi {{ member_name }},</p>"
                    "<p>The {{ cycle_label }} guild funding vote closes on <strong>{{ closes_on }}</strong>.</p>"
                    "<p>You're currently voting — 1st: <strong>{{ vote_1st }}</strong>, "
                    "2nd: <strong>{{ vote_2nd }}</strong>, 3rd: <strong>{{ vote_3rd }}</strong>.</p>"
                    '<p><a href="{{ voting_url }}">Change it any time before close</a></p>'
                    "<p>Past Lives Makerspace</p>"
                ),
            ),
        },
    ),
    "voting.vote_soon": EventCopy(
        placeholders=("member_name", "cycle_label", "closes_on", "voting_url"),
        sample_context={
            "member_name": "Robin Vale",
            "cycle_label": "June 2026",
            "closes_on": "June 30, 2026",
            "voting_url": "https://pastlives.example/guilds/voting/",
        },
        channels={
            Channel.IN_APP: ChannelCopy(
                subject="Vote soon — {{ cycle_label }}",
                body_text="You haven't cast a {{ cycle_label }} guild funding vote yet — it closes {{ closes_on }}.",
            ),
            Channel.EMAIL: ChannelCopy(
                subject="Vote soon: the {{ cycle_label }} guild funding vote closes {{ closes_on }}",
                body_text=(
                    "Hi {{ member_name }},\n\n"
                    "You haven't cast a guild funding vote yet for {{ cycle_label }} — it closes {{ closes_on }}. "
                    "It takes a minute and decides where the pool goes:\n\n"
                    "{{ voting_url }}\n\nPast Lives Makerspace"
                ),
                body_html=(
                    "<p>Hi {{ member_name }},</p>"
                    "<p>You haven't cast a guild funding vote yet for {{ cycle_label }} — it closes "
                    "<strong>{{ closes_on }}</strong>. It takes a minute and decides where the pool goes.</p>"
                    '<p><a href="{{ voting_url }}">Cast your vote</a></p>'
                    "<p>Past Lives Makerspace</p>"
                ),
            ),
        },
    ),
    "voting.results_published": EventCopy(
        placeholders=(
            "member_name",
            # Optional organizer note shown at the top of the email (blank for a normal
            # automated send; used for a one-off, e.g. a late "sorry this is overdue").
            "intro_note",
            "cycle_label",
            "allocation_summary",
            "vote_1st",
            "vote_2nd",
            "vote_3rd",
            "voting_url",
        ),
        sample_context={
            "member_name": "Robin Vale",
            "intro_note": "Heads-up: this one's a little late — going forward results are automated.",
            "cycle_label": "June 2026",
            "allocation_summary": "Metal Guild — $600.00 (45.0%)\nFiber Guild — $400.00 (30.0%)",
            "vote_1st": "Metal Guild",
            "vote_2nd": "Fiber Guild",
            "vote_3rd": "Wood Guild",
            "voting_url": "https://pastlives.example/guilds/voting/history/",
        },
        channels={
            Channel.IN_APP: ChannelCopy(
                subject="Funding results are in for {{ cycle_label }}",
                body_text="This cycle's guild allocations have been published.",
            ),
            Channel.EMAIL: ChannelCopy(
                subject="{{ cycle_label }} guild funding results",
                body_text=(
                    "Hi {{ member_name }},\n\n"
                    "{{ intro_note }}\n\n"
                    "The votes for {{ cycle_label }} are counted. Here's how the funding pool was split:\n\n"
                    "{{ allocation_summary }}\n\n"
                    "You were recorded as voting — 1st: {{ vote_1st }}, 2nd: {{ vote_2nd }}, 3rd: {{ vote_3rd }}.\n\n"
                    "Full breakdown: {{ voting_url }}\n\nPast Lives Makerspace"
                ),
                body_html=(
                    "<p>Hi {{ member_name }},</p>"
                    "<p>{{ intro_note }}</p>"
                    "<p>The votes for {{ cycle_label }} are counted. Here's how the funding pool was split:</p>"
                    "<pre>{{ allocation_summary }}</pre>"
                    "<p>You were recorded as voting — 1st: <strong>{{ vote_1st }}</strong>, "
                    "2nd: <strong>{{ vote_2nd }}</strong>, 3rd: <strong>{{ vote_3rd }}</strong>.</p>"
                    '<p><a href="{{ voting_url }}">See the full breakdown</a></p>'
                    "<p>Past Lives Makerspace</p>"
                ),
            ),
        },
    ),
    "voting.results_ready": EventCopy(
        placeholders=("cycle_label", "funding_pool", "votes_cast", "review_url"),
        sample_context={
            "cycle_label": "June 2026",
            "funding_pool": "1000.00",
            "votes_cast": "12",
            "review_url": "https://pastlives.example/manage/voting/history/7/",
        },
        channels={
            Channel.IN_APP: ChannelCopy(
                subject="Results ready to send — {{ cycle_label }}",
                body_text="A {{ cycle_label }} funding snapshot was taken (${{ funding_pool }} pool, {{ votes_cast }} votes). Review and send.",
            ),
            Channel.EMAIL: ChannelCopy(
                subject="Funding results ready to send — {{ cycle_label }}",
                body_text=(
                    "A funding snapshot for {{ cycle_label }} was taken (${{ funding_pool }} pool, "
                    "{{ votes_cast }} votes). Review the numbers and send results to members:\n\n"
                    "{{ review_url }}\n\nPast Lives Makerspace"
                ),
                body_html=(
                    "<p>A funding snapshot for {{ cycle_label }} was taken "
                    "(${{ funding_pool }} pool, {{ votes_cast }} votes).</p>"
                    '<p><a href="{{ review_url }}">Review the numbers and send results to members</a></p>'
                    "<p>Past Lives Makerspace</p>"
                ),
            ),
        },
    ),
    "release.published": EventCopy(
        placeholders=("member_name", "version", "release_title", "release_notes", "site_url"),
        sample_context={
            "member_name": "Robin Vale",
            "version": "0.19.9",
            "release_title": "No more double class-reminder emails",
            "release_notes": "• You now get a single class-reminder email instead of two.",
            "site_url": "https://pastlives.example/",
        },
        channels={
            Channel.IN_APP: ChannelCopy(
                subject="A new version is out: {{ release_title }}",
                body_text="Past Lives v{{ version }} — {{ release_title }}.",
            ),
            Channel.EMAIL: ChannelCopy(
                subject="What's new at Past Lives: {{ release_title }}",
                body_text=(
                    "Hi {{ member_name }},\n\n"
                    "We just released a new version of the Past Lives app (v{{ version }}): "
                    "{{ release_title }}.\n\n"
                    "{{ release_notes }}\n\n"
                    "Visit Past Lives: {{ site_url }}\n\nPast Lives Makerspace"
                ),
                body_html=(
                    "<p>Hi {{ member_name }},</p>"
                    "<p>We just released a new version of the Past Lives app "
                    "(v{{ version }}): <strong>{{ release_title }}</strong>.</p>"
                    "<pre>{{ release_notes }}</pre>"
                    '<p><a href="{{ site_url }}">Visit Past Lives</a></p>'
                    "<p>Past Lives Makerspace</p>"
                ),
            ),
        },
    ),
    "event.guild_published": EventCopy(
        placeholders=("guild_name", "event_title", "when", "location", "event_url"),
        sample_context={
            "guild_name": "Metal Guild",
            "event_title": "Forge Night",
            "when": "Sat, Jul 12 · 6:00 PM – 8:00 PM",
            "location": "Main Studio",
            "event_url": "https://pastlives.example/events/5/",
        },
        channels={
            Channel.IN_APP: ChannelCopy(
                subject="New {{ guild_name }} event: {{ event_title }}",
                body_text="{{ event_title }} — {{ when }}.",
            ),
            Channel.EMAIL: ChannelCopy(
                subject="New {{ guild_name }} event: {{ event_title }}",
                body_text=(
                    "{{ event_title }}\n{{ when }}\nWhere: {{ location }}\n\n"
                    "See the event details: {{ event_url }}\n\nPast Lives Makerspace"
                ),
                body_html=(
                    "<h2>{{ event_title }}</h2>"
                    "<p>{{ guild_name }} · {{ when }}</p>"
                    "<p>Where: {{ location }}</p>"
                    '<p><a href="{{ event_url }}">See the event details</a></p>'
                    "<p>Past Lives Makerspace</p>"
                ),
            ),
        },
    ),
    "event.community_published": EventCopy(
        placeholders=("event_title", "when", "location", "event_url"),
        sample_context={
            "event_title": "Monthly Potluck",
            "when": "Sat, Jul 12 · 6:00 PM – 8:00 PM",
            "location": "Common Area",
            "event_url": "https://pastlives.example/events/5/",
        },
        channels={
            Channel.IN_APP: ChannelCopy(
                subject="New community event: {{ event_title }}",
                body_text="{{ event_title }} — {{ when }}.",
            ),
            Channel.EMAIL: ChannelCopy(
                subject="New community event: {{ event_title }}",
                body_text=(
                    "{{ event_title }}\n{{ when }}\nWhere: {{ location }}\n\n"
                    "See the event details: {{ event_url }}\n\nPast Lives Makerspace"
                ),
                body_html=(
                    "<h2>{{ event_title }}</h2>"
                    "<p>{{ when }}</p>"
                    "<p>Where: {{ location }}</p>"
                    '<p><a href="{{ event_url }}">See the event details</a></p>'
                    "<p>Past Lives Makerspace</p>"
                ),
            ),
        },
    ),
    "event.lead_meeting_published": EventCopy(
        placeholders=("event_title", "when", "location", "event_url"),
        sample_context={
            "event_title": "Guild Lead Meeting",
            "when": "Sat, Jul 12 · 6:00 PM – 8:00 PM",
            "location": "Classroom",
            "event_url": "https://pastlives.example/events/5/",
        },
        channels={
            Channel.IN_APP: ChannelCopy(
                subject="Guild Lead Meeting: {{ event_title }}",
                body_text="{{ event_title }} — {{ when }}.",
            ),
            Channel.EMAIL: ChannelCopy(
                subject="Guild Lead Meeting: {{ event_title }}",
                body_text=(
                    "{{ event_title }}\n{{ when }}\nWhere: {{ location }}\n\n"
                    "See the event details: {{ event_url }}\n\nPast Lives Makerspace"
                ),
                body_html=(
                    "<h2>{{ event_title }}</h2>"
                    "<p>{{ when }}</p>"
                    "<p>Where: {{ location }}</p>"
                    '<p><a href="{{ event_url }}">See the event details</a></p>'
                    "<p>Past Lives Makerspace</p>"
                ),
            ),
        },
    ),
    "guild_announcement.submitted": EventCopy(
        placeholders=("guild_name", "announcement_title", "proposer_name", "review_url"),
        sample_context={
            "guild_name": "Metal Guild",
            "announcement_title": "New anvil this Saturday",
            "proposer_name": "Sam Rivera",
            "review_url": "https://pastlives.example/announcements/review/",
        },
        channels={
            Channel.IN_APP: ChannelCopy(
                subject="{{ proposer_name }} proposed a {{ guild_name }} announcement",
                body_text="“{{ announcement_title }}” is waiting for your review.",
            ),
            Channel.EMAIL: ChannelCopy(
                subject="Review a proposed {{ guild_name }} announcement",
                body_text=(
                    "{{ proposer_name }} proposed an announcement for {{ guild_name }}: "
                    "“{{ announcement_title }}”.\n\nReview it: {{ review_url }}\n\nPast Lives Makerspace"
                ),
                body_html=(
                    "<p><strong>{{ proposer_name }}</strong> proposed an announcement for "
                    "{{ guild_name }}: “{{ announcement_title }}”.</p>"
                    '<p><a href="{{ review_url }}">Review it</a></p>'
                    "<p>Past Lives Makerspace</p>"
                ),
            ),
        },
    ),
    "event.submitted": EventCopy(
        placeholders=("guild_name", "event_title", "when", "proposer_name", "review_url"),
        sample_context={
            "guild_name": "Metal Guild",
            "event_title": "Forge Night",
            "when": "Sat, Jul 12 · 6:00 PM – 8:00 PM",
            "proposer_name": "Robin Vale",
            "review_url": "https://pastlives.example/calendar/review/",
        },
        channels={
            Channel.IN_APP: ChannelCopy(
                subject="New event proposal: {{ event_title }}",
                body_text="{{ proposer_name }} proposed {{ event_title }} ({{ guild_name }}) for {{ when }}.",
            ),
            Channel.EMAIL: ChannelCopy(
                subject="New event proposal: {{ event_title }}",
                body_text=(
                    "{{ proposer_name }} proposed a Community Calendar event that needs a quick review.\n\n"
                    "{{ event_title }}\n{{ guild_name }} · {{ when }}\n\n"
                    "Review it: {{ review_url }}\n\nPast Lives Makerspace"
                ),
                body_html=(
                    "<p>{{ proposer_name }} proposed a Community Calendar event that needs a quick review.</p>"
                    '<p><strong><a href="{{ review_url }}">{{ event_title }}</a></strong><br>'
                    "{{ guild_name }} · {{ when }}</p>"
                    '<p><a href="{{ review_url }}">Review it in the queue</a></p>'
                    "<p>Past Lives Makerspace</p>"
                ),
            ),
        },
    ),
    "guild_announcement.approved": EventCopy(
        placeholders=("guild_name", "announcement_title", "guild_url"),
        sample_context={
            "guild_name": "Metal Guild",
            "announcement_title": "New anvil this Saturday",
            "guild_url": "https://pastlives.example/guilds/metal-guild/",
        },
        channels={
            Channel.IN_APP: ChannelCopy(
                subject="Your {{ guild_name }} announcement is posted",
                body_text="“{{ announcement_title }}” is now live on the guild page.",
            ),
            Channel.EMAIL: ChannelCopy(
                subject="Your {{ guild_name }} announcement is posted",
                body_text=(
                    "Your announcement “{{ announcement_title }}” was approved and is now posted "
                    "to {{ guild_name }}.\n\nSee it: {{ guild_url }}\n\nPast Lives Makerspace"
                ),
                body_html=(
                    "<p>Your announcement “<strong>{{ announcement_title }}</strong>” was approved and is "
                    "now posted to {{ guild_name }}.</p>"
                    '<p><a href="{{ guild_url }}">See it on the guild page</a></p>'
                    "<p>Past Lives Makerspace</p>"
                ),
            ),
        },
    ),
    # ``outcome`` is composed in Python (CommunityEvent._emit_decision) because the safe
    # renderer only substitutes {{ placeholders }} — it cannot branch. It reads "It's now
    # on the Community Calendar." for an immediate publish, or "It'll be announced and added
    # to the Community Calendar on <date>." when the approval was scheduled for later.
    "event.approved": EventCopy(
        placeholders=("event_title", "when", "event_url", "outcome"),
        sample_context={
            "event_title": "Forge Night",
            "when": "Sat, Jul 12 · 6:00 PM – 8:00 PM",
            "event_url": "https://pastlives.example/events/5/",
            "outcome": "It's now on the Community Calendar.",
        },
        channels={
            Channel.IN_APP: ChannelCopy(
                subject="Your event was approved: {{ event_title }}",
                body_text="{{ event_title }} — {{ when }}. {{ outcome }}",
            ),
            Channel.EMAIL: ChannelCopy(
                subject="Your event was approved: {{ event_title }}",
                body_text=(
                    "Good news — your proposed event was approved. {{ outcome }}\n\n"
                    "{{ event_title }}\n{{ when }}\n\n"
                    "See the event details: {{ event_url }}\n\nPast Lives Makerspace"
                ),
                body_html=(
                    "<p>Good news — your proposed event was approved. {{ outcome }}</p>"
                    '<p><strong><a href="{{ event_url }}">{{ event_title }}</a></strong><br>{{ when }}</p>'
                    '<p><a href="{{ event_url }}">See the event details</a></p>'
                    "<p>Past Lives Makerspace</p>"
                ),
            ),
        },
    ),
    "guild_announcement.changes_requested": EventCopy(
        placeholders=("guild_name", "announcement_title", "review_notes", "action_url"),
        sample_context={
            "guild_name": "Metal Guild",
            "announcement_title": "New anvil this Saturday",
            "review_notes": "Can you add the start time and where to meet?",
            "action_url": "https://pastlives.example/announcements/propose/7/edit/",
        },
        channels={
            Channel.IN_APP: ChannelCopy(
                subject="Changes requested on your {{ guild_name }} announcement",
                body_text="{{ review_notes }}",
            ),
            Channel.EMAIL: ChannelCopy(
                subject="Changes requested on your {{ guild_name }} announcement",
                body_text=(
                    "A reviewer asked for changes on “{{ announcement_title }}”:\n\n{{ review_notes }}\n\n"
                    "Edit and resubmit: {{ action_url }}\n\nPast Lives Makerspace"
                ),
                body_html=(
                    "<p>A reviewer asked for changes on “<strong>{{ announcement_title }}</strong>”:</p>"
                    "<p>{{ review_notes }}</p>"
                    '<p><a href="{{ action_url }}">Edit and resubmit</a></p>'
                    "<p>Past Lives Makerspace</p>"
                ),
            ),
        },
    ),
    "event.changes_requested": EventCopy(
        placeholders=("event_title", "reviewer_notes", "edit_url"),
        sample_context={
            "event_title": "Forge Night",
            "reviewer_notes": "Can you move it a bit later so it doesn't clash with open studio?",
            "edit_url": "https://pastlives.example/calendar/events/propose/5/edit/",
        },
        channels={
            Channel.IN_APP: ChannelCopy(
                subject="Changes requested: {{ event_title }}",
                body_text="A reviewer asked for changes to {{ event_title }}: {{ reviewer_notes }}",
            ),
            Channel.EMAIL: ChannelCopy(
                subject="Changes requested: {{ event_title }}",
                body_text=(
                    "A reviewer took a look at your proposed event, {{ event_title }}, and asked for a change "
                    "before it goes live:\n\n{{ reviewer_notes }}\n\n"
                    "Update and resubmit it: {{ edit_url }}\n\nPast Lives Makerspace"
                ),
                body_html=(
                    "<p>A reviewer took a look at your proposed event, "
                    '<strong><a href="{{ edit_url }}">{{ event_title }}</a></strong>, and asked for a change '
                    "before it goes live:</p>"
                    "<p>{{ reviewer_notes }}</p>"
                    '<p><a href="{{ edit_url }}">Update and resubmit your event</a></p>'
                    "<p>Past Lives Makerspace</p>"
                ),
            ),
        },
    ),
    "guild_announcement.declined": EventCopy(
        placeholders=("guild_name", "announcement_title", "review_notes"),
        sample_context={
            "guild_name": "Metal Guild",
            "announcement_title": "New anvil this Saturday",
            "review_notes": "We already announced this one — thanks though!",
        },
        channels={
            Channel.IN_APP: ChannelCopy(
                subject="Your {{ guild_name }} announcement wasn't posted",
                body_text="{{ review_notes }}",
            ),
            Channel.EMAIL: ChannelCopy(
                subject="About your {{ guild_name }} announcement",
                body_text=(
                    "Your proposed announcement “{{ announcement_title }}” wasn't posted.\n\n"
                    "{{ review_notes }}\n\nPast Lives Makerspace"
                ),
                body_html=(
                    "<p>Your proposed announcement “<strong>{{ announcement_title }}</strong>” wasn't posted.</p>"
                    "<p>{{ review_notes }}</p>"
                    "<p>Past Lives Makerspace</p>"
                ),
            ),
        },
    ),
    "event.declined": EventCopy(
        placeholders=("event_title", "reviewer_notes", "propose_url"),
        sample_context={
            "event_title": "Forge Night",
            "reviewer_notes": "We already have a similar event that week — thanks for suggesting it!",
            "propose_url": "https://pastlives.example/calendar/events/propose/",
        },
        channels={
            Channel.IN_APP: ChannelCopy(
                subject="Proposal not approved: {{ event_title }}",
                body_text="{{ event_title }} wasn't approved this time: {{ reviewer_notes }}",
            ),
            Channel.EMAIL: ChannelCopy(
                subject="About your event proposal: {{ event_title }}",
                body_text=(
                    "Thanks for proposing {{ event_title }}. After a look, a reviewer decided not to add it to "
                    "the calendar this time:\n\n{{ reviewer_notes }}\n\n"
                    "You're welcome to propose another event any time: {{ propose_url }}\n\nPast Lives Makerspace"
                ),
                body_html=(
                    "<p>Thanks for proposing <strong>{{ event_title }}</strong>. After a look, a reviewer decided "
                    "not to add it to the calendar this time:</p>"
                    "<p>{{ reviewer_notes }}</p>"
                    '<p><a href="{{ propose_url }}">Propose another event</a></p>'
                    "<p>Past Lives Makerspace</p>"
                ),
            ),
        },
    ),
    # event.reminder — 7/3/1-day-before nudge. In-app on; email + Discord OFF by default, but
    # the EMAIL copy is authored (Discord inherits it via copy_for) so a channel can flip on
    # later with no new copy. Guild name is deliberately omitted (the key serves both guild and
    # site-wide events, and the renderer can't hide an empty guild for the site-wide case).
    "event.reminder": EventCopy(
        placeholders=("event_title", "days_before", "when", "location", "event_url"),
        sample_context={
            "event_title": "Forge Night",
            "days_before": "3",
            "when": "Sat, Jul 12 · 6:00 PM – 8:00 PM",
            "location": "Main Studio",
            "event_url": "https://pastlives.example/events/5/",
        },
        channels={
            Channel.IN_APP: ChannelCopy(
                subject="Reminder: {{ event_title }} is {{ days_before }} day(s) away",
                body_text="{{ event_title }} is {{ days_before }} day(s) away — {{ when }}.",
            ),
            Channel.EMAIL: ChannelCopy(
                subject="Reminder: {{ event_title }} is {{ days_before }} day(s) away",
                body_text=(
                    "{{ event_title }} is coming up in {{ days_before }} day(s) — {{ when }}.\n"
                    "Where: {{ location }}\n\n"
                    "See the event details: {{ event_url }}\n\nPast Lives Makerspace"
                ),
                body_html=(
                    "<h2>{{ event_title }}</h2>"
                    "<p>Coming up in {{ days_before }} day(s) — {{ when }}</p>"
                    "<p>Where: {{ location }}</p>"
                    '<p><a href="{{ event_url }}">See the event details</a></p>'
                    "<p>Past Lives Makerspace</p>"
                ),
            ),
        },
    ),
    # event.happening_now — a single "starting now" ping. In-app on, email off, Discord ON.
    # Discord posts the EMAIL body (copy_for fallback), so it's authored lead-first (link early)
    # to read well as a one-line channel post AND as an email.
    "event.happening_now": EventCopy(
        placeholders=("event_title", "when", "location", "event_url"),
        sample_context={
            "event_title": "Forge Night",
            "when": "Sat, Jul 12 · 6:00 PM – 8:00 PM",
            "location": "Main Studio",
            "event_url": "https://pastlives.example/events/5/",
        },
        channels={
            Channel.IN_APP: ChannelCopy(
                subject="{{ event_title }} is starting now",
                body_text="{{ event_title }} is starting now — {{ when }}.",
            ),
            Channel.EMAIL: ChannelCopy(
                subject="{{ event_title }} is starting now",
                body_text=(
                    "{{ event_title }} is starting now — {{ when }}.\n"
                    "See the event details: {{ event_url }}\n"
                    "Where: {{ location }}\n\nPast Lives Makerspace"
                ),
                body_html=(
                    '<p><strong><a href="{{ event_url }}">{{ event_title }}</a></strong> is starting now — '
                    "{{ when }}.</p>"
                    "<p>Where: {{ location }}</p>"
                    '<p><a href="{{ event_url }}">See the event details</a></p>'
                    "<p>Past Lives Makerspace</p>"
                ),
            ),
        },
    ),
    # orientation.completed — a warm welcome to the guild's members when a newcomer finishes
    # their orientation. In-app + Discord only (no email). The Discord body carries the guild
    # URL as a plain link (an embed has no separate click target); the in-app row uses its
    # ``url`` field instead, so its body stays clean.
    "orientation.completed": EventCopy(
        placeholders=("member_name", "guild_name", "guild_url"),
        sample_context={
            "member_name": "Robin Vale",
            "guild_name": "Metal Guild",
            "guild_url": "https://pastlives.example/guilds/metal-guild/",
        },
        channels={
            Channel.IN_APP: ChannelCopy(
                subject="Welcome {{ member_name }} to {{ guild_name }}!",
                body_text=(
                    "{{ member_name }} just completed their orientation. "
                    "Say hello and give them a warm welcome to {{ guild_name }}."
                ),
            ),
            Channel.DISCORD: ChannelCopy(
                subject="Welcome {{ member_name }}!",
                body_text=(
                    "{{ member_name }} just completed their {{ guild_name }} orientation — "
                    "please give them a warm welcome! {{ guild_url }}"
                ),
            ),
        },
    ),
    # space.lease_requested — a studio ask landing with the admins. Staff-facing workflow
    # mail: the space code links to the map, the one CTA is the review queue, and the
    # member's own note is surfaced so a reviewer can decide without opening the app.
    "space.lease_requested": EventCopy(
        placeholders=("member_name", "space_code", "price_display", "requester_message", "review_url"),
        sample_context={
            "member_name": "Robin Vale",
            "space_code": "A9",
            "price_display": "$420.00/mo",
            "requester_message": "I'd love the corner light for pottery.",
            "review_url": "https://pastlives.example/info/requests/review/",
        },
        channels={
            Channel.IN_APP: ChannelCopy(
                subject="{{ member_name }} wants to lease {{ space_code }}",
                body_text="{{ member_name }} asked to lease {{ space_code }} ({{ price_display }}).",
            ),
            Channel.EMAIL: ChannelCopy(
                subject="{{ member_name }} wants to lease {{ space_code }}",
                body_text=(
                    "{{ member_name }} asked to lease a studio from the space map.\n\n"
                    "{{ space_code }} · {{ price_display }}\n\n"
                    'They wrote: "{{ requester_message }}"\n\n'
                    "Review the request: {{ review_url }}\n\n"
                    "Approving notifies the member — you still finalize the lease in Airtable.\n\n"
                    "Past Lives Makerspace"
                ),
                body_html=(
                    "<p>{{ member_name }} asked to lease a studio from the space map.</p>"
                    '<p><strong><a href="{{ review_url }}">{{ space_code }}</a></strong> · {{ price_display }}</p>'
                    "<p>They wrote: &ldquo;{{ requester_message }}&rdquo;</p>"
                    '<p><a href="{{ review_url }}">Review the request</a></p>'
                    "<p>Approving notifies the member — you still finalize the lease in Airtable.</p>"
                    "<p>Past Lives Makerspace</p>"
                ),
            ),
        },
    ),
    # space.cubby_requested — same shape as a lease ask, now routed to the makerspace admins
    # (the request flow was lightened). The member-facing word "cubby" is retired in favour of
    # "shelf" here too, since this copy is rendered to whoever reads the notification.
    "space.cubby_requested": EventCopy(
        placeholders=("member_name", "space_code", "price_display", "requester_message", "review_url"),
        sample_context={
            "member_name": "Robin Vale",
            "space_code": "C12",
            "price_display": "$25.00/mo",
            "requester_message": "For glaze storage between firings.",
            "review_url": "https://pastlives.example/info/requests/review/",
        },
        channels={
            Channel.IN_APP: ChannelCopy(
                subject="{{ member_name }} wants shelf {{ space_code }}",
                body_text="{{ member_name }} asked for shelf {{ space_code }} ({{ price_display }}).",
            ),
            Channel.EMAIL: ChannelCopy(
                subject="{{ member_name }} wants shelf {{ space_code }}",
                body_text=(
                    "{{ member_name }} asked for a shelf from the space map.\n\n"
                    "{{ space_code }} · {{ price_display }}\n\n"
                    'They wrote: "{{ requester_message }}"\n\n'
                    "Review the request: {{ review_url }}\n\nPast Lives Makerspace"
                ),
                body_html=(
                    "<p>{{ member_name }} asked for a shelf from the space map.</p>"
                    '<p><strong><a href="{{ review_url }}">{{ space_code }}</a></strong> · {{ price_display }}</p>'
                    "<p>They wrote: &ldquo;{{ requester_message }}&rdquo;</p>"
                    '<p><a href="{{ review_url }}">Review the request</a></p>'
                    "<p>Past Lives Makerspace</p>"
                ),
            ),
        },
    ),
    # space.request_approved — the member hears yes. Says plainly that a human finalizes the
    # paperwork, so nobody waits for an automatic lease that never arrives.
    "space.request_approved": EventCopy(
        placeholders=("space_code", "price_display", "audience_label", "space_url"),
        sample_context={
            "space_code": "A9",
            "price_display": "$420.00/mo",
            "audience_label": "the makerspace admins",
            "space_url": "https://pastlives.example/info/",
        },
        channels={
            Channel.IN_APP: ChannelCopy(
                subject="Your request for {{ space_code }} was approved",
                body_text="{{ space_code }} ({{ price_display }}) is yours to claim — {{ audience_label }} "
                "will be in touch to finalize it.",
            ),
            Channel.EMAIL: ChannelCopy(
                subject="Your request for {{ space_code }} was approved",
                body_text=(
                    "Good news — your request was approved.\n\n"
                    "{{ space_code }} · {{ price_display }}\n\n"
                    "{{ audience_label }} will be in touch to finalize the paperwork; nothing is "
                    "charged automatically.\n\n"
                    "See it on the map: {{ space_url }}\n\nPast Lives Makerspace"
                ),
                body_html=(
                    "<p>Good news — your request was approved.</p>"
                    '<p><strong><a href="{{ space_url }}">{{ space_code }}</a></strong> · {{ price_display }}</p>'
                    "<p>{{ audience_label }} will be in touch to finalize the paperwork; nothing is "
                    "charged automatically.</p>"
                    '<p><a href="{{ space_url }}">See it on the map</a></p>'
                    "<p>Past Lives Makerspace</p>"
                ),
            ),
        },
    ),
    # space.request_declined — the member hears no, carrying the reviewer's own words and a
    # way straight back to the map to look at what else is open.
    "space.request_declined": EventCopy(
        placeholders=("space_code", "reviewer_notes", "space_url"),
        sample_context={
            "space_code": "A9",
            "reviewer_notes": "That one is spoken for, but B4 opens up next month.",
            "space_url": "https://pastlives.example/info/",
        },
        channels={
            Channel.IN_APP: ChannelCopy(
                subject="Update on your {{ space_code }} request",
                body_text="{{ space_code }} isn't available for you right now: {{ reviewer_notes }}",
            ),
            Channel.EMAIL: ChannelCopy(
                subject="Update on your {{ space_code }} request",
                body_text=(
                    "Thanks for asking about {{ space_code }}. A reviewer wasn't able to say yes "
                    "this time:\n\n{{ reviewer_notes }}\n\n"
                    "You're welcome to ask about another space any time: {{ space_url }}\n\n"
                    "Past Lives Makerspace"
                ),
                body_html=(
                    "<p>Thanks for asking about <strong>{{ space_code }}</strong>. A reviewer wasn't able "
                    "to say yes this time:</p>"
                    "<p>{{ reviewer_notes }}</p>"
                    '<p><a href="{{ space_url }}">See what else is open on the map</a></p>'
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
