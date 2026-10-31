"""No-login access to a member's notification preferences from an email link.

Every email footer carries a "manage your email preferences or unsubscribe" link
pointing at ``/settings/?tab=notifications``. So a member can act on it straight from
their inbox, the link also carries a signed, per-recipient token that authorizes
editing *only* that one page without signing in. Every other settings surface still
requires a real login.

The link is built once per email (a placeholder in the footer template), then
:func:`finalize_manage_prefs_link` swaps in the recipient's own token at send time,
in ``core.email.send`` — the one place that knows who each message is going to.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import TYPE_CHECKING

from django.conf import settings
from django.core import signing

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractBaseUser

# Salt scoping the signature to this feature (a token signed here can't be replayed
# against another ``signing`` consumer).
_SALT = "email-notification-prefs"

# Baked into the rendered footer at template time; ``core.email.send`` replaces it with
# the recipient's real token (or "" when the recipient is not a known member) right
# before the message is handed to the mail backend.
PREFS_TOKEN_PLACEHOLDER = "__PL_PREFS_TOKEN__"


def make_prefs_token(user: AbstractBaseUser) -> str:
    """A signed token authorizing notification-preference edits for ``user``."""
    return signing.dumps(user.pk, salt=_SALT)


def read_prefs_token(token: str) -> AbstractBaseUser | None:
    """Resolve a prefs token to its user, or ``None`` if missing or invalid.

    No ``max_age``: an unsubscribe link must keep working however long the email sits
    in an inbox. The token grants access to the notification matrix only, so a
    non-expiring link is an acceptable trade for that narrow scope.
    """
    if not token:
        return None
    from django.contrib.auth import get_user_model

    try:
        user_pk = signing.loads(token, salt=_SALT)
    except signing.BadSignature:
        return None
    return get_user_model().objects.filter(pk=user_pk).first()


def user_for_email(email: str) -> AbstractBaseUser | None:
    """The user who owns ``email`` (a verified alias or the direct address), or ``None``."""
    from allauth.account.models import EmailAddress
    from django.contrib.auth import get_user_model

    address = EmailAddress.objects.filter(email__iexact=email).select_related("user").first()
    if address is not None:
        return address.user
    return get_user_model().objects.filter(email__iexact=email).first()


def finalize_manage_prefs_link(text_body: str, html_body: str | None, recipients: list[str]) -> tuple[str, str | None]:
    """Swap the footer's token placeholder for the recipient's real token.

    Only a single, known-member recipient gets a token. A multi-recipient send or a
    non-member address gets an empty token, so the link degrades to the plain settings
    page that asks them to sign in. A no-op (returns the bodies unchanged) when the
    placeholder is absent, so it never touches free-text or third-party templates.
    """
    has_text = PREFS_TOKEN_PLACEHOLDER in text_body
    has_html = html_body is not None and PREFS_TOKEN_PLACEHOLDER in html_body
    if not has_text and not has_html:
        return text_body, html_body

    token = ""
    if len(recipients) == 1:
        user = user_for_email(recipients[0])
        if user is not None:
            token = make_prefs_token(user)

    if has_text:
        text_body = text_body.replace(PREFS_TOKEN_PLACEHOLDER, token)
    if has_html and html_body is not None:
        html_body = html_body.replace(PREFS_TOKEN_PLACEHOLDER, token)
    return text_body, html_body


# The past students stop link (#1042): an instructor's email to everyone who took their classes
# links to a no login page that stops that instructor's emails to the address it went to. The
# token signs the instructor and the address together. Which instructor is never read from the
# email's text: the send names it in a :class:`StopLink`, whose placeholder is random per message
# and carries no pk, and ``core.email.send`` fills only that placeholder, for only that instructor.
# Anything an author types into the message renders as typed and is never signed.
_STOP_SALT = "past-students-stop"


@dataclass(frozen=True)
class StopLink:
    """Which instructor a past students email's stop link stops, and where its token goes.

    Built once per message by :meth:`for_instructor`; the message carries it to every send.
    """

    instructor_pk: int
    placeholder: str

    @classmethod
    def for_instructor(cls, instructor_pk: int) -> StopLink:
        """A stop link for ``instructor_pk`` with a fresh placeholder no author can guess."""
        return cls(instructor_pk=instructor_pk, placeholder=f"__PL_STOP_{secrets.token_hex(16)}__")

    @property
    def url(self) -> str:
        """The stop page URL with the placeholder where each recipient's token goes."""
        return stop_url(self.placeholder)


def make_stop_token(instructor_pk: int, email: str) -> str:
    """A signed token naming one instructor and one address (lowercased) for the stop page."""
    return signing.dumps([instructor_pk, email.strip().lower()], salt=_STOP_SALT)


def read_stop_token(token: str) -> tuple[int, str] | None:
    """The ``(instructor pk, address)`` a stop token names, or ``None`` when this site did not sign it.

    No ``max_age``, as with the prefs token: the link must keep working however long the email
    sits in an inbox, and it can only stop one instructor's emails to the address it names.
    """
    try:
        payload = signing.loads(token, salt=_STOP_SALT)
    except signing.BadSignature:
        return None
    if not (isinstance(payload, list) and len(payload) == 2):
        return None
    instructor_pk, email = payload
    if not isinstance(instructor_pk, int) or not isinstance(email, str) or not email:
        return None
    return instructor_pk, email


def stop_url(token: str) -> str:
    """The absolute stop page URL for ``token``, on the members site."""
    from django.urls import reverse

    return settings.MEMBER_BASE_URL.rstrip("/") + reverse("classes:stop_past_student_emails", args=[token])


def finalize_stop_link(
    text_body: str, html_body: str | None, recipients: list[str], stop_link: StopLink | None
) -> tuple[str, str | None, str]:
    """Fill ``stop_link``'s placeholder with a token of its instructor and the one recipient's address.

    Returns the bodies and the stop URL for the ``List-Unsubscribe`` header, ``""`` with no stop
    link. Without a ``stop_link`` the bodies are returned untouched, whatever they contain. A send
    to several addresses at once gets an empty token (its link is refused) and no header.
    """
    if stop_link is None:
        return text_body, html_body, ""
    if len(recipients) != 1:
        token = ""
    else:
        token = make_stop_token(stop_link.instructor_pk, recipients[0])
    text_body = text_body.replace(stop_link.placeholder, token)
    if html_body is not None:
        html_body = html_body.replace(stop_link.placeholder, token)
    return text_body, html_body, stop_url(token) if token else ""
