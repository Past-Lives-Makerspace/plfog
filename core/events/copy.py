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

from django.utils.safestring import mark_safe

from core.events.registry import Channel, EventType, Recipients, all_events, get_event

# Channels that carry authored copy; rows are seeded for these three. Discord copy is
# authored greeting-free (a broadcast embed has no recipient) — curated entries and the
# generated fallback each carry their own; a curated event without one still falls back
# to its email copy. Scheduled-email reuses the email copy.
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
    Recipients.CLASS_APPROVERS: "The CMS Administrators (holders only).",
    Recipients.GUILD_LEADERSHIP_OR_CLASS_APPROVERS: (
        "The guild's lead and staff; for a lead-less category, the CMS Administrators (holders only)."
    ),
    Recipients.SPACE_APPROVERS: "The Space & Cubby Administrators (holders only).",
    Recipients.EQUIPMENT_MANAGERS: (
        "Everyone who manages the equipment: its own managers, the owning guild's leadership, "
        "and the Equipment Administrators."
    ),
    Recipients.DISCOUNT_APPROVERS: "The Discount Code Administrators (holders only).",
    Recipients.EVENTS_APPROVERS: "The Calendar Administrators (holders only).",
    Recipients.GUILD_LEADERSHIP_OR_EVENTS_APPROVERS: (
        "The guild's lead and staff; for a site-wide or council proposal, the Calendar Administrators (holders only)."
    ),
    Recipients.BILLING_APPROVERS: "The Billing Administrators (holders only).",
    Recipients.GUILD_LEAD: "The guild's lead only.",
    Recipients.GUILD_MEMBERS: "Every active member of the guild.",
    Recipients.GUILD_ORIENTERS: "The guild's lead and everyone holding the orienter role.",
    Recipients.ORIENTATION_RUNNER: "The staffer who claimed/ran the orientation.",
    Recipients.REGISTRANT: "The member the event is about (the registrant).",
    Recipients.INSTRUCTOR: "The class's instructor.",
    Recipients.CLASS_ROSTER: "Everyone with a confirmed registration for the class.",
    Recipients.NEXT_WAITLISTED: "The next member in line on the waitlist.",
    Recipients.TAB_MEMBER: "The member whose billing tab this concerns.",
    Recipients.INVITER: "The person who sent the invitation.",
    Recipients.INVITEE: "The person being invited (addressed by email; no account yet).",
    Recipients.LEASE_TENANT: "The member holding the space agreement.",
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
        placeholders=("member_name", "class_title", "class_starts_at", "classes_url"),
        sample_context={
            "member_name": "Robin Vale",
            "class_title": "Intro to Lost-Wax Casting",
            "class_starts_at": "Saturday, July 12",
            "classes_url": "https://pastlives.example/classes/",
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
                    "Life happens... and due to rare and unfortunate circumstances, {{ class_title }} "
                    "on {{ class_starts_at }} has been cancelled. Any payment will be refunded in full, "
                    "and we're really sorry for any inconvenience.\n\n"
                    "We'd still love to see you in our space. Find another class: {{ classes_url }}\n\n"
                    "Past Lives Makerspace"
                ),
                body_html=(
                    "<p>Hi {{ member_name }},</p>"
                    "<p>Life happens... and due to rare and unfortunate circumstances, "
                    "<strong>{{ class_title }}</strong> on {{ class_starts_at }} has been cancelled. "
                    "Any payment will be refunded in full, and we're really sorry for any inconvenience.</p>"
                    "<p>We'd still love to see you in our space. Click below to find another class.</p>"
                    '<p style="text-align:center;margin:24px 0 8px;"><a href="{{ classes_url }}" '
                    'style="display:inline-block;padding:12px 28px;background-color:#EEB44B;color:#092E4C;'
                    'font-size:14px;font-weight:700;text-decoration:none;border-radius:6px;">'
                    "Find a Class</a></p>"
                    "<p>Past Lives Makerspace</p>"
                ),
            ),
        },
    ),
    "class_published": EventCopy(
        placeholders=("class_title", "class_url", "class_image_html"),
        sample_context={
            "class_title": "Intro to Lost-Wax Casting",
            "class_url": "https://pastlives.example/classes/intro-to-lost-wax-casting/",
            # App-built SafeString injected by the send path (the class's hero image, or
            # empty when it has none). Blank in the preview so no broken image shows.
            "class_image_html": "",
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
                    "{{ class_image_html }}"
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
                    "limit, your tab locks until it's paid down.\n\n"
                    "Pay down your tab: {{ tab_url }}\n\n"
                    "Past Lives Makerspace"
                ),
                body_html=(
                    "<p>Hi {{ member_name }},</p>"
                    "<p>Your tab balance is <strong>{{ balance }}</strong> of your {{ limit }} limit. "
                    "Once you reach the limit, your tab locks until it's paid down.</p>"
                    '<p><a href="{{ tab_url }}">Pay down your tab</a></p>'
                    "<p>Past Lives Makerspace</p>"
                ),
            ),
        },
    ),
    # Generalized to every refundable payment (class registrations now, orientation
    # bookings via the paid-orientations spec): ``item_title`` is a class title or
    # "Makerspace orientation", and ``registration_url`` carries the source's manage URL.
    "refund_issued": EventCopy(
        placeholders=("member_name", "item_title", "amount", "registration_url"),
        sample_context={
            "member_name": "Robin",
            "item_title": "Intro to Lost-Wax Casting",
            "amount": "$65.00",
            "registration_url": "https://pastlives.example/classes/my/abc123/",
        },
        channels={
            Channel.IN_APP: ChannelCopy(
                subject="Refund issued",
                body_text="Your {{ amount }} for {{ item_title }} has been refunded.",
            ),
            Channel.EMAIL: ChannelCopy(
                subject="Refund issued for {{ item_title }}",
                body_text=(
                    "Hi {{ member_name }},\n\n"
                    "We've refunded {{ amount }} for {{ item_title }}. Refunds typically process "
                    "within 5–10 business days.\n\n"
                    "View your booking: {{ registration_url }}\n\n"
                    "Past Lives Makerspace"
                ),
                body_html=(
                    "<p>Hi {{ member_name }},</p>"
                    "<p>We've refunded <strong>{{ amount }}</strong> for {{ item_title }}. Refunds "
                    "typically process within 5–10 business days.</p>"
                    '<p><a href="{{ registration_url }}">View your booking</a></p>'
                    "<p>Past Lives Makerspace</p>"
                ),
            ),
        },
    ),
    # Admin alert for an async refund failure. The payer may already hold a receipt
    # (receipts fire on the succeeded transition, and a late failure can follow it),
    # so the copy says to contact them after retrying.
    "refund_failed": EventCopy(
        placeholders=("payer_name", "item_title", "amount", "failure_reason", "admin_url"),
        sample_context={
            "payer_name": "Robin Vale",
            "item_title": "Intro to Lost-Wax Casting",
            "amount": "$65.00",
            "failure_reason": "The customer's bank could not process this refund.",
            "admin_url": "https://pastlives.example/classes/admin/registrations/42/",
        },
        channels={
            Channel.IN_APP: ChannelCopy(
                subject="A refund failed",
                body_text=(
                    "A {{ amount }} refund to {{ payer_name }} for {{ item_title }} failed. "
                    "Review and retry from the registration page."
                ),
            ),
            Channel.EMAIL: ChannelCopy(
                subject="A refund failed",
                body_text=(
                    "A refund did not go through and needs a retry.\n\n"
                    "Payer: {{ payer_name }}\n"
                    "Item: {{ item_title }}\n"
                    "Amount: {{ amount }}\n"
                    "Stripe's reason: {{ failure_reason }}\n\n"
                    "They've already received a refund receipt. Contact them after retrying.\n\n"
                    "Review and retry: {{ admin_url }}\n\n"
                    "Past Lives Makerspace"
                ),
                body_html=(
                    '<p>A <a href="{{ admin_url }}">refund</a> did not go through and needs a retry.</p>'
                    "<p>Payer: {{ payer_name }}<br>"
                    "Item: {{ item_title }}<br>"
                    "Amount: <strong>{{ amount }}</strong><br>"
                    "Stripe's reason: {{ failure_reason }}</p>"
                    "<p>They've already received a refund receipt. Contact them after retrying.</p>"
                    '<p><a href="{{ admin_url }}">Review and retry</a></p>'
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
                subject="Your space agreement is ending",
                body_text="Your space agreement for {{ space_name }} ends on {{ end_date }}.",
            ),
            Channel.EMAIL: ChannelCopy(
                subject="Your {{ space_name }} agreement ends {{ end_date }}",
                body_text=(
                    "Hi {{ member_name }},\n\n"
                    "Your space agreement for {{ space_name }} ends on {{ end_date }}, about a month from now.\n\n"
                    "If you'd like to renew, reply to this email.\n\n"
                    "Past Lives Makerspace"
                ),
                body_html=(
                    "<p>Hi {{ member_name }},</p>"
                    "<p>Your space agreement for <strong>{{ space_name }}</strong> ends on {{ end_date }}, "
                    "about a month from now.</p>"
                    "<p>If you'd like to renew, reply to this email.</p>"
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
            # Push is a single tray line: title + the bare announcement text, no email
            # footer/URL (which would otherwise leak in via the EMAIL fallback). The
            # PushAdapter flattens and caps this at send time.
            Channel.PUSH: ChannelCopy(
                subject="{{ guild_name }}: {{ announcement_title }}",
                body_text="{{ announcement_body }}",
            ),
            Channel.EMAIL: ChannelCopy(
                subject="{{ guild_name }}: {{ announcement_title }}",
                body_text=(
                    "{{ guild_name }} Announcement!\n\n{{ announcement_title }}\n\n{{ announcement_body }}\n\n"
                    "Visit the guild: {{ guild_url }}\n\nPast Lives Makerspace"
                ),
                body_html=(
                    "<h2>{{ guild_name }} Announcement!</h2>"
                    "<h3>{{ announcement_title }}</h3>"
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
            # Push is a single tray line: title + the bare announcement text, no email
            # footer/URL (which would otherwise leak in via the EMAIL fallback). The
            # PushAdapter flattens and caps this at send time.
            Channel.PUSH: ChannelCopy(
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
    "class_announcement": EventCopy(
        placeholders=("class_name", "announcement_title", "announcement_body", "class_url"),
        sample_context={
            "class_name": "Blacksmithing 101",
            "announcement_title": "This week moves to Thursday",
            "announcement_body": "Heads up: this week's session moves to Thursday at 6pm. Same room.",
            "class_url": "https://pastlives.example/classes/blacksmithing-101/",
        },
        channels={
            Channel.IN_APP: ChannelCopy(
                subject="{{ class_name }}: {{ announcement_title }}",
                body_text="{{ announcement_body }}",
            ),
            # Push is a single tray line: title + the bare announcement text, no email
            # footer/URL (which would otherwise leak in via the EMAIL fallback). The
            # PushAdapter flattens and caps this at send time.
            Channel.PUSH: ChannelCopy(
                subject="{{ class_name }}: {{ announcement_title }}",
                body_text="{{ announcement_body }}",
            ),
            Channel.EMAIL: ChannelCopy(
                subject="{{ class_name }}: {{ announcement_title }}",
                body_text=(
                    "{{ class_name }} Announcement!\n\n{{ announcement_title }}\n\n{{ announcement_body }}\n\n"
                    "View the class: {{ class_url }}\n\nPast Lives Makerspace"
                ),
                body_html=(
                    "<h2>{{ class_name }} Announcement!</h2>"
                    "<h3>{{ announcement_title }}</h3>"
                    "<p>{{ announcement_body }}</p>"
                    '<p><a href="{{ class_url }}">View {{ class_name }}</a></p>'
                    "<p>Past Lives Makerspace</p>"
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
                    "You've been invited to join the Past Lives Makerspace Member Portal.\n\n"
                    "Click the link below to create your account:\n\n"
                    "{{ signup_url }}"
                ),
                body_html=(
                    "<p>You've been invited to join the <strong>Past Lives Makerspace</strong> Member Portal.</p>"
                    '<div style="text-align:center; margin:24px 0 0;">'
                    '<a href="{{ signup_url }}" style="display:inline-block; padding:12px 32px; '
                    "background-color:#EEB44B; color:#092E4C; font-size:14px; font-weight:700; "
                    'text-decoration:none; border-radius:6px;">Create your account</a>'
                    "</div>"
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
                    "Your Past Lives Makerspace account is ready — now it's time to sign in.\n\n"
                    "Click the link below, and we'll email you a one-time login code:\n\n"
                    "{{ login_url }}\n\n"
                    "See you at the space!\nPast Lives Makerspace"
                ),
                body_html=(
                    "<p>Hi {{ member_name }},</p>"
                    "<p>Your <strong>Past Lives Makerspace</strong> account is ready — now it's "
                    "time to sign in.</p>"
                    "<p>Click below, and we'll email you a one-time login code.</p>"
                    '<div style="text-align:center; margin:24px 0 0;">'
                    '<a href="{{ login_url }}" style="display:inline-block; padding:12px 32px; '
                    "background-color:#EEB44B; color:#092E4C; font-size:14px; font-weight:700; "
                    'text-decoration:none; border-radius:6px;">Activate your account</a>'
                    "</div>"
                    '<p style="margin:16px 0 0; font-size:13px; color:#96ACBB;">'
                    "See you at the space! — Past Lives Makerspace</p>"
                ),
            ),
        },
    ),
    "voting.closing_soon": EventCopy(
        placeholders=(
            "member_name",
            "cycle_label",
            "closes_on",
            "vote_1st",
            "vote_2nd",
            "vote_3rd",
            "turnout_count",
            "pool_display",
            "voting_url",
        ),
        sample_context={
            "member_name": "Robin Vale",
            "cycle_label": "June 2026",
            "closes_on": "June 30, 2026",
            "vote_1st": "Metal Guild",
            "vote_2nd": "Fiber Guild",
            "vote_3rd": "Wood Guild",
            "turnout_count": "24",
            "pool_display": "$1,000",
            "voting_url": "https://pastlives.example/guilds/voting/",
        },
        channels={
            Channel.IN_APP: ChannelCopy(
                subject="Polls closing soon — {{ cycle_label }}",
                body_text="{{ cycle_label }} guild voting closes {{ closes_on }}. Your current vote: {{ vote_1st }}, {{ vote_2nd }}, {{ vote_3rd }}.",
            ),
            Channel.EMAIL: ChannelCopy(
                subject="Your {{ cycle_label }} guild vote is recorded",
                body_text=(
                    "Hi {{ member_name }},\n\n"
                    "The {{ cycle_label }} guild funding vote closes on {{ closes_on }}. Here's your ballot:\n\n"
                    "1st: {{ vote_1st }}\n"
                    "2nd: {{ vote_2nd }}\n"
                    "3rd: {{ vote_3rd }}\n\n"
                    "{{ turnout_count }} members have voted so far, and {{ pool_display }} goes to the guilds "
                    "when the month rolls over. Results will be shared in the first week of the month.\n\n"
                    "Change your vote any time before close: {{ voting_url }}\n\nPast Lives Makerspace"
                ),
                body_html=(
                    "<p>Hi {{ member_name }},</p>"
                    "<p>The {{ cycle_label }} guild funding vote closes on <strong>{{ closes_on }}</strong>. "
                    "Here's your ballot as it stands:</p>"
                    '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
                    'style="margin:0 0 20px;border:1px solid #e3e7ec;border-radius:10px;">'
                    '<tr><td style="padding:12px 16px;border-bottom:1px solid #e3e7ec;">'
                    '<span style="display:inline-block;min-width:34px;text-align:center;background-color:#EEB44B;color:#092E4C;font-size:11px;font-weight:700;padding:3px 8px;border-radius:10px;">1st</span>'
                    ' <strong style="color:#092E4C;">{{ vote_1st }}</strong></td></tr>'
                    '<tr><td style="padding:12px 16px;border-bottom:1px solid #e3e7ec;">'
                    '<span style="display:inline-block;min-width:34px;text-align:center;background-color:#B9C7D3;color:#092E4C;font-size:11px;font-weight:700;padding:3px 8px;border-radius:10px;">2nd</span>'
                    ' <strong style="color:#092E4C;">{{ vote_2nd }}</strong></td></tr>'
                    '<tr><td style="padding:12px 16px;">'
                    '<span style="display:inline-block;min-width:34px;text-align:center;background-color:#e3e7ec;color:#092E4C;font-size:11px;font-weight:700;padding:3px 8px;border-radius:10px;">3rd</span>'
                    ' <strong style="color:#092E4C;">{{ vote_3rd }}</strong></td></tr>'
                    "</table>"
                    "<p><strong>{{ turnout_count }}</strong> members have voted so far, and "
                    "<strong>{{ pool_display }}</strong> goes to the guilds when the month rolls over. "
                    "Results will be shared in the first week of the month.</p>"
                    '<p style="text-align:center;margin:24px 0 8px;"><a href="{{ voting_url }}" '
                    'style="display:inline-block;padding:12px 28px;background-color:#EEB44B;color:#092E4C;'
                    'font-size:14px;font-weight:700;text-decoration:none;border-radius:6px;">'
                    "Review or change your vote</a></p>"
                ),
            ),
        },
    ),
    "voting.vote_soon": EventCopy(
        placeholders=("member_name", "cycle_label", "closes_on", "turnout_count", "pool_display", "voting_url"),
        sample_context={
            "member_name": "Robin Vale",
            "cycle_label": "June 2026",
            "closes_on": "June 30, 2026",
            "turnout_count": "24",
            "pool_display": "$1,000",
            "voting_url": "https://pastlives.example/guilds/voting/",
        },
        channels={
            Channel.IN_APP: ChannelCopy(
                subject="Vote soon — {{ cycle_label }}",
                body_text="You haven't cast a {{ cycle_label }} guild funding vote yet — voting closes {{ closes_on }}.",
            ),
            Channel.EMAIL: ChannelCopy(
                subject="Your {{ cycle_label }} guild vote closes {{ closes_on }}",
                body_text=(
                    "Hi {{ member_name }},\n\n"
                    "You haven't cast a guild funding vote yet for {{ cycle_label }} — voting closes {{ closes_on }}. "
                    "It takes a minute, and it decides how {{ pool_display }} is split between the guilds. "
                    "{{ turnout_count }} members have voted so far.\n\n"
                    "Cast your vote: {{ voting_url }}\n\nPast Lives Makerspace"
                ),
                body_html=(
                    "<p>Hi {{ member_name }},</p>"
                    "<p>You haven't cast a guild funding vote yet for {{ cycle_label }} — voting closes "
                    "<strong>{{ closes_on }}</strong>. It takes a minute, and it decides how "
                    "<strong>{{ pool_display }}</strong> is split between the guilds. "
                    "<strong>{{ turnout_count }}</strong> members have voted so far.</p>"
                    '<p style="text-align:center;margin:24px 0 8px;"><a href="{{ voting_url }}" '
                    'style="display:inline-block;padding:12px 28px;background-color:#EEB44B;color:#092E4C;'
                    'font-size:14px;font-weight:700;text-decoration:none;border-radius:6px;">'
                    "Cast your vote</a></p>"
                ),
            ),
        },
    ),
    "voting.officers_closing_soon": EventCopy(
        placeholders=(
            "cycle_label",
            "closes_on",
            "turnout_count",
            "not_voted_count",
            "pool_display",
            "voting_url",
        ),
        sample_context={
            "cycle_label": "June 2026",
            "closes_on": "June 30, 2026",
            "turnout_count": "24",
            "not_voted_count": "9",
            "pool_display": "$1,000",
            "voting_url": "https://pastlives.example/guilds/voting/",
        },
        channels={
            Channel.IN_APP: ChannelCopy(
                subject="Officer heads-up — {{ cycle_label }} vote closes {{ closes_on }}",
                body_text="{{ turnout_count }} members have voted; {{ not_voted_count }} eligible members haven't yet.",
            ),
            Channel.EMAIL: ChannelCopy(
                subject="Officer heads-up: the {{ cycle_label }} guild vote closes {{ closes_on }}",
                body_text=(
                    "Hi there,\n\n"
                    "The {{ cycle_label }} guild funding vote closes on {{ closes_on }}.\n\n"
                    "Turnout so far: {{ turnout_count }} members have voted; {{ not_voted_count }} eligible "
                    "members haven't yet. {{ pool_display }} will be split by the final rankings.\n\n"
                    "A quick nudge in your guild channels goes a long way.\n\n"
                    "{{ voting_url }}\n\nPast Lives Makerspace"
                ),
                body_html=(
                    "<p>Hi there,</p>"
                    "<p>The {{ cycle_label }} guild funding vote closes on <strong>{{ closes_on }}</strong>.</p>"
                    "<p>Turnout so far: <strong>{{ turnout_count }}</strong> members have voted; "
                    "<strong>{{ not_voted_count }}</strong> eligible members haven't yet. "
                    "<strong>{{ pool_display }}</strong> will be split by the final rankings.</p>"
                    "<p>A quick nudge in your guild channels goes a long way.</p>"
                    '<p style="text-align:center;margin:24px 0 8px;"><a href="{{ voting_url }}" '
                    'style="display:inline-block;padding:12px 28px;background-color:#EEB44B;color:#092E4C;'
                    'font-size:14px;font-weight:700;text-decoration:none;border-radius:6px;">'
                    "See the voting page</a></p>"
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
            # allocation_summary: plain-text lines for the text body.
            "allocation_summary",
            # allocation_chart: app-built, email-safe HTML bar chart for the HTML body
            # (FundingSnapshot.allocation_chart_html) — a SafeString the renderer injects
            # unescaped. The text body still uses allocation_summary.
            "allocation_chart",
            # ballot_recap: pre-formatted sentence for voters ("You voted — 1st: X, 2nd: Y, 3rd: Z.")
            # Empty string for non-voters so the paragraph renders blank rather than showing
            # "1st: , 2nd: , 3rd: ." with missing values.
            "ballot_recap",
            # vote_1st/2nd/3rd kept for any existing DB-stored NotificationTemplate rows.
            "vote_1st",
            "vote_2nd",
            "vote_3rd",
            "voting_url",
        ),
        sample_context={
            "member_name": "Robin Vale",
            "intro_note": "",
            "cycle_label": "June 2026",
            "allocation_summary": "Metal Guild — $600.00 (45.0%)\nFiber Guild — $400.00 (30.0%)",
            # A two-bar sample so the live copy preview shows the real chart, not escaped
            # tags. The send path builds this from FundingSnapshot.allocation_chart_html.
            "allocation_chart": mark_safe(
                '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
                "style=\"border-collapse:collapse;margin:8px 0 20px;font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;\">"
                '<tr><td style="padding:0 0 6px;">'
                '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>'
                '<td align="left" style="color:#F4EFDD;font-size:14px;font-weight:600;padding:0 8px 5px 0;">🥇 Metal Guild</td>'
                '<td align="right" style="color:#EEB44B;font-size:14px;font-weight:700;padding:0 0 5px;white-space:nowrap;">$600.00 &middot; 45.0%</td>'
                "</tr></table>"
                '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#0a1929;border-radius:5px;">'
                '<tr><td width="100%" style="height:18px;font-size:0;background-color:#EEB44B;background-image:linear-gradient(90deg,#EEB44B,#d4a043);border-radius:5px;">&nbsp;</td></tr></table>'
                "</td></tr>"
                '<tr><td style="padding:0 0 6px;">'
                '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>'
                '<td align="left" style="color:#F4EFDD;font-size:14px;font-weight:600;padding:0 8px 5px 0;">🥈 Fiber Guild</td>'
                '<td align="right" style="color:#EEB44B;font-size:14px;font-weight:700;padding:0 0 5px;white-space:nowrap;">$400.00 &middot; 30.0%</td>'
                "</tr></table>"
                '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#0a1929;border-radius:5px;">'
                '<tr><td width="67%" style="height:18px;font-size:0;background-color:#EEB44B;background-image:linear-gradient(90deg,#EEB44B,#d4a043);border-radius:5px;">&nbsp;</td>'
                '<td style="height:18px;font-size:0;">&nbsp;</td></tr></table>'
                "</td></tr></table>"
            ),
            "ballot_recap": "You voted — 1st: Metal Guild, 2nd: Fiber Guild, 3rd: Wood Guild.",
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
                    "{{ ballot_recap }}\n\n"
                    "Full breakdown: {{ voting_url }}\n\nPast Lives Makerspace"
                ),
                body_html=(
                    "<p>Hi {{ member_name }},</p>"
                    "<p>{{ intro_note }}</p>"
                    "<p>The votes for {{ cycle_label }} are counted. Here's how the funding pool was split:</p>"
                    "{{ allocation_chart }}"
                    "<p>{{ ballot_recap }}</p>"
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
    "voting.discord_reminder": EventCopy(
        placeholders=("cycle_label", "closes_on", "turnout_count", "pool_display", "standings_text", "voting_url"),
        sample_context={
            "cycle_label": "June 2026",
            "closes_on": "June 30, 2026",
            "turnout_count": "18",
            "pool_display": "$1,000",
            "standings_text": "🥇 `████████░░` **Metal Guild** — 42 pts\n🥈 `█████░░░░░` **Fiber Guild** — 25 pts",
            "voting_url": "https://pastlives.example/guilds/voting/",
        },
        channels={
            Channel.DISCORD: ChannelCopy(
                subject="Guild funding — {{ cycle_label }}",
                body_text=(
                    "Polls close {{ closes_on }}. Cast or update your picks before then — every vote moves the needle.\n\n"
                    "{{ standings_text }}\n\n"
                    "{{ turnout_count }} vote(s) counted · estimated pool {{ pool_display }}\n\n"
                    "{{ voting_url }}"
                ),
            ),
        },
    ),
    "voting.results_discord": EventCopy(
        placeholders=("cycle_label", "allocation_summary", "voting_url"),
        sample_context={
            "cycle_label": "June 2026",
            "allocation_summary": "Metal Guild — $600.00 (45.0%)\nFiber Guild — $400.00 (30.0%)",
            "voting_url": "https://pastlives.example/guilds/voting/history/",
        },
        channels={
            Channel.DISCORD: ChannelCopy(
                subject="{{ cycle_label }} guild funding results",
                body_text=(
                    "The {{ cycle_label }} funding vote is closed. Here's how the pool was split:\n\n"
                    "{{ allocation_summary }}\n\n"
                    "Full breakdown: {{ voting_url }}"
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
            # Discord is a channel broadcast — no recipient to greet, so it gets its
            # own copy instead of inheriting the email greeting via the fallback.
            Channel.DISCORD: ChannelCopy(
                subject="What's new at Past Lives: {{ release_title }}",
                body_text=(
                    "We just released a new version of the Past Lives app (v{{ version }}): "
                    "{{ release_title }}.\n\n{{ release_notes }}\n\n{{ site_url }}"
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
                    "We've reviewed your proposed event, {{ event_title }}, and have a change request:\n\n"
                    "{{ reviewer_notes }}\n\n"
                    "Update and resubmit it: {{ edit_url }}\n\nPast Lives Makerspace"
                ),
                body_html=(
                    "<p>We've reviewed your proposed event, "
                    '<strong><a href="{{ edit_url }}">{{ event_title }}</a></strong>, and have a change '
                    "request:</p>"
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
                    "Thanks for proposing {{ event_title }}. We can't approve the event at this "
                    "time:\n\n{{ reviewer_notes }}\n\n"
                    "You're welcome to propose another event any time: {{ propose_url }}\n\nPast Lives Makerspace"
                ),
                body_html=(
                    "<p>Thanks for proposing <strong>{{ event_title }}</strong>. We can't approve the "
                    "event at this time:</p>"
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
    # join_line / join_cta are the linked meeting's "Join meeting" pieces (Meetings §6.6):
    # the constrained renderer has no conditionals, so both arrive PRE-BUILT from
    # membership.events._event_context and are empty strings when no meeting link exists —
    # the guarded CTA without an {% if %}.
    "event.reminder": EventCopy(
        placeholders=("event_title", "days_before", "when", "location", "event_url", "join_line", "join_cta"),
        sample_context={
            "event_title": "Forge Night",
            "days_before": "3",
            "when": "Sat, Jul 12 · 6:00 PM – 8:00 PM",
            "location": "Main Studio",
            "event_url": "https://pastlives.example/events/5/",
            "join_line": "Join the meeting: https://meet.example/abc-defg\n",
            "join_cta": mark_safe(  # trusted app-built markup, like the voting chart sample
                '<p style="text-align:center;margin:24px 0 8px;">'
                '<a href="https://meet.example/abc-defg" '
                'style="display:inline-block;padding:12px 28px;background-color:#EEB44B;color:#092E4C;'
                'font-size:14px;font-weight:700;text-decoration:none;border-radius:6px;">Join meeting</a></p>'
            ),
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
                    "{{ join_line }}"
                    "See the event details: {{ event_url }}\n\nPast Lives Makerspace"
                ),
                body_html=(
                    "<h2>{{ event_title }}</h2>"
                    "<p>Coming up in {{ days_before }} day(s) — {{ when }}</p>"
                    "<p>Where: {{ location }}</p>"
                    "{{ join_cta }}"
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
                subject="{{ member_name }} wants space {{ space_code }}",
                body_text="{{ member_name }} asked for space {{ space_code }} ({{ price_display }}).",
            ),
            Channel.EMAIL: ChannelCopy(
                subject="{{ member_name }} wants space {{ space_code }}",
                body_text=(
                    "{{ member_name }} asked for a studio space from the space map.\n\n"
                    "{{ space_code }} · {{ price_display }}\n\n"
                    'They wrote: "{{ requester_message }}"\n\n'
                    "Review the request: {{ review_url }}\n\n"
                    "Approving notifies the member. You still finalize the agreement in Airtable.\n\n"
                    "Past Lives Makerspace"
                ),
                body_html=(
                    "<p>{{ member_name }} asked for a studio space from the space map.</p>"
                    '<p><strong><a href="{{ review_url }}">{{ space_code }}</a></strong> · {{ price_display }}</p>'
                    "<p>They wrote: &ldquo;{{ requester_message }}&rdquo;</p>"
                    '<p><a href="{{ review_url }}">Review the request</a></p>'
                    "<p>Approving notifies the member. You still finalize the agreement in Airtable.</p>"
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
                    "We'll be in touch soon to finalize the paperwork.\n\n"
                    "See it on the map: {{ space_url }}\n\nPast Lives Makerspace"
                ),
                body_html=(
                    "<p>Good news — your request was approved.</p>"
                    '<p><strong><a href="{{ space_url }}">{{ space_code }}</a></strong> · {{ price_display }}</p>'
                    "<p>We'll be in touch soon to finalize the paperwork.</p>"
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
                    "Thanks for asking about {{ space_code }}. That space is not available at this "
                    "time:\n\n{{ reviewer_notes }}\n\n"
                    "Find a space: {{ space_url }}\n\n"
                    "Past Lives Makerspace"
                ),
                body_html=(
                    "<p>Thanks for asking about <strong>{{ space_code }}</strong>. That space is not "
                    "available at this time. Click below to find another space.</p>"
                    "<p>{{ reviewer_notes }}</p>"
                    '<p style="text-align:center;margin:24px 0 8px;"><a href="{{ space_url }}" '
                    'style="display:inline-block;padding:12px 28px;background-color:#EEB44B;color:#092E4C;'
                    'font-size:14px;font-weight:700;text-decoration:none;border-radius:6px;">'
                    "Find a Space</a></p>"
                    "<p>Past Lives Makerspace</p>"
                ),
            ),
        },
    ),
    # meeting.minutes_approved — a broadcast to the guild's members (Discord dual-routes to
    # the guild's own channel), so NO channel may address a single recipient. Before this
    # curated copy existed, the generic fallback's email greeting leaked "Hi [missing:
    # member_name]" into guild Discord channels.
    "meeting.minutes_approved": EventCopy(
        # meeting_title is Meeting.display_title, which already embeds the guild name
        # ("Metal Guild — Monthly Meeting") — so the bodies never repeat guild_name.
        placeholders=("guild_name", "meeting_title", "meeting_url"),
        sample_context={
            "guild_name": "Metal Guild",
            "meeting_title": "Metal Guild — Monthly Meeting",
            "meeting_url": "https://pastlives.example/guilds/3/meetings/12/",
        },
        channels={
            Channel.IN_APP: ChannelCopy(
                subject="Meeting minutes approved",
                body_text="The minutes for {{ meeting_title }} are approved and locked.",
            ),
            Channel.EMAIL: ChannelCopy(
                subject="{{ guild_name }} meeting minutes approved",
                body_text=(
                    "The minutes for {{ meeting_title }} are approved and locked.\n\n"
                    "Read them: {{ meeting_url }}\n\n"
                    "Past Lives Makerspace"
                ),
                body_html=(
                    "<p>The minutes for <strong>{{ meeting_title }}</strong> are approved and locked.</p>"
                    '<p><a href="{{ meeting_url }}">Read the minutes</a></p>'
                    "<p>Past Lives Makerspace</p>"
                ),
            ),
            Channel.DISCORD: ChannelCopy(
                subject="Meeting minutes approved",
                body_text="The minutes for {{ meeting_title }} are approved and locked.\n\n{{ meeting_url }}",
            ),
        },
    ),
    # meeting.council_minutes_approved — same broadcast posture for the cross-guild council
    # meeting (Discord posts centrally; recipients are all guild leads/staff/officers).
    "meeting.council_minutes_approved": EventCopy(
        placeholders=("meeting_title", "meeting_url"),
        sample_context={
            "meeting_title": "Council — Monthly Meeting",
            "meeting_url": "https://pastlives.example/meetings/15/",
        },
        channels={
            Channel.IN_APP: ChannelCopy(
                subject="Council minutes approved",
                body_text="The minutes for {{ meeting_title }} are approved and locked.",
            ),
            Channel.EMAIL: ChannelCopy(
                subject="Council minutes approved",
                body_text=(
                    "The minutes for {{ meeting_title }} are approved and locked.\n\n"
                    "Read them: {{ meeting_url }}\n\n"
                    "Past Lives Makerspace"
                ),
                body_html=(
                    "<p>The minutes for <strong>{{ meeting_title }}</strong> are approved and locked.</p>"
                    '<p><a href="{{ meeting_url }}">Read the minutes</a></p>'
                    "<p>Past Lives Makerspace</p>"
                ),
            ),
            Channel.DISCORD: ChannelCopy(
                subject="Council minutes approved",
                body_text="The minutes for {{ meeting_title }} are approved and locked.\n\n{{ meeting_url }}",
            ),
        },
    ),
    # equipment.reservation_confirmed — the member's booking receipt (forced email with the
    # calendar invite attached by the emit call). One primary CTA to the equipment page.
    "equipment.reservation_confirmed": EventCopy(
        placeholders=("member_name", "equipment_name", "reservation_when", "equipment_url"),
        sample_context={
            "member_name": "Robin Vale",
            "equipment_name": "CNC Router",
            "reservation_when": "Saturday, September 12, 2:00 PM to 4:00 PM",
            "equipment_url": "https://pastlives.example/equipment/cnc-router/",
        },
        channels={
            Channel.IN_APP: ChannelCopy(
                subject="Reservation confirmed",
                body_text="{{ equipment_name }}: {{ reservation_when }}. See you there.",
            ),
            Channel.EMAIL: ChannelCopy(
                subject="Reserved: {{ equipment_name }}, {{ reservation_when }}",
                body_text=(
                    "Hi {{ member_name }},\n\n"
                    "Your reservation is set.\n\n"
                    "{{ equipment_name }}\n{{ reservation_when }}\n\n"
                    "A calendar invite is attached. If your plans change, you can cancel "
                    "from the equipment page and the time opens up for someone else.\n\n"
                    "See your reservation: {{ equipment_url }}\n\nPast Lives Makerspace"
                ),
                body_html=(
                    "<p>Hi {{ member_name }},</p>"
                    "<p>Your reservation is set.</p>"
                    '<p><strong><a href="{{ equipment_url }}">{{ equipment_name }}</a></strong><br>'
                    "{{ reservation_when }}</p>"
                    "<p>A calendar invite is attached. If your plans change, you can cancel from the "
                    "equipment page and the time opens up for someone else.</p>"
                    '<p style="text-align:center;margin:24px 0 8px;"><a href="{{ equipment_url }}" '
                    'style="display:inline-block;padding:12px 28px;background-color:#EEB44B;color:#092E4C;'
                    'font-size:14px;font-weight:700;text-decoration:none;border-radius:6px;">'
                    "See Your Reservation</a></p>"
                    "<p>Past Lives Makerspace</p>"
                ),
            ),
        },
    ),
    # equipment.reservation_cancelled_by_manager — carries the manager's required reason and
    # sends the member straight back to pick a new time.
    "equipment.reservation_cancelled_by_manager": EventCopy(
        placeholders=("member_name", "equipment_name", "reservation_when", "cancel_reason", "equipment_url"),
        sample_context={
            "member_name": "Robin Vale",
            "equipment_name": "CNC Router",
            "reservation_when": "Saturday, September 12, 2:00 PM to 4:00 PM",
            "cancel_reason": "The router is down for repair. Back Tuesday.",
            "equipment_url": "https://pastlives.example/equipment/cnc-router/",
        },
        channels={
            Channel.IN_APP: ChannelCopy(
                subject="Your {{ equipment_name }} reservation was cancelled",
                body_text="A manager cancelled your {{ reservation_when }} reservation: {{ cancel_reason }}",
            ),
            Channel.EMAIL: ChannelCopy(
                subject="Your {{ equipment_name }} reservation was cancelled",
                body_text=(
                    "Hi {{ member_name }},\n\n"
                    "A manager cancelled your {{ equipment_name }} reservation for "
                    "{{ reservation_when }}.\n\n"
                    "Their note: {{ cancel_reason }}\n\n"
                    "Pick a new time: {{ equipment_url }}\n\nPast Lives Makerspace"
                ),
                body_html=(
                    "<p>Hi {{ member_name }},</p>"
                    '<p>A manager cancelled your <strong><a href="{{ equipment_url }}">{{ equipment_name }}'
                    "</a></strong> reservation for {{ reservation_when }}.</p>"
                    "<p>Their note: {{ cancel_reason }}</p>"
                    '<p style="text-align:center;margin:24px 0 8px;"><a href="{{ equipment_url }}" '
                    'style="display:inline-block;padding:12px 28px;background-color:#EEB44B;color:#092E4C;'
                    'font-size:14px;font-weight:700;text-decoration:none;border-radius:6px;">'
                    "Pick a New Time</a></p>"
                    "<p>Past Lives Makerspace</p>"
                ),
            ),
        },
    ),
    # equipment.reservation_made — the managers' awareness ping. member_name here is the
    # RESERVER, not the recipient, so no channel greets with it (a "Hi {{ member_name }}"
    # would greet the wrong person).
    "equipment.reservation_made": EventCopy(
        placeholders=("member_name", "equipment_name", "reservation_when", "equipment_url"),
        sample_context={
            "member_name": "Robin Vale",
            "equipment_name": "CNC Router",
            "reservation_when": "Saturday, September 12, 2:00 PM to 4:00 PM",
            "equipment_url": "https://pastlives.example/equipment/cnc-router/",
        },
        channels={
            Channel.IN_APP: ChannelCopy(
                subject="New reservation on {{ equipment_name }}",
                body_text="{{ member_name }} reserved {{ equipment_name }} for {{ reservation_when }}.",
            ),
            Channel.EMAIL: ChannelCopy(
                subject="New reservation on {{ equipment_name }}",
                body_text=(
                    "{{ member_name }} reserved {{ equipment_name }} for {{ reservation_when }}.\n\n"
                    "See the schedule: {{ equipment_url }}\n\nPast Lives Makerspace"
                ),
                body_html=(
                    '<p>{{ member_name }} reserved <strong><a href="{{ equipment_url }}">{{ equipment_name }}'
                    "</a></strong> for {{ reservation_when }}.</p>"
                    '<p><a href="{{ equipment_url }}">See the schedule</a></p>'
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

    Discord gets its OWN greeting-free copy (label + description). Discord is a
    broadcast channel — there is no recipient to greet — so it must never
    inherit the email greeting via the ``copy_for`` fallback (that once posted
    "Hi [missing: member_name]" into a guild's channel).
    """
    placeholders = ("member_name",)
    sample = {"member_name": "Robin Vale"}
    in_app = ChannelCopy(subject=event.label, body_text=event.description)
    email = ChannelCopy(
        subject=event.label,
        body_text=f"Hi {{{{ member_name }}}},\n\n{event.description}\n\nPast Lives Makerspace",
        body_html=f"<p>Hi {{{{ member_name }}}},</p><p>{event.description}</p><p>Past Lives Makerspace</p>",
    )
    discord = ChannelCopy(subject=event.label, body_text=event.description)
    return EventCopy(
        placeholders=placeholders,
        sample_context=sample,
        channels={Channel.IN_APP: in_app, Channel.EMAIL: email, Channel.DISCORD: discord},
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
