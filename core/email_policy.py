"""What staging is allowed to do with an outbound email (settings.IS_STAGING).

Staging is a clone of production: the same code, a copy of the members, and a real mail
transport, because the people practising there (instructors, admins) must receive the
emails their actions trigger. Two things keep that from reaching anyone else:

1. **Marking.** Every message is stamped so nobody mistakes it for the real portal: a
   ``[STAGING]`` subject prefix, a first line in the plain-text body and a banner at the
   top of the HTML body.
2. **Delivery policy.** A recipient is handed to the transport only when
   :func:`is_deliverable` says so: listed in ``EMAIL_DELIVERY_ALLOWLIST`` (a full address or
   a bare domain), or the address belongs to a user who is staff, a FOG admin or an
   instructor. Everyone else is suppressed by :func:`core.email.send`, which writes a
   ``TransactionalEmailLog`` row per dropped address so the admin Email Log shows what
   *would* have gone out.

Every function here is a pure decision or a string transform over the message; the only
database access is the address-to-user lookup, so the policy is unit-testable without a
transport. Outside staging every function is a no-op (``is_deliverable`` is always True and
the marking helpers return their input unchanged).
"""

from __future__ import annotations

import re
from email.utils import parseaddr

from django.conf import settings

SUBJECT_PREFIX = "[STAGING] "
SUPPRESSION_REASON = "staging: recipient not on the delivery allowlist"

# Inserted right after the opening <body> tag so it is the first thing a mail client paints.
# Inline styles on purpose: this is email, where <style> blocks and classes are stripped.
_BANNER_STYLE = (
    "background-color:#b45309;color:#ffffff;font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;"
    "font-size:14px;font-weight:700;line-height:1.4;padding:12px 16px;text-align:center;"
)
_BODY_TAG_RE = re.compile(r"<body\b[^>]*>", re.IGNORECASE)


def staging_notice() -> str:
    """The one sentence every staging email carries, naming the host it came from."""
    return f"STAGING. This came from {settings.MEMBER_HOST}, not the real Past Lives portal. Nothing here is real."


def staging_subject(subject: str) -> str:
    """``subject`` with the staging prefix, added once (a resend never stacks prefixes)."""
    if not settings.IS_STAGING or subject.startswith(SUBJECT_PREFIX):
        return subject
    return f"{SUBJECT_PREFIX}{subject}"


def staging_text_body(text_body: str) -> str:
    """``text_body`` with the staging notice as its first line."""
    if not settings.IS_STAGING:
        return text_body
    return f"{staging_notice()}\n\n{text_body}"


def staging_html_body(html_body: str | None) -> str | None:
    """``html_body`` with the staging banner right after ``<body>`` (or first, if there is none)."""
    if html_body is None or not settings.IS_STAGING:
        return html_body
    banner = f'<div style="{_BANNER_STYLE}">{staging_notice()}</div>'
    match = _BODY_TAG_RE.search(html_body)
    if match is None:
        return f"{banner}{html_body}"
    return f"{html_body[: match.end()]}{banner}{html_body[match.end() :]}"


def is_deliverable(recipient: str) -> bool:
    """Whether staging may hand ``recipient`` to the real mail transport.

    ``recipient`` may be a bare address or a ``Name <address>`` pair; only the address part is
    judged, case-insensitively. Outside staging every recipient is deliverable.

    Args:
        recipient: One recipient as it would be passed to the mail backend.

    Returns:
        True when the address is on ``EMAIL_DELIVERY_ALLOWLIST`` (exactly, or by its domain),
        or belongs to a user who is staff, a FOG admin or an instructor.
    """
    if not settings.IS_STAGING:
        return True
    address = parseaddr(recipient)[1].strip().lower()
    _local, _at, domain = address.rpartition("@")
    allowlist = settings.EMAIL_DELIVERY_ALLOWLIST
    if address in allowlist or domain in allowlist:
        return True
    return _has_practising_role(address)


def partition_recipients(recipients: list[str]) -> tuple[list[str], list[str]]:
    """Split ``recipients`` into (deliverable, suppressed), preserving order."""
    deliverable: list[str] = []
    suppressed: list[str] = []
    for recipient in recipients:
        (deliverable if is_deliverable(recipient) else suppressed).append(recipient)
    return deliverable, suppressed


def _has_practising_role(address: str) -> bool:
    """True when ``address`` belongs to a staff user, a FOG admin or an instructor.

    Reuses the app's own predicates rather than a new role concept: ``User.is_staff`` (kept in
    step with ``fog_role`` by ``Member.sync_user_permissions``), ``Member.is_fog_admin`` and
    ``Member.is_instructor``.
    """
    from core.email_prefs import user_for_email
    from membership.models import Member

    user = user_for_email(address)
    if user is None:
        return False
    if getattr(user, "is_staff", False):
        return True
    member = Member.objects.filter(user_id=user.pk).only("fog_role", "instructor_slug").first()
    return member is not None and (member.is_fog_admin or member.is_instructor)
