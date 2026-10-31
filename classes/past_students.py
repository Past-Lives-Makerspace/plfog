"""Everyone who took an instructor's classes: the composer's My past students audience (#1042).

:func:`past_students` is the one roster: who the Recipients step lists, who the count counts and
who the send reaches. :func:`stop_line` is the line every such email ends with, linking to the no
login page that records a :class:`~classes.models.PastStudentOptOut`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from django.contrib.auth.models import User

    from membership.models import Member


@dataclass(frozen=True)
class PastStudents:
    """An instructor's past students, split the way a send reaches them.

    ``users`` are accounts (the bell, push and email), one per account. ``guests`` are addresses
    with no account (the email only), ``(address, label)``, one per lowercased address that no
    listed account already uses.
    """

    users: list[User]
    guests: list[tuple[str, str]]

    @property
    def user_ids(self) -> set[int]:
        """The listed accounts' pks."""
        return {user.pk for user in self.users}

    @property
    def addresses(self) -> list[str]:
        """The email only addresses, in roster order."""
        return [address for address, _label in self.guests]


def past_students(instructor: Member) -> PastStudents:
    """Every distinct person with a CONFIRMED registration in a class ``instructor`` teaches.

    A class counts when the instructor has an instructors row on it, whatever its status. A
    registration whose member has an account is that account, once however many classes they
    took. One with no account is its lowercased address, once, and left out when a listed
    account already uses it (its email, chosen notification email or a verified alias), so
    nobody gets the email twice. Anyone who stopped this instructor's emails is left out
    (:meth:`~classes.models.PastStudentOptOutManager.skipped_by`): by account, and by every
    address of that account, so a guest registration at a stopped member's alias is skipped too.
    A fixed number of queries, however many classes and registrations.

    Args:
        instructor: The member whose classes' students are wanted.

    Returns:
        The roster, accounts and guests each in name order.
    """
    from allauth.account.models import EmailAddress
    from django.db.models.functions import Lower

    from classes.models import PastStudentOptOut, Registration
    from membership.names import user_label

    skip_users, skip_emails = PastStudentOptOut.objects.skipped_by(instructor)
    users: dict[int, User] = {}
    taken: set[str] = set()
    guests: dict[str, str] = {}
    registrations = (
        Registration.objects.filter(class_offering__instructors=instructor, status=Registration.Status.CONFIRMED)
        .select_related("member__user")
        .order_by("first_name", "last_name", "pk")
    )
    for registration in registrations:
        member = registration.member
        user = member.user if member is not None else None
        if user is not None and member is not None:
            addresses = {(user.email or "").strip().lower(), (member.notification_email or "").strip().lower()}
            addresses.discard("")
            if user.pk not in skip_users and not addresses & skip_emails:
                users[user.pk] = user
            taken |= addresses
            continue
        address = registration.email.strip().lower()
        if address and address not in guests:
            name = f"{registration.first_name} {registration.last_name}".strip()
            guests[address] = f"{name} · {address}" if name else address
    taken |= set(
        EmailAddress.objects.filter(user_id__in=list(users))
        .annotate(address=Lower("email"))
        .filter(address__in=list(guests))
        .values_list("address", flat=True)
    )
    return PastStudents(
        users=sorted(users.values(), key=lambda user: (user_label(user).casefold(), user.pk)),
        guests=[
            (address, label)
            for address, label in sorted(guests.items(), key=lambda item: (item[1].casefold(), item[0]))
            if address not in taken and address not in skip_emails
        ],
    )


def instructor_name(instructor: Member) -> str:
    """What a past students email and its stop page call the instructor: their name, never an address.

    The name their instructor profile and every class shows (:attr:`Member.display_name`, the
    preferred name, else the legal name), else "your instructor".
    """
    return instructor.display_name.strip() or "your instructor"


def stop_line(instructor: Member) -> tuple[str, str]:
    """The ``(html, text)`` line that ends every past students email from ``instructor``.

    "You took a class with <name>. Stop emails from <name>" (:func:`instructor_name`), linking to
    the no login stop page. The link carries a placeholder naming the instructor that
    ``core.email.send`` swaps for a token of the instructor and each recipient's own address
    (:func:`core.email_prefs.finalize_stop_link`), so one built message serves every recipient.

    Args:
        instructor: The instructor sending; their emails are the ones the link stops.
    """
    from django.utils.html import escape

    from core.email_prefs import stop_token_placeholder, stop_url

    name = instructor_name(instructor)
    url = stop_url(stop_token_placeholder(instructor.pk))
    html = (
        '<p style="margin:28px 0 0;padding-top:14px;border-top:1px solid #e3e7ec;color:#5b6b78;'
        'font-size:13px;line-height:1.5;" data-past-students-stop>'
        f'You took a class with {escape(name)}. <a href="{escape(url)}" style="color:#0d4876;">'
        f"Stop emails from {escape(name)}</a></p>"
    )
    return html, f"You took a class with {name}. Stop emails from {name}: {url}"
