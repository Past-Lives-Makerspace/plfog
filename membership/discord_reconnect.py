"""Asking members who linked Discord but are not on the server to reconnect (#1239).

Before #1138 Connect Discord saved the link and never added anyone to the server. Joining needs
the member's own ``guilds.join`` token, which only a new connect obtains, so the app cannot add
them itself. ``manage.py nudge_discord_reconnect`` reads the server's member list once, finds
the Active members with a linked Discord account missing from it, and with ``--send`` opens one
:class:`~membership.models.DiscordReconnectNudge` each and tells them by bell and email
(``membership.discord_reconnect``). Home shows the open nudge under Needs Attention until they
dismiss it or reconnect (``link_and_import`` closes it when ``put_on_server`` succeeds).

Only linked members are ever looked at: nobody on the server is matched to an unlinked member.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.db import transaction
from django.tasks import task
from django.urls import reverse

from core.tasks import enqueue_on_commit

if TYPE_CHECKING:
    from django.db.models import QuerySet

    from membership.models import DiscordReconnectNudge, Member

NUDGE_EVENT = "membership.discord_reconnect"
SUBJECT = "Reconnect Discord to join the Past Lives server"


class ServerListUnavailableError(Exception):
    """The server's member list cannot be read, so nobody can be told apart and nobody is nudged."""


@dataclass
class NudgeRun:
    """What one run found: who is missing and not yet nudged, and who was nudged before."""

    missing: list[Member]
    already_nudged: list[Member]


def server_member_ids() -> set[str]:
    """Every Discord user id on the Past Lives server, read once.

    Raises:
        ServerListUnavailableError: When the server id or bot token is blank (the list would read
            as empty and every linked member would look missing), or a page could not be read.
    """
    from core.events.discord_dm import bot_disabled
    from core.events.discord_roles import server_members
    from core.models import SiteConfiguration

    server_id = SiteConfiguration.load().discord_server_id.strip()
    if not server_id:
        raise ServerListUnavailableError("The Discord server id is blank in Site Settings.")
    if bot_disabled("reconnect nudge"):
        raise ServerListUnavailableError("The Discord bot token is blank here (unset, or staging).")
    members = server_members(server_id, retry=True)
    if members is None:
        raise ServerListUnavailableError("Could not read the Discord server's member list; see the log for why.")
    return set(members)


def linked_off_server(on_server: set[str]) -> QuerySet[Member]:
    """Active members with a linked Discord account whose id is not in ``on_server``, nudge loaded."""
    from membership.models import Member

    return (
        Member.objects.filter(status=Member.Status.ACTIVE)
        .exclude(discord_user_id="")
        .exclude(discord_user_id__in=on_server)
        .select_related("discord_reconnect_nudge")
        .order_by("pk")
    )


def find_missing(on_server: set[str]) -> NudgeRun:
    """Split the linked members missing from the server into those to nudge and those nudged before."""
    from membership.models import DiscordReconnectNudge

    run = NudgeRun(missing=[], already_nudged=[])
    for member in linked_off_server(on_server):
        try:
            member.discord_reconnect_nudge
        except DiscordReconnectNudge.DoesNotExist:
            run.missing.append(member)
        else:
            run.already_nudged.append(member)
    return run


def send_nudges(members: list[Member]) -> list[DiscordReconnectNudge]:
    """Open a nudge for each member and tell each one once the transaction commits."""
    from membership.models import DiscordReconnectNudge

    nudges: list[DiscordReconnectNudge] = []
    with transaction.atomic():
        for member in members:
            nudge = DiscordReconnectNudge.objects.create(member=member)
            enqueue_on_commit(notify_nudged, nudge.pk)
            nudges.append(nudge)
    return nudges


def reconnect_path() -> str:
    """The connect flow the Home button, the bell and the email all start."""
    return reverse("hub_discord_connect")


def email_context(member: Member) -> dict[str, Any]:
    """What ``membership/emails/discord_reconnect.{txt,html}`` reads."""
    return {
        "first_name": member.display_name.split(" ")[0],
        "headline": SUBJECT,
        "reconnect_url": f"{settings.MEMBER_BASE_URL.rstrip('/')}{reconnect_path()}",
    }


@task
def notify_nudged(nudge_id: int) -> None:
    """``membership.discord_reconnect``: one bell notice and one email with the Reconnect Discord button.

    Safe to run twice: the period is the nudge's own, so the delivery ledger drops a second send.
    A nudge closed before this runs (the member already reconnected or dismissed it) sends nothing.
    """
    from core.events.senders import emit_with_email_shell
    from membership.models import DiscordReconnectNudge

    nudge = DiscordReconnectNudge.objects.select_related("member__user").get(pk=nudge_id)
    if nudge.status != DiscordReconnectNudge.Status.OPEN:
        return
    member = nudge.member
    emit_with_email_shell(
        NUDGE_EVENT,
        target=member,
        context={"user": member.user},
        subject=SUBJECT,
        text_template="membership/emails/discord_reconnect.txt",
        html_template="membership/emails/discord_reconnect.html",
        template_context=email_context(member),
        in_app_title=SUBJECT,
        in_app_body="You're linked but not on the server yet. Reconnect once and we'll add you with your roles.",
        url=reconnect_path(),
        email_to=member.primary_email or None,
        period=f"discord_reconnect:{nudge.pk}",
    )
