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

from typing import TYPE_CHECKING

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
