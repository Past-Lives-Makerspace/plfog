"""How a login account is named next to its email (#617).

Most login accounts carry no first or last name: the name lives on the linked ``Member``, and
the username is the email. A label built from ``get_full_name() or get_username()`` therefore
read "email · email". These helpers name an account by its member display name, then by the
account's own full name, and fall back to the email alone, never twice.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractBaseUser, AnonymousUser


def user_display_name(user: AbstractBaseUser | AnonymousUser) -> str:
    """The person's name for ``user``: the member display name, else the account's full name, else ``""``.

    ``user.member`` raises an ``AttributeError`` subclass when there is no linked member, so
    ``getattr`` covers an account without one.
    """
    member = getattr(user, "member", None)
    name = (getattr(member, "display_name", "") or "").strip() if member is not None else ""
    if name:
        return name
    get_full_name = getattr(user, "get_full_name", None)
    return (get_full_name() or "").strip() if get_full_name is not None else ""


def user_name_or_email(user: AbstractBaseUser | AnonymousUser) -> str:
    """The person's name for ``user``, else their email, else the username: what to call them alone."""
    return user_display_name(user) or (getattr(user, "email", "") or "").strip() or user.get_username()


def user_label(user: AbstractBaseUser | AnonymousUser) -> str:
    """``"<name> · <email>"`` for ``user``, or just the email (or username) when there is no name."""
    name = user_display_name(user)
    email = (getattr(user, "email", "") or "").strip()
    if not email:
        return name or user.get_username()
    if not name or name.casefold() == email.casefold():
        return email
    return f"{name} · {email}"
