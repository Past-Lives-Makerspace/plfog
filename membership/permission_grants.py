"""Telling a member about a permission someone else turned on for them (#1163).

Each grant method (:meth:`Member.sync_admin_capabilities`, :meth:`Member.grant_instructor`,
:meth:`Member.apply_admin_role`, :meth:`Guild.set_class_reviewer`) works out what it newly
turned on and hands that to :func:`record`, which enqueues one :func:`send_permission_granted`
task per permission after the save commits. Each task rings the bell and sends one email about
its permission: what it lets them do, how, and a button per page, worded from
:attr:`AdminCapability.GUIDES`. A save that turns on two sends two emails (Felix: each email
separate). A grant a member makes to themself sends nothing.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from django.tasks import task
from django.urls import reverse

from core.tasks import enqueue_on_commit
from core.urls_util import member_absolute_url

if TYPE_CHECKING:
    from django.contrib.auth.models import User

    from membership.models import GuidePage, Guild, Member, PermissionGuide

PERMISSION_GRANTED_EVENT = "permission.granted"


def record(member: Member, grants: Sequence[str], *, granted_by: User | None, guild: Guild | None = None) -> None:
    """Email ``member`` once this save commits, one email per permission in ``grants`` (newly turned on).

    Nothing is sent for an empty list or when ``granted_by`` is the member themself (the View As
    toggles, their own edit page). ``guild`` is the guild a Class Reviewer grant is for.

    Raises:
        KeyError: A grant has no entry in :attr:`AdminCapability.GUIDES`.
    """
    from membership.models import AdminCapability

    if not grants or (granted_by is not None and granted_by.pk == member.user_id):
        return
    for grant in grants:
        AdminCapability.GUIDES[grant]  # fail at the save, not in the worker
    for grant in grants:
        enqueue_on_commit(
            send_permission_granted,
            member.pk,
            grant,
            granted_by.pk if granted_by is not None else None,
            guild.pk if guild is not None else None,
            uuid.uuid4().hex,
        )


def _page_url(page: GuidePage) -> str:
    """An email-ready link to one of a guide's pages."""
    return member_absolute_url(f"{reverse(page.url_name)}{page.query}")


def granted_subject(grant: str, guild: Guild | None) -> str:
    """The email subject and the bell title: ``You have been granted Billing Administrator``."""
    return f"You have been granted {card_title(grant, guild)}"


def card_title(grant: str, guild: Guild | None) -> str:
    """A grant's name as the email shows it; a Class Reviewer card names its guild."""
    from membership.models import AdminCapability

    title = AdminCapability.GUIDES[grant].title
    if grant == AdminCapability.CLASS_REVIEWER:
        if guild is None:
            raise ValueError("A Class Reviewer grant needs its guild")
        return f"{title} for {guild.name}"
    return title


def _card(grant: str, guide: PermissionGuide, guild: Guild | None) -> dict[str, Any]:
    return {
        "title": card_title(grant, guild),
        "what": guide.what,
        "how": guide.how,
        "pages": [(page.label, _page_url(page)) for page in guide.pages],
    }


def email_context(member: Member, grant: str, granter: Member | None, guild: Guild | None) -> dict[str, Any]:
    """What ``membership/emails/permission_granted.{txt,html}`` reads.

    ``granter`` is whoever turned it on; None (a superuser with no member record) reads "An admin".
    """
    from membership.models import AdminCapability

    return {
        "first_name": member.display_name.split(" ")[0],
        "headline": granted_subject(grant, guild),
        "granter_name": granter.display_name if granter is not None else "An admin",
        "help_contact": granter.display_name if granter is not None else "an admin",
        "card": _card(grant, AdminCapability.GUIDES[grant], guild),
        "portal_url": member_absolute_url(reverse("hub_home")),
    }


@task
def send_permission_granted(
    member_id: int, grant: str, granted_by_id: int | None, guild_id: int | None, batch: str
) -> None:
    """``permission.granted``: the bell and a forced email to the member about one permission.

    Safe to run twice: ``batch`` is this grant's own token, so the spine's delivery ledger drops a
    second run. A member with no account still gets the email at their staged address.
    """
    from django.contrib.auth.models import User

    from core.events.senders import emit_with_email_shell
    from membership.models import Guild, Member

    member = Member.objects.select_related("user").get(pk=member_id)
    granted_by = User.objects.get(pk=granted_by_id) if granted_by_id is not None else None
    granter = Member.objects.filter(user=granted_by).first() if granted_by is not None else None
    guild = Guild.objects.get(pk=guild_id) if guild_id is not None else None
    subject = granted_subject(grant, guild)
    context = email_context(member, grant, granter, guild)
    emit_with_email_shell(
        PERMISSION_GRANTED_EVENT,
        actor=granted_by,
        target=member,
        context={"user": member.user},
        subject=subject,
        text_template="membership/emails/permission_granted.txt",
        html_template="membership/emails/permission_granted.html",
        template_context=context,
        in_app_title=subject,
        in_app_body="What it lets you do and where to find it.",
        url=context["card"]["pages"][0][1],
        email_to=member.primary_email or None,
        period=f"permission_granted:{batch}",
    )
