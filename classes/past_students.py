"""Everyone who took an instructor's classes: the composer's My past students audience (#1042).

:func:`past_students` is the one roster: who the Recipients step lists, who the count counts and
who the send reaches. :func:`stop_line` is the line every such email ends with, linking to the no
login page that records a :class:`~classes.models.PastStudentOptOut`. :func:`featurable_classes`
and :class:`ClassCard` are the optional class card such an email carries under its message.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from django.contrib.auth.models import User

    from classes.models import ClassOffering, ClassOfferingQuerySet
    from core.email_prefs import StopLink
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


def stop_line(instructor: Member, stop_link: StopLink) -> tuple[str, str]:
    """The ``(html, text)`` line that ends every past students email from ``instructor``.

    "You took a class with <name>. Stop emails from <name>" (:func:`instructor_name`), linking to
    the no login stop page at ``stop_link``'s placeholder, which ``core.email.send`` fills with a
    token of the instructor and each recipient's own address
    (:func:`core.email_prefs.finalize_stop_link`), so one built message serves every recipient.

    Args:
        instructor: The instructor sending; their emails are the ones the link stops.
        stop_link: The message's stop link, built for ``instructor``.
    """
    from django.utils.html import escape

    name = instructor_name(instructor)
    url = stop_link.url
    html = (
        '<p style="margin:28px 0 0;padding-top:14px;border-top:1px solid #e3e7ec;color:#5b6b78;'
        'font-size:13px;line-height:1.5;" data-past-students-stop>'
        f'You took a class with {escape(name)}. <a href="{escape(url)}" style="color:#0d4876;">'
        f"Stop emails from {escape(name)}</a></p>"
    )
    return html, f"You took a class with {name}. Stop emails from {name}: {url}"


def featurable_classes(instructor: Member) -> ClassOfferingQuerySet:
    """The classes ``instructor`` may feature as a card: published, and with an upcoming session or flexible scheduling.

    One query, title order. A class counts when the instructor has an instructors row on it.
    """
    from django.db.models import Q
    from django.utils import timezone

    from classes.models import ClassOffering

    return (
        ClassOffering.objects.for_instructor(instructor)
        .filter(status=ClassOffering.Status.PUBLISHED)
        .filter(Q(scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE) | Q(sessions__starts_at__gte=timezone.now()))
        .distinct()
        .order_by("title")
    )


@dataclass(frozen=True)
class ClassCard:
    """A class as the card in a past students email shows it (#1042): what the email partial reads.

    ``when`` is the next session, or "Flexible scheduling"; ``price`` the sale price while a
    sale runs; ``image_url`` and ``url`` are absolute, as an email needs.
    """

    title: str
    blurb: str
    when: str
    price: str
    image_url: str
    url: str

    @classmethod
    def for_offering(cls, offering: ClassOffering) -> ClassCard:
        """Read the card off ``offering`` now: its photo, short description, next date, price and page."""
        from django.urls import reverse
        from django.utils import timezone
        from django.utils.formats import date_format

        from classes.templatetags.classes_tags import cents_as_price
        from core.urls_util import book_absolute_url, member_absolute_url

        if offering.is_flexible:
            when = "Flexible scheduling"
        else:
            upcoming = offering.sessions.filter(starts_at__gte=timezone.now()).order_by("starts_at").first()
            when = (
                date_format(timezone.localtime(upcoming.starts_at), "l, F j \\a\\t g:i A")
                if upcoming is not None
                else "Upcoming dates TBA"
            )
        image_url = offering.card_photo_url
        return cls(
            title=offering.title,
            blurb=offering.seo_description,
            when=when,
            price=cents_as_price(offering.sale_price_cents),
            image_url=member_absolute_url(image_url),
            url=book_absolute_url(reverse("classes:public_class_detail", kwargs={"slug": offering.slug})),
        )

    def html(self) -> str:
        """The card for the email's HTML part (``membership/emails/_class_card.html``)."""
        from django.template.loader import render_to_string

        return render_to_string("membership/emails/_class_card.html", {"card": self})

    def text(self) -> str:
        """The card for the email's text part: title, when and price, the description, the Register link."""
        return f"{self.title}\n{self.when} · {self.price}\n{self.blurb}\nRegister: {self.url}"
