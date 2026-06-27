"""Discord account-linking views (``/settings/discord/...``).

Members link their Discord account so the per-member Discord DM channel
(:mod:`core.events.discord_dm`) can reach them. These views are thin (CLAUDE.md:
logic out of views) — they own only the HTTP request/response and the CSRF ``state``
handshake. The OAuth orchestration (authorize URL, code exchange, identity fetch)
lives in the :mod:`core.events.discord_oauth` service; recording the link lives on
:meth:`membership.models.Member.link_discord` / ``unlink_discord``.

The callback redirect URI MUST exactly match the one registered on the Discord
application: ``<scheme>://<host>/settings/discord/callback/`` (built here with
``request.build_absolute_uri(reverse("hub_discord_callback"))``).
"""

from __future__ import annotations

import secrets

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_POST

from core.events import discord_oauth
from hub.views import _get_member

# Session key holding the per-flow CSRF ``state`` token (verified on the callback).
_STATE_SESSION_KEY = "discord_oauth_state"


def _notifications_url() -> str:
    """The Notifications settings tab — where every linking outcome lands."""
    return f"{reverse('hub_user_settings')}?tab=notifications"


@login_required
def discord_connect(request: HttpRequest) -> HttpResponse:
    """Start the OAuth flow: store a CSRF ``state`` and redirect to Discord's authorize page."""
    member = _get_member(request)
    if member is None:
        messages.error(request, "Your account is not linked to a membership.")
        return redirect("hub_user_settings")
    if not discord_oauth.is_configured():
        messages.error(request, "Connecting Discord isn't available right now. Please try again later.")
        return redirect(_notifications_url())
    state = secrets.token_urlsafe(32)
    request.session[_STATE_SESSION_KEY] = state
    redirect_uri = request.build_absolute_uri(reverse("hub_discord_callback"))
    return redirect(discord_oauth.authorize_url(redirect_uri, state))


@login_required
def discord_callback(request: HttpRequest) -> HttpResponse:
    """Finish the OAuth flow: verify ``state``, exchange the code, and store the link."""
    member = _get_member(request)
    if member is None:
        messages.error(request, "Your account is not linked to a membership.")
        return redirect("hub_user_settings")
    expected_state = request.session.pop(_STATE_SESSION_KEY, None)
    if request.GET.get("error"):
        messages.info(request, "Discord connection was cancelled.")
        return redirect(_notifications_url())
    state = request.GET.get("state", "")
    code = request.GET.get("code", "")
    if not code or not state or not expected_state or state != expected_state:
        messages.error(request, "We couldn't verify that Discord sign-in. Please try connecting again.")
        return redirect(_notifications_url())
    redirect_uri = request.build_absolute_uri(reverse("hub_discord_callback"))
    try:
        discord_oauth.link_member_from_code(member, code, redirect_uri)
    except discord_oauth.DiscordOAuthError:
        messages.error(request, "We couldn't connect your Discord account. Please try again.")
        return redirect(_notifications_url())
    messages.success(request, "Your Discord account is connected — you can now get notifications as a DM.")
    return redirect(_notifications_url())


@login_required
@require_POST
def discord_disconnect(request: HttpRequest) -> HttpResponse:
    """Clear the member's Discord link (they stop receiving DMs)."""
    member = _get_member(request)
    if member is None:
        messages.error(request, "Your account is not linked to a membership.")
        return redirect("hub_user_settings")
    member.unlink_discord()
    messages.success(request, "Your Discord account has been disconnected.")
    return redirect(_notifications_url())
