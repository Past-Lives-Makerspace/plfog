"""The guild page's Invite panel and the invite banner's Join (#1137).

The panel (`hub_guild_invite_panel`, GET) and its Send Invites (`hub_guild_invite_send`, POST)
open for whoever ``membership.permissions.can_invite_to_guild`` admits: the guild's lead, its
staff and admins, on a guild members can see. Anyone else gets a 404 and nothing is sent. The
banner's Join (`hub_guild_invite_accept`, POST) accepts an invite for its own invitee only, and
joins through :func:`hub.views.join_guild_from_page`, the hero Join's path.
"""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET, require_POST

from hub.forms import GuildInviteForm
from hub.toast import trigger_client_event, trigger_toast
from hub.views import _can_view_guild, _get_member, join_guild_from_page, joined_guild_response
from membership import guild_invites
from membership.models import Guild, GuildInvite, GuildMembership
from membership.permissions import can_invite_to_guild

# The client event a send that went out fires; the Invite button closes the modal on it. htmx hands a
# non object payload over as {value: ...}, so the modal's own close-modal check could not match it.
SENT_EVENT = "guild-invites-sent"


def _invite_guild(request: HttpRequest, pk: int) -> Guild:
    """The guild named in the URL, when this request may invite to it; else a 404."""
    guild = get_object_or_404(Guild, pk=pk)
    if not can_invite_to_guild(request, guild):
        raise Http404
    return guild


def _panel(request: HttpRequest, guild: Guild) -> HttpResponse:
    return render(
        request,
        "hub/partials/_guild_invite_panel.html",
        {"guild": guild, "candidates": guild_invites.candidate_rows(guild)},
    )


@login_required
@require_GET
def hub_guild_invite_panel(request: HttpRequest, pk: int) -> HttpResponse:
    """The Invite panel's body, loaded into the ``guild-invite`` modal: every member not in the guild or invited."""
    return _panel(request, _invite_guild(request, pk))


@login_required
@require_POST
def hub_guild_invite_send(request: HttpRequest, pk: int) -> HttpResponse:
    """Send Invites: one open invite and one notice per member picked, then the panel again with a toast.

    Nothing picked answers the panel with an error toast and sends nothing. A send that went
    out fires ``guild-invites-sent``, on which the Invite button closes the modal.
    """
    guild = _invite_guild(request, pk)
    form = GuildInviteForm(request.POST)
    if not form.is_valid():
        response = _panel(request, guild)
        trigger_toast(response, form.error_summary(), "error")
        return response
    result = guild_invites.send_invites(guild, form.cleaned_data["members"], _get_member(request))
    response = _panel(request, guild)
    trigger_toast(response, result.toast(), "success" if result.sent else "info")
    if result.sent:
        trigger_client_event(response, SENT_EVENT)
    return response


@login_required
@require_POST
def hub_guild_invite_accept(request: HttpRequest, pk: int) -> HttpResponse:
    """The invite banner's Join: the invitee joins through the hero Join's path, with ``source="invite"``.

    Only the invite's own member may accept it, and only while it is open; anyone else, or a
    closed invite, gets a 404. One tap, as the hero modal's defaults are: the welcome email when
    the guild sends one, no Discord hello. An invitee who is already a member just gets the
    page's member state back.
    """
    member = _get_member(request)
    invite = get_object_or_404(GuildInvite.objects.open().select_related("guild"), pk=pk)
    if member is None or invite.member_id != member.pk:
        raise Http404
    guild = invite.guild
    if not _can_view_guild(request, guild):
        raise Http404
    _joined, welcomed = join_guild_from_page(
        guild,
        member,
        source=GuildMembership.Source.INVITE,
        send_welcome=True,
        announce_discord=False,
    )
    return joined_guild_response(request, guild, member, welcomed=welcomed)
