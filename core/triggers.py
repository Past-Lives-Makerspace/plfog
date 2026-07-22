"""The notification trigger catalogue — single source of truth.

Each Trigger describes one notifiable event: its stable key (stored in
Notification.trigger / NotificationPreference.trigger), display label and
description for the settings UI, category grouping, audience (who sees the
toggle), and defaults. `force_email` triggers always email and never show a
toggle (e.g. security new-login).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Audience(str, Enum):
    ALL_MEMBERS = "all_members"
    INSTRUCTORS_ONLY = "instructors_only"
    STAFF_ONLY = "staff_only"


@dataclass(frozen=True)
class Trigger:
    key: str
    label: str
    description: str
    category: str
    audience: Audience = Audience.ALL_MEMBERS
    force_email: bool = False
    push_default: bool = False
    email_default: bool = False


TRIGGERS: list[Trigger] = [
    # Classes — member-side
    Trigger("class_published", "New class published", "A new class or workshop goes live.", "Classes"),
    Trigger("class_reminder", "Class reminder", "24 hours before a session you're registered for.", "Classes"),
    Trigger("registration_confirmed", "Registration confirmed", "Your registration and payment cleared.", "Classes"),
    # Transactional: a cancelled class always emails the people who booked it (including
    # guests with no account), so it is forced rather than opt-in.
    Trigger(
        "class_cancelled",
        "Class cancelled",
        "A class you're registered for was cancelled.",
        "Classes",
        force_email=True,
    ),
    Trigger(
        "waitlist_spot_available", "Waitlist spot available", "A spot opened in a class you waitlisted.", "Classes"
    ),
    Trigger("waitlist_confirmed", "Added to waitlist", "You joined a class waitlist.", "Classes"),
    # Transactional: a refund is a money movement — the receipt always goes out.
    Trigger(
        "refund_issued",
        "Refund issued",
        "A refund was processed for a registration.",
        "Classes",
        force_email=True,
    ),
    # Classes — instructor-side
    Trigger(
        "instructor_class_approved",
        "Your class was approved",
        "A reviewer approved your class.",
        "Teaching",
        Audience.INSTRUCTORS_ONLY,
    ),
    Trigger(
        "instructor_changes_requested",
        "Changes requested",
        "A reviewer asked for edits.",
        "Teaching",
        Audience.INSTRUCTORS_ONLY,
    ),
    Trigger(
        "instructor_new_registration",
        "New registration",
        "Someone registered for your class.",
        "Teaching",
        Audience.INSTRUCTORS_ONLY,
    ),
    Trigger(
        "class_review_requested",
        "Class needs your review",
        "An instructor submitted a class in a guild you lead — review it.",
        "Teaching",
    ),
    Trigger(
        "class_validation_requested",
        "Class needs executive validation",
        "A guild lead approved a class; it needs admin sign-off to publish.",
        "Teaching",
        Audience.STAFF_ONLY,
    ),
    # Guild activity
    Trigger("guild_announcement", "Guild announcement", "A guild you're in posted an announcement.", "Guilds"),
    Trigger(
        "orientation_requested",
        "Orientation requested",
        "Someone requested an orientation for a guild you lead.",
        "Guilds",
    ),
    Trigger(
        "orientation_update",
        "Orientation updates",
        "Your orientation request was confirmed, declined, or cancelled.",
        "Guilds",
    ),
    Trigger(
        "guild_joined",
        "New guild member",
        "A new member joined a guild you lead.",
        "Guilds",
    ),
    # Billing / tab
    Trigger("tab_charged", "Tab charged", "Your monthly tab was charged.", "Billing"),
    Trigger("tab_charge_failed", "Tab charge failed", "A charge failed — update your payment method.", "Billing"),
    # Transactional: both concern money owed on the member's tab — a charge they did not
    # enter themselves, and the warning before the tab locks. Neither is opt-out-able.
    Trigger(
        "tab_entry_added",
        "Tab entry added",
        "An admin added a line item to your tab.",
        "Billing",
        force_email=True,
    ),
    Trigger(
        "tab_approaching_limit",
        "Tab approaching limit",
        "Your balance is near your tab limit.",
        "Billing",
        force_email=True,
    ),
    # Membership
    Trigger("invite_accepted", "Invite accepted", "Someone you invited has joined.", "Membership"),
    Trigger("new_member_joined", "New member joined", "A new member signed up.", "Membership", Audience.STAFF_ONLY),
    # Spaces / leases
    # Transactional: a tenant must hear that their lease is about to end.
    Trigger(
        "lease_expiring",
        "Lease expiring soon",
        "Your space lease ends within 30 days.",
        "Spaces",
        force_email=True,
    ),
    # Admin broadcasts
    Trigger("site_announcement", "Makerspace-wide announcement", "Staff posted a site-wide notice.", "Announcements"),
]

_BY_KEY = {t.key: t for t in TRIGGERS}

# Stable category display order for the settings UI.
CATEGORY_ORDER = ["Classes", "Teaching", "Voting", "Guilds", "Billing", "Membership", "Spaces", "Announcements"]


def get(key: str) -> Trigger:
    """Return the Trigger for a key. Raises KeyError if unknown."""
    return _BY_KEY[key]


def for_member(*, is_instructor: bool, is_staff: bool) -> list[Trigger]:
    """Triggers whose toggle should be shown to this member. Excludes forced triggers."""
    out: list[Trigger] = []
    for t in TRIGGERS:
        if t.force_email:
            continue
        if t.audience == Audience.INSTRUCTORS_ONLY and not is_instructor:
            continue
        if t.audience == Audience.STAFF_ONLY and not is_staff:
            continue
        out.append(t)
    return out


def by_category(*, is_instructor: bool, is_staff: bool) -> dict[str, list[Trigger]]:
    """for_member() grouped by category, in CATEGORY_ORDER."""
    visible = for_member(is_instructor=is_instructor, is_staff=is_staff)
    grouped: dict[str, list[Trigger]] = {}
    for cat in CATEGORY_ORDER:
        rows = [t for t in visible if t.category == cat]
        if rows:
            grouped[cat] = rows
    return grouped
