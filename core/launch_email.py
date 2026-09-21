"""The Member Portal launch email: one announcement, two doors in.

Every active member hears that the portal is open and what unlocks in the weeks after.
Which door they get depends on whether their account has ever signed in:

- A member who has signed in before gets the announcement with a sign-in button. It rides
  the ``site_announcement`` broadcast, so they also get the in-app notification and their
  saved preferences apply, exactly like the release email.
- A member who has never signed in gets the same announcement as an activation invite: the
  forced ``member.login_invite`` email with a button to the login-code page and their address
  pre-filled. Nothing in-app, because they have no bell to check yet.

Both variants render from ``membership/emails/launch_announcement.html``, which reuses the
release email's hero, button and footer partials plus the rollout table. The plain-text part
is built here so the two never drift, as ``core.release_email`` does.

Sends are idempotent per member through the delivery ledger: both emits share
:data:`LAUNCH_PERIOD`, so a re-run reaches only whoever was missed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

from django.conf import settings
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.dateformat import format as date_format

if TYPE_CHECKING:
    from core.events.emit import EmitResult
    from membership.models import Member

#: The day the portal opened to members. Labels the hero band and keys the delivery ledger.
LAUNCH_DATE = date(2026, 9, 21)

#: The idempotency bucket both emits share: a re-run skips everyone already delivered.
LAUNCH_PERIOD = f"portal_launch:{LAUNCH_DATE.isoformat()}"

ANNOUNCEMENT_SUBJECT = "The Past Lives Member Portal is live"
INVITE_SUBJECT = "Your Past Lives Member Portal account is ready"

HERO_BADGE = "Now live"
HERO_TITLE = "The Member Portal is open"

ANNOUNCEMENT_CTA = "Sign in to the Member Portal"
INVITE_CTA = "Activate your account"

_INTRO = (
    "Today we're opening the Past Lives Member Portal to every member. It's one place for "
    "your profile, your guilds, the member directory, the community calendar and your notifications."
)
_ACTIVATE = (
    "Your account is already set up. To activate it, click the button below and we'll email "
    "you a one-time login code. There's no password to remember."
)

#: What an ordinary active member can do on launch day. Every line checked against merged code.
LIVE_TODAY: tuple[str, ...] = (
    "Set up your profile and choose whether it appears in the Member Directory.",
    "See who runs Past Lives and each guild on the Leadership Directory.",
    "Follow guild meetings and community events on the calendar.",
    "Choose how you hear from us: in the portal, by email or by Discord DM.",
)

SCHEDULE_NOTE = "Dates are tentative. We'll announce each release in the portal and on Discord as it lands."


@dataclass(frozen=True)
class RolloutWeek:
    """One row of the rollout table: which week, when it starts, what unlocks."""

    label: str
    starts: str
    features: tuple[str, ...]


#: The tentative rollout after launch day, as the email shows it. Week 1 is the launch itself.
ROLLOUT_SCHEDULE: tuple[RolloutWeek, ...] = (
    RolloutWeek("Week 1", "Mon Sep 21", ("Member Portal opens",)),
    RolloutWeek("Week 2", "Mon Sep 28", ("Guild Pages", "Orientations")),
    RolloutWeek("Week 3", "Mon Oct 5", ("Member Wiki", "Meetings & Spaces")),
    RolloutWeek("Week 4", "Mon Oct 12", ("Classes & Workshops",)),
    RolloutWeek("Week 5", "Mon Oct 19", ("Equipment (or Space Rentals)",)),
)


def portal_home_url() -> str:
    """The absolute hub home URL: the announcement's button, and where a signed-out click lands on login."""
    return f"{settings.MEMBER_BASE_URL}{reverse('hub_home')}"


def _render(
    *,
    subject: str,
    preheader: str,
    greeting: str,
    intro_paragraphs: list[str],
    cta_url: str,
    cta_label: str,
    signin_note: str,
) -> tuple[str, str]:
    """Assemble ``(html, text)`` for one variant from the shared context."""
    context = {
        "preheader": preheader,
        "hero_badge": HERO_BADGE,
        "hero_title": HERO_TITLE,
        "hero_subtitle": date_format(LAUNCH_DATE, "F j, Y"),
        "greeting": greeting,
        "intro_paragraphs": intro_paragraphs,
        "live_today": list(LIVE_TODAY),
        "weeks": ROLLOUT_SCHEDULE,
        "schedule_note": SCHEDULE_NOTE,
        "cta_url": cta_url,
        "cta_label": cta_label,
        "signin_note": signin_note,
    }
    # Coerce SafeString to plain str, as the release renderer does, so an admin preview's
    # srcdoc attribute escapes it correctly.
    html = "" + render_to_string("membership/emails/launch_announcement.html", context)
    text = _text(
        subject=subject,
        greeting=greeting,
        intro_paragraphs=intro_paragraphs,
        cta_label=cta_label,
        cta_url=cta_url,
        signin_note=signin_note,
    )
    return html, text


def _text(
    *,
    subject: str,
    greeting: str,
    intro_paragraphs: list[str],
    cta_label: str,
    cta_url: str,
    signin_note: str,
) -> str:
    """The plain-text part, mirroring the HTML section for section, ending in the shared footer."""
    lines: list[str] = [subject, ""]
    if greeting:
        lines += [greeting, ""]
    for paragraph in intro_paragraphs:
        lines += [paragraph, ""]
    lines += ["What you can do today", *[f"• {line}" for line in LIVE_TODAY], ""]
    lines += ["What's coming next"]
    for week in ROLLOUT_SCHEDULE:
        lines.append(f"{week.label}, starting {week.starts}: {'; '.join(week.features)}")
    lines += [SCHEDULE_NOTE, "", f"{cta_label}: {cta_url}"]
    if signin_note:
        lines += [signin_note]
    lines.append("")
    return "\n".join(lines) + render_to_string("membership/emails/_footer.txt")


def render_launch_announcement(*, cta_url: str) -> tuple[str, str]:
    """The variant for a member who has signed in before: no greeting (one message reaches everyone)."""
    return _render(
        subject=ANNOUNCEMENT_SUBJECT,
        preheader="Sign in for your profile, your guilds and the calendar, plus what's coming over the next five weeks.",
        greeting="",
        intro_paragraphs=[_INTRO],
        cta_url=cta_url,
        cta_label=ANNOUNCEMENT_CTA,
        signin_note="",
    )


def render_launch_invite(*, member_name: str, login_url: str, email: str) -> tuple[str, str]:
    """The variant for a member who has never signed in: personal, with the activation button."""
    return _render(
        subject=INVITE_SUBJECT,
        preheader="Activate your account with one click, then see what's coming over the next five weeks.",
        greeting=f"Hi {member_name},",
        intro_paragraphs=[_INTRO, _ACTIVATE],
        cta_url=login_url,
        cta_label=INVITE_CTA,
        signin_note=f"This link is for {email}. Sign in with that same address whenever you come back.",
    )


def send_launch_announcement(*, discord: bool = False) -> EmitResult:
    """Broadcast the announcement to every activated member through ``site_announcement``.

    The pre-rendered HTML rides as the EMAIL override so the spine does not autoescape it;
    the in-app row and any Discord post render from the event's copy. Discord stays off
    unless asked for, like the release email.
    """
    from core.events.channels import Channel, Message
    from core.events.emit import emit

    site_url = portal_home_url()
    html, text = render_launch_announcement(cta_url=site_url)
    email = Message(
        title=ANNOUNCEMENT_SUBJECT, body=text, url=site_url, html_body=html, trigger_kind="site_announcement"
    )
    return emit(
        "site_announcement",
        context={
            "member_name": "there",
            "announcement_title": ANNOUNCEMENT_SUBJECT,
            "announcement_body": (
                "The Member Portal is open. Sign in for your profile, your guilds, the calendar "
                "and what's coming over the next five weeks."
            ),
            "site_url": site_url,
        },
        url=site_url,
        period=LAUNCH_PERIOD,
        messages={Channel.EMAIL: email},
        suppress_broadcast=not discord,
    )


def send_launch_invite(member: Member) -> EmitResult:
    """Email one never-signed-in member the launch as an activation invite.

    Provisions their account first (:meth:`Member.first_sign_in_url`), then emits the forced
    ``member.login_invite`` email with the launch body in place of the stock copy.

    Raises:
        ValueError: if the member has no email on file (nothing to send to).
    """
    from core.events.channels import Channel, Message
    from core.events.emit import emit

    login_url = member.first_sign_in_url()
    html, text = render_launch_invite(member_name=member.display_name, login_url=login_url, email=member.primary_email)
    email = Message(title=INVITE_SUBJECT, body=text, url=login_url, html_body=html, trigger_kind="member.login_invite")
    return emit(
        "member.login_invite",
        target=member,
        context={"user": member.user, "member_name": member.display_name, "login_url": login_url},
        period=LAUNCH_PERIOD,
        messages={Channel.EMAIL: email},
    )


def _preview_name(to: str) -> str:
    """The member behind ``to`` for the preview's greeting, or "there" for an outside address."""
    from core.email_prefs import user_for_email
    from membership.models import Member

    user = user_for_email(to)
    member = Member.objects.filter(user_id=user.pk).first() if user is not None else None
    return member.display_name if member is not None else "there"


def send_launch_previews(to: str) -> None:
    """Send both variants to one inbox, straight through ``core.email.send``.

    Deliberately not the notification spine: a preview must not claim a member's
    :data:`LAUNCH_PERIOD` ledger slot, or the real send would skip them. A provider
    failure raises, so the command reports it instead of a preview that never arrived.
    """
    from core.email import send
    from membership.models import login_code_url

    html, text = render_launch_announcement(cta_url=portal_home_url())
    # The trigger_kind is a literal on purpose: the email gallery's send-site lint reads it.
    send(to=to, subject=ANNOUNCEMENT_SUBJECT, trigger_kind="portal_launch.test", text_body=text, html_body=html)
    html, text = render_launch_invite(member_name=_preview_name(to), login_url=login_code_url(to), email=to)
    send(to=to, subject=INVITE_SUBJECT, trigger_kind="portal_launch.test", text_body=text, html_body=html)
