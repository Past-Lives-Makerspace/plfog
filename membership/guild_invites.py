"""Inviting members to join a guild (#1137).

A guild's lead, its staff and admins (``membership.permissions.can_invite_to_guild``) pick
members from the Invite panel on the guild page; :func:`send_invites` opens one
:class:`~membership.models.GuildInvite` per person and, once that commits, tells each one with a
bell notice and an email (``guild.invited``). Nobody joins by being invited: the invitee taps
Join on the guild page's invite banner, which runs the hero Join's path with ``source="invite"``
(``hub.views.guild_invite_accept``), and :meth:`Member.subscribe_to_guild` closes the invite.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING, Any, cast

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import QuerySet
from django.urls import reverse

if TYPE_CHECKING:
    from collections.abc import Iterable

    from django.contrib.auth.models import User

    from membership.models import Guild, GuildInvite, Member

INVITED_EVENT = "guild.invited"


@dataclass
class InviteResult:
    """What one Send Invites did: who was invited now, and who was skipped as already in or already invited."""

    sent: list[Member] = field(default_factory=list)
    skipped: list[Member] = field(default_factory=list)

    def toast(self) -> str:
        """The inviter's toast: how many invites went out, and why any did not."""
        sent = len(self.sent)
        noun = "invite" if sent == 1 else "invites"
        if not self.skipped:
            return f"Sent {sent} {noun}."
        skipped = len(self.skipped)
        people = "person was" if skipped == 1 else "people were"
        if not sent:
            return f"No invites sent. {skipped} {people} already in the guild or already invited."
        return f"Sent {sent} {noun}. {skipped} {people} already in the guild or already invited."


def inviteable_members() -> QuerySet[Member]:
    """Every member someone may be invited as: active, with a live account, and not hidden from the directory.

    The same people the composer's Add people offers (``hub.forms.announcement_add_member_choices``),
    limited to active memberships, since only an active member can join a guild.
    """
    from membership.models import Member

    return Member.objects.filter(status=Member.Status.ACTIVE, user__isnull=False, user__is_active=True).exclude(
        hide_from_directory=True
    )


def invite_candidates(guild: Guild) -> list[Member]:
    """The Invite panel's list: :func:`inviteable_members` less the guild's members and its open invitees.

    Ordered by display name, case blind, as the composer's Add people list is. One query.
    """
    from membership.models import GuildInvite

    open_invitees = GuildInvite.objects.open().filter(guild=guild).values("member_id")
    members = (
        inviteable_members()
        .exclude(guild_memberships__guild=guild)
        .exclude(pk__in=open_invitees)
        .select_related("user")
    )
    return sorted(members, key=lambda member: (member.display_name.casefold(), member.pk))


def candidate_rows(guild: Guild) -> list[tuple[int, str]]:
    """``(member pk, "<name> · <email>")`` for each of :func:`invite_candidates`, the composer's label."""
    from membership.names import user_label

    # invite_candidates admits only members with an account, so ``user`` is set on every row.
    return [(member.pk, user_label(cast("User", member.user))) for member in invite_candidates(guild)]


def banner_invite(guild: Guild, member: Member, *, is_member: bool) -> GuildInvite | None:
    """The open invite the guild page's banner offers ``member``, or None.

    One query. An invitee who already joined some other way (a Discord reaction, the
    ``/join-guild`` command) sees no banner, and their invite is closed here.
    """
    from django.utils import timezone

    from membership.models import GuildInvite

    invite = GuildInvite.objects.open().filter(guild=guild, member=member).select_related("invited_by").first()
    if invite is not None and is_member:
        GuildInvite.objects.filter(pk=invite.pk, accepted_at__isnull=True).update(accepted_at=timezone.now())
        return None
    return invite


def send_invites(guild: Guild, members: Iterable[Member], invited_by: Member | None) -> InviteResult:
    """Open one invite per member not yet in ``guild`` and not already invited to it, and tell each after commit.

    Safe to repeat: a member already in the guild, or with an open invite to it, is skipped, and
    ``uq_guildinvite_open`` turns a concurrent second send into a skip too.

    Args:
        guild: The guild to invite them to.
        members: The members picked.
        invited_by: Who sent the invites (None when the sender has no linked member).

    Returns:
        Who was invited now and who was skipped.
    """
    from membership.models import GuildInvite

    result = InviteResult()
    member_ids = set(guild.memberships.values_list("member_id", flat=True))
    invited_ids = set(GuildInvite.objects.open().filter(guild=guild).values_list("member_id", flat=True))
    for member in dict.fromkeys(members):
        if member.pk in member_ids or member.pk in invited_ids:
            result.skipped.append(member)
            continue
        try:
            with transaction.atomic():
                invite = GuildInvite.objects.create(guild=guild, member=member, invited_by=invited_by)
        except IntegrityError:
            result.skipped.append(member)
            continue
        invited_ids.add(member.pk)
        result.sent.append(member)
        transaction.on_commit(partial(notify_invited, invite), robust=True)
    return result


def _absolute_url(path: str) -> str:
    return f"{settings.MEMBER_BASE_URL.rstrip('/')}{path}"


def guild_page_path(invite: GuildInvite) -> str:
    """Where the bell and the email's Join land: the guild page, whose banner offers Join."""
    return reverse("hub_guild_detail", args=[invite.guild.slug])


def invited_subject(invite: GuildInvite) -> str:
    """The email subject and the bell title, worded as the banner is."""
    if invite.invited_by is None:
        return f"You're invited to join {invite.guild.name}"
    return f"{invite.invited_by.display_name} invited you to join {invite.guild.name}"


def email_context(invite: GuildInvite) -> dict[str, Any]:
    """What ``membership/emails/guild_invite.{txt,html}`` reads."""
    return {
        "first_name": invite.member.display_name.split(" ")[0],
        "headline": invited_subject(invite),
        "inviter_name": invite.invited_by.display_name if invite.invited_by is not None else "",
        "guild": invite.guild,
        "join_url": _absolute_url(guild_page_path(invite)),
    }


def notify_invited(invite: GuildInvite) -> None:
    """``guild.invited``: one bell notice and one email to the invitee, once per invite."""
    from core.events.senders import emit_with_email_shell

    subject = invited_subject(invite)
    emit_with_email_shell(
        INVITED_EVENT,
        actor=invite.invited_by,
        target=invite.guild,
        context={"user": invite.member.user},
        subject=subject,
        text_template="membership/emails/guild_invite.txt",
        html_template="membership/emails/guild_invite.html",
        template_context=email_context(invite),
        in_app_title=subject,
        in_app_body=f"Open {invite.guild.name} and tap Join to become a member.",
        url=guild_page_path(invite),
        period=f"guild_invite:{invite.pk}",
    )
