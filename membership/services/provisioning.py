"""Idempotent, silent provisioning of a User for a Member.

This is the single source of truth for the "every member is a user" invariant
(see ``docs/superpowers/plans/2026-06-25-members-are-users.md``). Both the
going-forward auto-provision signal and the ``provision_member_users`` backfill
command call :func:`provision_user_for_member`.

The helper OWNS the whole chain — create user → verified primary
``EmailAddress`` → promote staged ``MemberEmail`` rows → link ``member.user`` —
and runs under a re-entrancy guard so the User ``post_save`` signal
(:func:`membership.signals.ensure_user_has_member`) is a COMPLETE no-op while a
provision is in flight (Review fix #3). It is SILENT: it sends no email and fires
no notification (Review fix #10 / Risk R1).
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
from contextvars import ContextVar
from typing import TYPE_CHECKING

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction

if TYPE_CHECKING:
    from collections.abc import Iterator

    from django.contrib.auth.models import User

    from membership.models import Member

logger = logging.getLogger(__name__)

# Django's ``User.username`` is a 150-char field; an email can be longer (254).
_USERNAME_MAX_LENGTH = 150

# Re-entrancy guard. While a provision is in flight on this thread, the
# ``ensure_user_has_member`` User post_save signal must be a COMPLETE no-op — the
# helper owns create → EmailAddress → migrate → link. A contextvar (not a plain
# thread-local) so it is correctly scoped to the running call stack.
_provisioning: ContextVar[bool] = ContextVar("membership_provisioning", default=False)


def is_provisioning() -> bool:
    """True while :func:`provision_user_for_member` is running on this thread."""
    return _provisioning.get()


@contextlib.contextmanager
def _provisioning_guard() -> Iterator[None]:
    """Mark the current call stack as provisioning so the User signal no-ops."""
    token = _provisioning.set(True)
    try:
        yield
    finally:
        _provisioning.reset(token)


def _username_for(email: str) -> str:
    """A unique, length-safe username for ``email``.

    ``create_user(username=email)`` is the established pattern (mirrors
    ``AutoCreateUserLoginCodeForm._create_user_idempotent``), but an email can
    exceed the 150-char username column. When it does, truncate and append a short
    hash of the full email so the username stays unique and stable per address
    (Review fix #4 — don't let one long row crash the batch).
    """
    if len(email) <= _USERNAME_MAX_LENGTH:
        return email
    digest = hashlib.sha256(email.encode("utf-8")).hexdigest()[:12]
    prefix = email[: _USERNAME_MAX_LENGTH - len(digest) - 1]
    return f"{prefix}-{digest}"


def _get_or_create_user(email: str) -> User | None:
    """Return the User for ``email``, creating a passwordless one if needed.

    Reuses an existing User with the same email (idempotent). Otherwise creates one
    with an unusable password (login is by code). Tolerates a concurrent create —
    the username unique constraint makes the loser raise ``IntegrityError``; the
    create runs in its own savepoint so the surrounding transaction stays usable,
    and we then re-read the winner. Returns ``None`` only when creation failed and
    no row can be found (the caller skips + logs).
    """
    user_model = get_user_model()
    existing = user_model.objects.filter(email__iexact=email).first()
    if existing is not None:
        return existing
    username = _username_for(email)
    try:
        with transaction.atomic():
            return user_model.objects.create_user(username=username, email=email)
    except IntegrityError:
        logger.info("User for %s created concurrently; reusing it.", email)
        return user_model.objects.filter(email__iexact=email).first()


def _ensure_unverified_primary(user: User, email: str) -> None:
    """Create an UNVERIFIED primary EmailAddress for ``email`` if none exists.

    Only used on the ``verified=False`` path. The default ``verified=True`` path is
    handled entirely by ``MemberEmail.objects.migrate_to_user`` (which creates a
    verified primary). When this creates the primary first, ``migrate_to_user``
    finds it already-primary and leaves its ``verified`` flag untouched.
    """
    from allauth.account.models import EmailAddress

    if not EmailAddress.objects.filter(user=user, email__iexact=email).exists():
        EmailAddress.objects.create(user=user, email=email, verified=False, primary=True)


def provision_user_for_member(member: Member, *, verified: bool = True) -> User | None:
    """Idempotently ensure ``member`` has a linked User with a primary EmailAddress.

    SILENT: sends no email and fires no notification. Returns the linked user, or
    ``None`` when the member cannot be provisioned (blank email, or the email
    already belongs to a different member — the duplicate-email case, Review fix #2).

    Behaviour:
        * Already linked → return ``member.user`` (no-op).
        * Blank ``_pre_signup_email`` → return ``None`` (a passwordless account needs
          an email; this is the genuinely-emailless case).
        * Otherwise create/reuse ``User(username=email, email=email)``, create the
          verified primary ``EmailAddress`` + promote any staged ``MemberEmail``
          rows (via ``migrate_to_user``), then link ``member.user`` — PRESERVING the
          member's existing ``status`` (Review fix #1: the link path must not flip a
          FORMER/SUSPENDED member to ACTIVE).

    Args:
        member: The member to provision.
        verified: Whether the primary EmailAddress is created verified. Defaults to
            ``True`` so login-by-code works immediately (Decision 3 / Risk R5).
    """
    if member.user_id is not None:
        return member.user

    email = (member._pre_signup_email or "").strip().lower()
    if not email:
        logger.info("Skipping provision for member %s (pk=%s): no email on file.", member.display_name, member.pk)
        return None

    from membership.models import Member, MemberEmail

    with _provisioning_guard():
        user = _get_or_create_user(email)
        if user is None:
            logger.warning(
                "Could not provision member %s (pk=%s): user create failed for %s.", member, member.pk, email
            )
            return None

        # Never steal a User already owned by a DIFFERENT member (the duplicate
        # _pre_signup_email case): linking would violate the OneToOne. Skip + log.
        if Member.objects.filter(user=user).exclude(pk=member.pk).exists():
            logger.warning(
                "Skipping provision for member %s (pk=%s): email %s already belongs to another member.",
                member.display_name,
                member.pk,
                email,
            )
            return None

        with transaction.atomic():
            # Only the user FK is written — status (and everything else) is preserved.
            member.user = user
            member.save(update_fields=["user"])
            if not verified:
                _ensure_unverified_primary(user, email)
            MemberEmail.objects.migrate_to_user(user)

        logger.info("Provisioned user %s for member %s (pk=%s).", user.username, member.display_name, member.pk)
        return user
