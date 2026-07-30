"""The registry of every email the app can send — the gallery's source of truth.

Two families feed the copy-review gallery:

1. **Notification-spine events** (family 1) — derived from ``core.events.registry``,
   not hand-listed, so the long tail is automatic. An event whose email actually
   ships as a *structural template* (via ``emit_with_email_shell`` / a per-channel
   EMAIL override) is deduped into its structural entry — the DB copy for those
   keys is only the in-app/Discord body and publishing it would show copy that
   never ships. The dedup check runs BEFORE the EMAIL-off classification:
   ``tab_charge_failed`` has member EMAIL default OFF yet its email ships to
   admins via the ``charge_failed_admin`` template.
2. **Structural template pairs + specials** (family 2) — :data:`STRUCTURAL_EMAILS`,
   a hand-authored list covering every ``.txt``/``.html`` template pair, the two
   special renderers (release email, announcement composer), the allauth trio, and
   the inline-string ``find_account`` email.

Two completeness guards (enforced by the NON-gated spec in
``tests/core/email_gallery_completeness_spec.py``, so normal CI fails on an
unregistered new email):

* **Guard 1** — :func:`discover_template_pairs` (filesystem scan of the email
  template dirs) must be a subset of :func:`registered_template_pairs`.
* **Guard 2** — :func:`discover_inline_sends` (an AST lint over every direct
  ``core.email.send`` call site) must only find ``trigger_kind`` values that are
  registered or explicitly allowlisted with a reason.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from core.events.copy import audience_description
from core.events.registry import Channel, ChannelDefault, EventType, all_events

REPO_ROOT = Path(__file__).resolve().parents[3]


class Renderer(str, Enum):
    """How a gallery entry's email is rendered."""

    SPINE_COPY = "spine_copy"  # resolved_copy → render_copy → wrap_email_html (the emit() path)
    SHELL_TEMPLATE = "shell_template"  # email_shell_message over a .txt/.html pair (the send path)
    WELCOME = "welcome"  # classes.emails._welcome_email_bodies (instructor-authored)
    RELEASE = "release"  # core.release_email.render_release_email
    ANNOUNCEMENT = "announcement"  # membership.models.build_announcement_email_html
    ALLAUTH = "allauth"  # adapter.render_mail over account/email/<prefix>_*
    INLINE_STRING = "inline_string"  # f-string body reproduced from the send site (text-only)


@dataclass(frozen=True)
class GalleryEmail:
    """One email card in the gallery.

    Args:
        key: Stable id (event key or template basename) — the card anchor + dedup key.
        name: Human title shown on the card.
        section: One of :data:`SECTIONS` (sidebar group).
        renderer: Which render path builds this card.
        trigger_note: Plain-language "Sent when… Goes to…".
        edit_pointer: Where a reviewer edits this copy (Site Settings vs template path).
        audience: Who receives it (hand-written for structural; derived for spine).
        event_keys: ALL spine event keys whose EMAIL this entry renders (dedup; may be >1).
        text_template / html_template: The structural shell templates (SHELL_TEMPLATE).
        template_prefix: The allauth prefix (``account/email/<prefix>``).
        context_builder: Name of the builder callable in ``context.py``.
    """

    key: str
    name: str
    section: str
    renderer: Renderer
    trigger_note: str
    edit_pointer: str
    audience: str = ""
    event_keys: frozenset[str] = field(default_factory=frozenset)
    text_template: str | None = None
    html_template: str | None = None
    template_prefix: str | None = None
    context_builder: str | None = None


# Sidebar order. The closing "No email is sent" note-section is appended after these.
SECTIONS: list[str] = [
    "Classes",
    "Teaching",
    "Guilds & Orientations",
    "Billing",
    "Membership & Account",
    "Voting",
    "Events",
    "Announcements & Release",
    "System/Auth",
]

NO_EMAIL_SECTION = "No email is sent"

# Registry category → gallery section, for the derived spine cards.
_CATEGORY_SECTIONS: dict[str, str] = {
    "Classes": "Classes",
    "Teaching": "Teaching",
    "Guilds": "Guilds & Orientations",
    "Orientations": "Guilds & Orientations",
    "Billing": "Billing",
    "Membership": "Membership & Account",
    "Spaces": "Membership & Account",
    "Voting": "Voting",
    "Events": "Events",
    "Announcements": "Announcements & Release",
}


def _tpl(app_dir: str, base: str) -> str:
    return f"Template in code (templates/{app_dir}/{base}.{{txt,html}})"


STRUCTURAL_EMAILS: list[GalleryEmail] = [
    # --- Classes ---------------------------------------------------------------
    GalleryEmail(
        key="confirmation",
        name="Registration confirmed",
        section="Classes",
        renderer=Renderer.SHELL_TEMPLATE,
        trigger_note=(
            "Sent when a class registration is confirmed — on payment success for a paid class, "
            "immediately on submit for a free one. Goes to the registrant's email address."
        ),
        edit_pointer=_tpl("classes/emails", "confirmation"),
        audience="The registrant (member or guest).",
        event_keys=frozenset({"registration_confirmed"}),
        text_template="classes/emails/confirmation.txt",
        html_template="classes/emails/confirmation.html",
        context_builder="confirmation_context",
    ),
    GalleryEmail(
        key="welcome",
        name="Class welcome (instructor-authored)",
        section="Classes",
        renderer=Renderer.WELCOME,
        trigger_note=(
            "Sent right after the confirmation when the instructor has written and enabled a welcome "
            "email for their class. The subject and body are the instructor's own words; the shell and "
            "session details around them are the app's. Goes to each newly confirmed registrant."
        ),
        edit_pointer=(
            "Instructor-authored per class (Teach → class → Emails); "
            "shell in templates/classes/emails/welcome.{txt,html}"
        ),
        audience="Each newly confirmed registrant of that class.",
        context_builder="welcome_context",
    ),
    GalleryEmail(
        key="waitlist_joined",
        name="Added to the waitlist",
        section="Classes",
        renderer=Renderer.SHELL_TEMPLATE,
        trigger_note=(
            "Sent when someone joins a sold-out class's waitlist. Tells them their position and what "
            "happens if a spot opens. Goes to the registrant's email address."
        ),
        edit_pointer=_tpl("classes/emails", "waitlist_joined"),
        audience="The waitlisted registrant.",
        event_keys=frozenset({"waitlist_confirmed"}),
        text_template="classes/emails/waitlist_joined.txt",
        html_template="classes/emails/waitlist_joined.html",
        context_builder="waitlist_joined_context",
    ),
    GalleryEmail(
        key="waitlist_spot_opened",
        name="A waitlist spot opened",
        section="Classes",
        renderer=Renderer.SHELL_TEMPLATE,
        trigger_note=(
            "Sent when a confirmed registration cancels or refunds and the next waitlisted person is "
            "promoted. Carries the claim link and the claim window. Goes to the next member in line."
        ),
        edit_pointer=_tpl("classes/emails", "waitlist_spot_opened"),
        audience="The next member in line on the waitlist.",
        event_keys=frozenset({"waitlist_spot_available"}),
        text_template="classes/emails/waitlist_spot_opened.txt",
        html_template="classes/emails/waitlist_spot_opened.html",
        context_builder="waitlist_spot_opened_context",
    ),
    GalleryEmail(
        key="reminder",
        name="Class reminder",
        section="Classes",
        renderer=Renderer.SHELL_TEMPLATE,
        trigger_note=(
            "Sent 24 hours before each session a person is registered for. Goes to the registrant's email address."
        ),
        edit_pointer=_tpl("classes/emails", "reminder"),
        audience="The registrant, per upcoming session.",
        event_keys=frozenset({"class_reminder"}),
        text_template="classes/emails/reminder.txt",
        html_template="classes/emails/reminder.html",
        context_builder="reminder_context",
    ),
    # --- Teaching --------------------------------------------------------------
    GalleryEmail(
        key="instructor_new_registration",
        name="New registration (instructor notice)",
        section="Teaching",
        renderer=Renderer.SHELL_TEMPLATE,
        trigger_note=(
            "Sent when someone registers for a class. Goes to the class's instructor. Admins get a "
            "CC variant of the same notice (same template, admin audience)."
        ),
        edit_pointer=_tpl("classes/emails", "instructor_new_registration"),
        audience="The class's instructor (admins receive a CC variant).",
        event_keys=frozenset({"instructor_new_registration"}),
        text_template="classes/emails/instructor_new_registration.txt",
        html_template="classes/emails/instructor_new_registration.html",
        context_builder="instructor_new_registration_context",
    ),
    GalleryEmail(
        key="review_request",
        name="Class review request (reviewer)",
        section="Teaching",
        renderer=Renderer.SHELL_TEMPLATE,
        trigger_note=(
            "Sent when an instructor submits a class for review. Goes to the category's guild lead and "
            "staff (or to admins when the category has no guild lead), with a tokenized review link."
        ),
        edit_pointer=_tpl("classes/emails", "review_request"),
        audience="The guild's lead + staff, or admins for lead-less categories.",
        event_keys=frozenset({"class_review_requested"}),
        text_template="classes/emails/review_request.txt",
        html_template="classes/emails/review_request.html",
        context_builder="review_request_context",
    ),
    GalleryEmail(
        key="review_submitted_instructor",
        name="Your class is in review (instructor explainer)",
        section="Teaching",
        renderer=Renderer.SHELL_TEMPLATE,
        trigger_note=(
            "Sent when an instructor submits a class for review — explains what happens next. "
            "Goes to the submitting instructor."
        ),
        edit_pointer=_tpl("classes/emails", "review_submitted_instructor"),
        audience="The submitting instructor.",
        event_keys=frozenset({"class_review_requested"}),
        text_template="classes/emails/review_submitted_instructor.txt",
        html_template="classes/emails/review_submitted_instructor.html",
        context_builder="review_submitted_instructor_context",
    ),
    GalleryEmail(
        key="admin_validation_request",
        name="Executive validation request",
        section="Teaching",
        renderer=Renderer.SHELL_TEMPLATE,
        trigger_note=(
            "Sent when a guild lead approves a class and the admin (executive) gate opens. "
            "Goes to all FOG admins, with a tokenized review link."
        ),
        edit_pointer=_tpl("classes/emails", "admin_validation_request"),
        audience="All FOG admins.",
        event_keys=frozenset({"class_validation_requested"}),
        text_template="classes/emails/admin_validation_request.txt",
        html_template="classes/emails/admin_validation_request.html",
        context_builder="admin_validation_request_context",
    ),
    GalleryEmail(
        key="review_decision",
        name="Review decision (instructor)",
        section="Teaching",
        renderer=Renderer.SHELL_TEMPLATE,
        trigger_note=(
            "Sent when any reviewer decides on a submitted class — approved, live, changes requested, "
            "or declined (the subject line varies by outcome; the gallery shows the fully-approved "
            "outcome). Goes to the class's instructor."
        ),
        edit_pointer=_tpl("classes/emails", "review_decision"),
        audience="The class's instructor.",
        event_keys=frozenset({"instructor_class_approved", "instructor_changes_requested"}),
        text_template="classes/emails/review_decision.txt",
        html_template="classes/emails/review_decision.html",
        context_builder="review_decision_context",
    ),
    # --- Guilds & Orientations -------------------------------------------------
    GalleryEmail(
        key="orientation_request",
        name="Orientation request received",
        section="Guilds & Orientations",
        renderer=Renderer.SHELL_TEMPLATE,
        trigger_note=(
            "Sent when a member books a guild orientation slot — confirms the request landed and "
            "carries a tentative calendar invite. Goes to the requesting member."
        ),
        edit_pointer=_tpl("membership/emails", "orientation_request"),
        audience="The member who requested the orientation.",
        event_keys=frozenset({"orientation_update"}),
        text_template="membership/emails/orientation_request.txt",
        html_template="membership/emails/orientation_request.html",
        context_builder="orientation_member_context",
    ),
    GalleryEmail(
        key="orientation_confirmed",
        name="Orientation confirmed",
        section="Guilds & Orientations",
        renderer=Renderer.SHELL_TEMPLATE,
        trigger_note=(
            "Sent when an orienter confirms the member's orientation request — carries a confirmed "
            "calendar invite. Goes to the requesting member."
        ),
        edit_pointer=_tpl("membership/emails", "orientation_confirmed"),
        audience="The member whose orientation was confirmed.",
        event_keys=frozenset({"orientation_update"}),
        text_template="membership/emails/orientation_confirmed.txt",
        html_template="membership/emails/orientation_confirmed.html",
        context_builder="orientation_member_context",
    ),
    GalleryEmail(
        key="orientation_declined",
        name="Orientation not confirmed",
        section="Guilds & Orientations",
        renderer=Renderer.SHELL_TEMPLATE,
        trigger_note=(
            "Sent when an orienter declines the member's orientation request. Goes to the requesting member."
        ),
        edit_pointer=_tpl("membership/emails", "orientation_declined"),
        audience="The member whose request was declined.",
        event_keys=frozenset({"orientation_update"}),
        text_template="membership/emails/orientation_declined.txt",
        html_template="membership/emails/orientation_declined.html",
        context_builder="orientation_member_context",
    ),
    GalleryEmail(
        key="orientation_cancelled",
        name="Orientation cancelled",
        section="Guilds & Orientations",
        renderer=Renderer.SHELL_TEMPLATE,
        trigger_note=(
            "Sent when a booked orientation is cancelled (by the member, the guild, or a cancelled "
            "slot) — retracts the calendar invite. Goes to the booked member."
        ),
        edit_pointer=_tpl("membership/emails", "orientation_cancelled"),
        audience="The member whose orientation was cancelled.",
        event_keys=frozenset({"orientation_update"}),
        text_template="membership/emails/orientation_cancelled.txt",
        html_template="membership/emails/orientation_cancelled.html",
        context_builder="orientation_member_context",
    ),
    GalleryEmail(
        key="orientation_thankyou",
        name="Orientation thank-you (guild-authored)",
        section="Guilds & Orientations",
        renderer=Renderer.SHELL_TEMPLATE,
        trigger_note=(
            "Sent when an orientation is marked complete and the guild has written and enabled a "
            "thank-you email. The subject and body are the guild lead's own words. Goes to the "
            "member who completed the orientation."
        ),
        edit_pointer=(
            "Guild-authored (guild page → Orientations settings); "
            "shell in templates/membership/emails/orientation_thankyou.{txt,html}"
        ),
        audience="The member who completed the orientation.",
        event_keys=frozenset({"orientation_update"}),
        text_template="membership/emails/orientation_thankyou.txt",
        html_template="membership/emails/orientation_thankyou.html",
        context_builder="orientation_thankyou_context",
    ),
    GalleryEmail(
        key="orientation_lead_request",
        name="New orientation request (orienters)",
        section="Guilds & Orientations",
        renderer=Renderer.SHELL_TEMPLATE,
        trigger_note=(
            "Sent when a member requests a guild orientation. Goes to the guild's lead and staff, "
            "with one-click confirm / decline links."
        ),
        edit_pointer=_tpl("membership/emails", "orientation_lead_request"),
        audience="The guild's lead and all of its staff.",
        event_keys=frozenset({"orientation_requested"}),
        text_template="membership/emails/orientation_lead_request.txt",
        html_template="membership/emails/orientation_lead_request.html",
        context_builder="orientation_lead_request_context",
    ),
    GalleryEmail(
        key="guild_welcome",
        name="Guild welcome (guild-authored)",
        section="Guilds & Orientations",
        renderer=Renderer.SHELL_TEMPLATE,
        trigger_note=(
            "Sent when a member joins a guild that has written and enabled a join welcome email. "
            "The subject and body are the guild lead's own words. Goes to the joining member."
        ),
        edit_pointer=(
            "Guild-authored (guild page → Orientations settings); "
            "shell in templates/membership/emails/guild_welcome.{txt,html}"
        ),
        audience="The member who just joined the guild.",
        event_keys=frozenset({"guild_joined"}),
        text_template="membership/emails/guild_welcome.txt",
        html_template="membership/emails/guild_welcome.html",
        context_builder="guild_welcome_context",
    ),
    GalleryEmail(
        key="discord_guilds_imported",
        name="Your Past Lives guilds are set up (Discord link)",
        section="Guilds & Orientations",
        renderer=Renderer.SHELL_TEMPLATE,
        trigger_note=(
            "Sent once, right after a member links their Discord account and their guilds are "
            "imported. Goes to the member who linked."
        ),
        edit_pointer=_tpl("membership/emails", "discord_guilds_imported"),
        audience="The member who just linked Discord.",
        event_keys=frozenset({"discord_guilds_imported"}),
        text_template="membership/emails/discord_guilds_imported.txt",
        html_template="membership/emails/discord_guilds_imported.html",
        context_builder="discord_guilds_imported_context",
    ),
    # --- Billing ---------------------------------------------------------------
    GalleryEmail(
        key="receipt",
        name="Tab receipt",
        section="Billing",
        renderer=Renderer.SHELL_TEMPLATE,
        trigger_note=(
            "Sent after a member's tab is successfully charged — an itemized receipt. Goes to the charged member."
        ),
        edit_pointer=_tpl("billing/email", "receipt"),
        audience="The member whose tab was charged.",
        event_keys=frozenset({"tab_charged"}),
        text_template="billing/email/receipt.txt",
        html_template="billing/email/receipt.html",
        context_builder="receipt_context",
    ),
    GalleryEmail(
        key="charge_failed_admin",
        name="Tab charge failed (admin notice)",
        section="Billing",
        renderer=Renderer.SHELL_TEMPLATE,
        trigger_note=(
            "Sent when a member's tab charge fails. Goes to all FOG admins (the member gets an "
            "in-app notification only — their email for this event is opt-in)."
        ),
        edit_pointer=_tpl("billing/email", "charge_failed_admin"),
        audience="All FOG admins.",
        event_keys=frozenset({"tab_charge_failed"}),
        text_template="billing/email/charge_failed_admin.txt",
        html_template="billing/email/charge_failed_admin.html",
        context_builder="charge_failed_admin_context",
    ),
    # --- Announcements & Release (special renderers) ---------------------------
    GalleryEmail(
        key="release_update",
        name="Release update (What's new)",
        section="Announcements & Release",
        renderer=Renderer.RELEASE,
        trigger_note=(
            "Sent when an admin composes and sends a release update from Site Settings → "
            "Announcements (Draft from latest release). Goes to every activated member "
            "(members who have signed in at least once)."
        ),
        edit_pointer=(
            "Composed in Site Settings → Announcements; renderer core/release_email.py + "
            "templates/membership/emails/_release_shell.html"
        ),
        audience="Every activated member (has signed in at least once).",
        event_keys=frozenset({"site_announcement"}),
        context_builder="release_context",
    ),
    GalleryEmail(
        key="announcement",
        name="Site / guild announcement (composer)",
        section="Announcements & Release",
        renderer=Renderer.ANNOUNCEMENT,
        trigger_note=(
            "Sent when staff compose an announcement in the wizard and choose to also email it. "
            "Goes to every activated member (site-wide) or the guild's joined members (guild)."
        ),
        edit_pointer=(
            "Composed in the announcement wizard; shell in membership/models.py::build_announcement_email_html"
        ),
        audience="Every activated member, or one guild's joined members.",
        event_keys=frozenset({"site_announcement"}),
        context_builder="announcement_context",
    ),
    # --- System/Auth -----------------------------------------------------------
    GalleryEmail(
        key="login_code",
        name="Login code",
        section="System/Auth",
        renderer=Renderer.ALLAUTH,
        trigger_note=(
            "Sent whenever someone requests a sign-in code on the login page. Goes to the address they entered."
        ),
        edit_pointer="Template in code (templates/account/email/login_code_*.{txt,html})",
        audience="The person signing in.",
        template_prefix="login_code",
        context_builder="login_code_context",
    ),
    GalleryEmail(
        key="unknown_account",
        name="No account found",
        section="System/Auth",
        renderer=Renderer.ALLAUTH,
        trigger_note=(
            "Sent when someone requests a login code for an email address we have no account for. "
            "Goes to the address they entered."
        ),
        edit_pointer="Template in code (templates/account/email/unknown_account_*.{txt,html})",
        audience="The address with no account on file.",
        template_prefix="unknown_account",
        context_builder="unknown_account_context",
    ),
    GalleryEmail(
        key="account_already_exists",
        name="Account already exists",
        section="System/Auth",
        renderer=Renderer.ALLAUTH,
        trigger_note=(
            "Sent when someone tries to sign up with an email address that already has an account. "
            "Goes to that address."
        ),
        edit_pointer="Template in code (templates/account/email/account_already_exists_*.{txt,html})",
        audience="The existing account's address.",
        template_prefix="account_already_exists",
        context_builder="account_already_exists_context",
    ),
    GalleryEmail(
        key="find_account",
        name="Find my account (login link)",
        section="System/Auth",
        renderer=Renderer.INLINE_STRING,
        trigger_note=(
            "Sent when someone uses Find Your Account and their name matches an active member. "
            "Plain-text only — reminds them of their account email and links the login page. "
            "Goes to the member's email on file."
        ),
        edit_pointer="Text authored in code (core/forms.py::FindAccountForm.send_login_email)",
        audience="The matched member's email on file.",
        context_builder="find_account_context",
    ),
]

# Every spine event key whose EMAIL ships as a structural template above. The family-1
# derivation tests membership here FIRST — before the EMAIL-off classification — so a
# member-OFF event that still emails staff through a shell template (tab_charge_failed →
# charge_failed_admin) renders as its structural card instead of landing in "No email".
_STRUCTURAL_EVENT_KEYS: frozenset[str] = frozenset().union(*(e.event_keys for e in STRUCTURAL_EMAILS))

# Hand-written trigger notes for spine events whose registry description reads thin
# as a "Sent when…" sentence. Everything else derives from the event description.
_TRIGGER_NOTES: dict[str, str] = {
    "guild_announcement": (
        "Sent when a guild lead or staffer posts an announcement to their guild (members can opt out)."
    ),
    "member.invited": "Sent when an admin invites a prospective member to Past Lives.",
    "member.login_invite": (
        "Sent when an admin emails an existing member who has never signed in a first-time sign-in link."
    ),
    "voting.closing_soon": (
        "Sent a few days before the monthly guild-funding vote closes, to each member who has "
        "already voted, with their recorded picks."
    ),
    "voting.vote_soon": "Sent as a nudge to members who have signed in but haven't cast a funding vote yet.",
    "voting.officers_closing_soon": (
        "Sent a few days before the monthly guild-funding vote closes, to guild leadership, "
        "with live turnout so they can rally their guilds."
    ),
    "voting.results_published": (
        "Sent when an admin clicks Send results after a monthly funding snapshot — each voter "
        "gets their personalized results."
    ),
    "voting.results_ready": (
        "Sent to admins when a funding snapshot is taken and the results are ready to review and send."
    ),
    "release.published": "Sent when a new release of the Past Lives app is announced.",
}


# Events whose EMAIL audience is NOT the one their in-app recipient resolver describes,
# because the send addresses explicit ``email_to`` addresses instead (the guest-safe
# transactional path). The card must document who gets the *email*, so these win over
# ``audience_description``.
_EMAIL_AUDIENCE_OVERRIDES: dict[str, str] = {
    "class_cancelled": (
        "Everyone holding a spot on the class — members and guests alike. "
        "(The in-app bell row still goes to every active member.)"
    ),
    "refund_issued": "Whoever booked the registration, member or guest, at the address they booked with.",
}


def _spine_audience(event: EventType) -> str:
    """Who receives this event's EMAIL — the override when one exists, else the resolver."""
    return _EMAIL_AUDIENCE_OVERRIDES.get(event.key, audience_description(event))


def _spine_trigger_note(event: EventType) -> str:
    """The plain-language trigger note for a derived spine card."""
    if event.key in _TRIGGER_NOTES:
        return f"{_TRIGGER_NOTES[event.key]} Goes to: {_spine_audience(event)}"
    desc = event.description.rstrip(".")
    desc = desc[0].lower() + desc[1:] if desc else event.label.lower()
    return f"Sent when {desc}. Goes to: {_spine_audience(event)}"


def _spine_entry(event: EventType) -> GalleryEmail:
    """Build the derived SPINE_COPY gallery entry for one emailing spine event."""
    section = _CATEGORY_SECTIONS[event.category]
    return GalleryEmail(
        key=event.key,
        name=event.label,
        section=section,
        renderer=Renderer.SPINE_COPY,
        trigger_note=_spine_trigger_note(event),
        edit_pointer="Edit in Site Settings → Notifications",
        audience=_spine_audience(event),
        event_keys=frozenset({event.key}),
    )


def _no_email_reason(event: EventType) -> str:
    """Why an event sends no email card — shown in the closing note-section."""
    email = event.channel(Channel.EMAIL)
    if email is None:
        channels = " + ".join(c.value.replace("_", " ") for c in event.channel_list)
        return f"{channels} only — no email channel"
    return "email is opt-in (off by default); members who opt in receive the generic notification copy"


def gallery_emails() -> list[GalleryEmail]:
    """Every gallery card: structural/special entries + the derived spine cards.

    Family-1 derivation order (M2): the structural dedup check runs BEFORE the
    EMAIL-off classification, so a member-OFF event whose email ships via a shell
    template renders as its structural card.
    """
    derived: list[GalleryEmail] = []
    for event in all_events():
        if event.key in _STRUCTURAL_EVENT_KEYS:
            continue  # its email ships as a structural template — rendered by that entry
        email = event.channel(Channel.EMAIL)
        if email is None or email.default is ChannelDefault.OFF:
            continue  # listed in the "No email is sent" section instead
        derived.append(_spine_entry(event))
    return list(STRUCTURAL_EMAILS) + derived


def no_email_events() -> list[tuple[EventType, str]]:
    """Every spine event that gets no card, with the plain-language reason."""
    out: list[tuple[EventType, str]] = []
    for event in all_events():
        if event.key in _STRUCTURAL_EVENT_KEYS:
            continue
        email = event.channel(Channel.EMAIL)
        if email is None or email.default is ChannelDefault.OFF:
            out.append((event, _no_email_reason(event)))
    return out


# --- Guard 1 — template-pair discovery ----------------------------------------

# The email template dirs the app renders standalone emails from.
_TEMPLATE_DIRS: tuple[str, ...] = (
    "templates/classes/emails",
    "templates/membership/emails",
    "templates/billing/email",
    "templates/account/email",
)

_ALLAUTH_MESSAGE_SUFFIX = "_message"


def discover_template_pairs() -> set[str]:
    """Every standalone email template pair under the email dirs.

    A pair is an ``.html`` not starting with ``_`` that has a sibling ``.txt`` of the
    same basename. allauth's ``<prefix>_message.{html,txt}`` counts as one pair keyed
    ``<prefix>``. The underscore rule + sibling rule naturally exclude every include
    and shell (``_base.html``, ``_footer.*``, ``notification_shell.html``,
    ``base_message.txt``, …).
    """
    pairs: set[str] = set()
    for rel in _TEMPLATE_DIRS:
        directory = REPO_ROOT / rel
        for html in directory.glob("*.html"):
            if html.name.startswith("_"):
                continue
            base = html.stem
            if not (directory / f"{base}.txt").exists():
                continue
            if base.endswith(_ALLAUTH_MESSAGE_SUFFIX):
                pairs.add(base[: -len(_ALLAUTH_MESSAGE_SUFFIX)])
            else:
                pairs.add(base)
    return pairs


def registered_template_pairs() -> set[str]:
    """The template-pair keys the registry covers (specials have no discoverable pair)."""
    keys: set[str] = set()
    for entry in STRUCTURAL_EMAILS:
        if entry.renderer is Renderer.SHELL_TEMPLATE and entry.html_template:
            keys.add(Path(entry.html_template).stem)
        elif entry.renderer is Renderer.WELCOME:
            keys.add(entry.key)  # renders its pair via _welcome_email_bodies, no template fields
        elif entry.renderer is Renderer.ALLAUTH and entry.template_prefix:
            keys.add(entry.template_prefix)
    return keys


class EmailGalleryIncomplete(Exception):
    """Raised when a discovered email template pair has no gallery registration."""


def assert_registry_complete() -> None:
    """Fail loudly when a discovered template pair is not registered (Guard 1)."""
    missing = discover_template_pairs() - registered_template_pairs()
    if missing:
        raise EmailGalleryIncomplete(
            f"Email template pairs with no gallery registration: {sorted(missing)} — "
            "register them in tests/e2e/email_gallery/registry.py so their copy is reviewable."
        )


# --- Guard 2 — inline-string send discovery (B3) ------------------------------

# App packages scanned for direct ``core.email.send`` call sites. The spine's own
# transport (core/events/emit.py + core/events/channels.py) is the choke-point every
# family-1/2 email already funnels through, so those two modules are exempt.
_APP_DIRS: tuple[str, ...] = ("classes", "membership", "billing", "core", "hub", "plfog")
_SPINE_TRANSPORT_MODULES: frozenset[str] = frozenset({"core/events/emit.py", "core/events/channels.py"})
_SKIP_DIR_NAMES: frozenset[str] = frozenset({"tests", "spec", "migrations"})

# trigger_kinds whose copy IS rendered by a gallery card.
_REGISTERED_INLINE_KINDS: dict[str, str] = {
    "core.find_account": "the find_account INLINE_STRING card",
    "classes.welcome_email": "the welcome WELCOME card (this is the real send)",
}

# trigger_kinds deliberately NOT carded, each with the reason.
_INLINE_EMAIL_ALLOWLIST: dict[str, str] = {
    "classes.welcome_email_test": "identical body to the welcome card with a [Test] subject prefix",
    "classes.instructor_message": "instructor free-text 'email the class' blast — author-written, no app copy",
    "classes.admin_message": "admin free-text 'email the class' blast — author-written, no app copy",
    "release_email.test": "test-send of the release email to one admin — same copy as the release_update card",
    "announcement.test": "test-send of the announcement composer — same copy as the announcement card",
    "hub.beta_feedback": "member-authored free-text feedback forwarded to staff — no app copy to approve",
}

# Send sites whose trigger_kind is computed (not a string literal), keyed by
# repo-relative module path, each with the reason its copy is still covered.
_DYNAMIC_SEND_ALLOWLIST: dict[str, str] = {
    "plfog/adapters.py": (
        "the allauth adapter derives auth.* trigger kinds from the template prefix — "
        "its copy is the account/email templates, covered by the ALLAUTH cards + Guard 1"
    ),
}


def _iter_app_modules() -> list[Path]:
    modules: list[Path] = []
    for app in _APP_DIRS:
        for path in (REPO_ROOT / app).rglob("*.py"):
            rel = path.relative_to(REPO_ROOT).as_posix()
            if rel in _SPINE_TRANSPORT_MODULES:
                continue
            parts = set(path.relative_to(REPO_ROOT).parts)
            if parts & _SKIP_DIR_NAMES or path.name.endswith("_spec.py"):
                continue
            modules.append(path)
    return modules


def _send_aliases(tree: "object") -> tuple[set[str], set[str]]:
    """(direct-call names, module-attribute names) that reach ``core.email.send`` in a module."""
    import ast

    func_names: set[str] = set()
    module_names: set[str] = set()
    assert isinstance(tree, ast.AST)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module == "core.email":
                for alias in node.names:
                    if alias.name == "send":
                        func_names.add(alias.asname or alias.name)
            elif node.module == "core":
                for alias in node.names:
                    if alias.name == "email":
                        module_names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "core.email" and alias.asname:
                    module_names.add(alias.asname)
    return func_names, module_names


def discover_inline_sends() -> tuple[dict[str, list[str]], list[str]]:
    """Every direct ``core.email.send`` call site in the app tree.

    Returns ``(literal_kinds, dynamic_sites)``: a map of each literal ``trigger_kind``
    to the modules calling with it, and the modules whose ``trigger_kind`` argument is
    computed rather than a string literal (those must be in the dynamic allowlist).
    """
    import ast

    literal_kinds: dict[str, list[str]] = {}
    dynamic_sites: list[str] = []
    for path in _iter_app_modules():
        rel = path.relative_to(REPO_ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        func_names, module_names = _send_aliases(tree)
        if not func_names and not module_names:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            is_send = (isinstance(func, ast.Name) and func.id in func_names) or (
                isinstance(func, ast.Attribute)
                and func.attr == "send"
                and isinstance(func.value, ast.Name)
                and func.value.id in module_names
            )
            if not is_send:
                continue
            kind = next((kw.value for kw in node.keywords if kw.arg == "trigger_kind"), None)
            if isinstance(kind, ast.Constant) and isinstance(kind.value, str):
                literal_kinds.setdefault(kind.value, []).append(rel)
            else:
                dynamic_sites.append(rel)
    return literal_kinds, dynamic_sites


def unregistered_inline_sends() -> tuple[dict[str, list[str]], list[str]]:
    """Inline sends the gallery neither cards nor allowlists (Guard 2). Empty when complete."""
    literal_kinds, dynamic_sites = discover_inline_sends()
    covered = set(_REGISTERED_INLINE_KINDS) | set(_INLINE_EMAIL_ALLOWLIST)
    missing_kinds = {k: sites for k, sites in literal_kinds.items() if k not in covered}
    missing_dynamic = [site for site in dynamic_sites if site not in _DYNAMIC_SEND_ALLOWLIST]
    return missing_kinds, missing_dynamic
