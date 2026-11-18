"""How a member and a login account are named.

Staff see a member who set a preferred name with their legal name beside it, "Wren (Quinn
Ashdown)"; other members see what the member chose (the preferred name alone, "Wren (Quinn A.)", or
the full form); everyone greets them by the preferred name, "Hi Wren" (:func:`member_label`,
:func:`greeting_form`).

How a login account is named next to its email (#617).

Most login accounts carry no first or last name: the name lives on the linked ``Member``, and
the username is the email. A label built from ``get_full_name() or get_username()`` therefore
read "email · email". These helpers name an account by its member display name, then by the
account's own full name, and fall back to the email alone, never twice.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractBaseUser, AnonymousUser


def legal_initial_form(full_legal_name: str) -> str:
    """A legal name as first name and last initial: ``"Quinn Ashdown"`` -> ``"Quinn A."``.

    A one word legal name stays as it is.
    """
    words = (full_legal_name or "").split()
    if len(words) < 2:
        return " ".join(words)
    return f"{words[0]} {words[-1][0]}."


def member_label(preferred_name: str, full_legal_name: str, form: str = "full") -> str:
    """A member's preferred name with their legal name beside it: ``"Wren (Quinn Ashdown)"``.

    ``form`` is ``"full"`` for the whole legal name or ``"initial"`` for its first name and last
    initial, ``"Wren (Quinn A.)"``. One name alone when the preferred name is blank (the legal name,
    whole), when the legal name is blank, or when the two are the same name. When the legal name starts
    with the preferred name as whole words, the label is the legal name alone in that form ("Sam" and
    "Sam Reyes" give "Sam Reyes", or "Sam R."; "Sam" and "Samantha Reyes" give "Sam (Samantha Reyes)").
    Case and surrounding spaces are ignored.
    """
    preferred = (preferred_name or "").strip()
    legal = (full_legal_name or "").strip()
    if not preferred or not legal:
        return preferred or legal
    if preferred.casefold() == legal.casefold():
        return preferred
    shown = legal_initial_form(legal) if form == "initial" else legal
    if legal.casefold().startswith(preferred.casefold() + " "):
        return shown
    return f"{preferred} ({shown})"


def greeting_form(label: str) -> str:
    """The name a greeting uses from a :func:`member_label`: ``"Wren (Quinn Ashdown)"`` -> ``"Wren"``.

    Any other name comes back as it is.
    """
    head, sep, tail = label.partition(" (")
    if sep and head and tail.endswith(")"):
        return head
    return label


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
