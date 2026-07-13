"""Discord account-linking views.

Two entry points, both thin (CLAUDE.md: logic out of views) — they own only the HTTP
request/response and the CSRF ``state`` handshake; the OAuth + import orchestration lives
in :func:`membership.discord_sync.link_and_import`:

* ``/settings/discord/...`` — the **logged-in** connect/callback/disconnect (a member on
  their settings page). State key ``discord_oauth_state``; lands on the Notifications tab.
* ``/discord/link/`` + ``/discord/link/callback/`` — the **anon-allowed** low-friction link
  posted *in Discord*: click once, and if your verified Discord email matches your Past
  Lives account you're linked and your guilds are set up, no FOG login required. State key
  ``discord_link_state`` (distinct so the two flows never collide); renders a full-page
  outcome landing.

Each callback's redirect URI MUST exactly match one registered on the Discord application:
``/settings/discord/callback/`` and ``/discord/link/callback/`` respectively.
"""

from __future__ import annotations

import secrets

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from core.events import discord_oauth
from hub.views import _get_member

# Session key holding the logged-in flow's CSRF ``state`` token (verified on the callback).
_STATE_SESSION_KEY = "discord_oauth_state"
# A DISTINCT key for the anon link flow, so two in-flight flows never overwrite each other.
_LINK_STATE_SESSION_KEY = "discord_link_state"


def _notifications_url() -> str:
    """The Notifications settings tab — where the logged-in linking outcome lands."""
    return f"{reverse('hub_user_settings')}?tab=notifications"


@login_required
def discord_connect(request: HttpRequest) -> HttpResponse:
    """Start the logged-in OAuth flow: store a CSRF ``state`` and redirect to Discord."""
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
    """Finish the logged-in OAuth flow: verify ``state``, link, and import guilds."""
    from membership.discord_sync import LinkOutcome, link_and_import

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
    result = link_and_import(code, redirect_uri, member=member)
    if result.outcome is LinkOutcome.LINKED:
        if result.guilds:
            messages.success(
                request,
                f"Your Discord account is connected — we set you up in {len(result.guilds)} "
                f"guild{'' if len(result.guilds) == 1 else 's'}.",
            )
        else:
            messages.success(request, "Your Discord account is connected — you can now get notices as a DM.")
    elif result.outcome is LinkOutcome.ALREADY_LINKED_ELSEWHERE:
        messages.error(request, "That Discord account is already connected to a different Past Lives account.")
    elif result.outcome is LinkOutcome.ACCOUNT_HAS_OTHER_DISCORD:
        messages.error(
            request, "Your account already has a different Discord connected — disconnect it first to switch."
        )
    else:  # OAUTH_FAILED (NEEDS_LOGIN can't happen on the logged-in path).
        messages.error(request, "We couldn't connect your Discord account. Please try again.")
    return redirect(_notifications_url())


@login_required
@require_POST
def discord_disconnect(request: HttpRequest) -> HttpResponse:
    """Clear the member's Discord link (they stop receiving DMs and the two-way guild sync)."""
    member = _get_member(request)
    if member is None:
        messages.error(request, "Your account is not linked to a membership.")
        return redirect("hub_user_settings")
    member.unlink_discord()
    messages.success(request, "Your Discord account has been disconnected.")
    return redirect(_notifications_url())


def discord_link_start(request: HttpRequest) -> HttpResponse:
    """Start the anon-allowed low-friction link: store ``state`` and redirect to Discord.

    Not ``@login_required`` — this is the link posted *in Discord*. If Discord linking is
    unconfigured, land on the friendly retry (never a 500).
    """
    if not discord_oauth.is_configured():
        return _render_landing(request, state="oauth_failed")
    state = secrets.token_urlsafe(32)
    request.session[_LINK_STATE_SESSION_KEY] = state
    redirect_uri = request.build_absolute_uri(reverse("hub_discord_link_callback"))
    return redirect(discord_oauth.authorize_url(redirect_uri, state))


def discord_link_callback(request: HttpRequest) -> HttpResponse:
    """Finish the anon-allowed link: verify ``state``, link + import, render the outcome.

    A dropped session cookie (Discord's in-app browser) or a denied consent lands on a
    calm retry / cancelled screen — never a 500.
    """
    from membership.discord_sync import LinkOutcome, link_and_import

    expected_state = request.session.pop(_LINK_STATE_SESSION_KEY, None)
    if request.GET.get("error"):
        return _render_landing(request, state="cancelled")
    state = request.GET.get("state", "")
    code = request.GET.get("code", "")
    if not code or not state or not expected_state or state != expected_state:
        return _render_landing(request, state="oauth_failed")
    redirect_uri = request.build_absolute_uri(reverse("hub_discord_link_callback"))
    member = _get_member(request)  # set when the visitor happens to be logged in, else None
    result = link_and_import(code, redirect_uri, member=member)
    if result.outcome is LinkOutcome.LINKED:
        return _render_landing(request, state="linked", guilds=result.guilds, complete=result.complete)
    if result.outcome is LinkOutcome.NEEDS_LOGIN:
        return _render_landing(request, state="needs_login")
    if result.outcome is LinkOutcome.ALREADY_LINKED_ELSEWHERE:
        return _render_landing(request, state="already_linked_elsewhere")
    if result.outcome is LinkOutcome.ACCOUNT_HAS_OTHER_DISCORD:
        return _render_landing(request, state="account_has_other_discord")
    return _render_landing(request, state="oauth_failed")


def _render_landing(
    request: HttpRequest,
    *,
    state: str,
    guilds: list | None = None,
    complete: bool = True,
) -> HttpResponse:
    """Render the anon link-outcome landing for one state (every state has a button)."""
    guild_links = [{"name": g.name, "url": reverse("hub_guild_detail", args=[g.slug])} for g in (guilds or [])]
    connect_next = reverse("hub_discord_connect")
    login_next_url = f"{reverse('account_login')}?next={connect_next}"
    signup_next_url = f"{reverse('account_signup')}?next={connect_next}"
    return render(
        request,
        "hub/discord_link_landing.html",
        {
            "state": state,
            "guilds": guild_links,
            "complete": complete,
            "retry_url": reverse("hub_discord_link_start"),
            "login_url": reverse("account_login"),
            "login_next_url": login_next_url,
            "signup_next_url": signup_next_url,
            "guilds_url": f"{reverse('hub_user_settings')}?tab=guilds",
            "discord_settings_url": _notifications_url(),
            "all_guilds_url": reverse("hub_guild_directory"),
        },
    )
