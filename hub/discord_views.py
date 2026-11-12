"""Discord account-linking views.

Two entry points, both thin (CLAUDE.md: logic out of views) — they own only the HTTP
request/response and the CSRF ``state`` handshake; the OAuth + import orchestration lives
in :func:`membership.discord_sync.link_and_import`:

* ``/settings/discord/...`` — the **logged-in** connect/callback/disconnect (a member on
  their settings page). Its ``state`` is signed (:func:`_signed_state`) rather than kept in the
  session, because the Android app starts Connect in its WebView and Discord hands the callback
  to Chrome, a different cookie jar with the same signed-in member. Lands on the Notifications tab.
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
from django.core import signing
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from core.events import discord_oauth
from hub.views import _get_member

# Salt for the logged-in flow's signed ``state``, so no other signed value in the app passes for one.
_STATE_SALT = "hub.discord_connect.state"
# How long a signed ``state`` stays good: long enough to approve on Discord, short enough to go stale.
_STATE_MAX_AGE_SECONDS = 600
# Session key for the anon link flow's ``state``: it has no user to sign for.
_LINK_STATE_SESSION_KEY = "discord_link_state"


def _signed_state(user_pk: int | None) -> str:
    """A ``state`` naming the user who pressed Connect, plus a nonce, signed with the secret key."""
    return signing.dumps({"pk": user_pk, "nonce": secrets.token_urlsafe(16)}, salt=_STATE_SALT)


def _state_is_for(state: str, user_pk: int | None) -> bool:
    """Whether ``state`` is one we signed in the last ten minutes for this user, used for the first time.

    Checked without the session, so a callback that lands in another browser signed in as the
    same member still verifies. A forged, expired or other user's state is refused, and so is a
    second use: the nonce is recorded in the shared cache for as long as the state could verify.
    """
    try:
        payload = signing.loads(state, salt=_STATE_SALT, max_age=_STATE_MAX_AGE_SECONDS)
    except signing.BadSignature:
        return False
    if payload["pk"] != user_pk:
        return False
    return bool(cache.add(f"discord-connect-state:{payload['nonce']}", True, _STATE_MAX_AGE_SECONDS))


def _notifications_url() -> str:
    """The Notifications settings tab — where the logged-in linking outcome lands."""
    return f"{reverse('hub_user_settings')}?tab=notifications"


@login_required
def discord_connect(request: HttpRequest) -> HttpResponse:
    """Start the logged-in OAuth flow: sign a CSRF ``state`` for this user and redirect to Discord."""
    member = _get_member(request)
    if member is None:
        messages.error(request, "Your account is not linked to a membership.")
        return redirect("hub_user_settings")
    if not discord_oauth.is_configured():
        messages.error(request, "Connecting Discord isn't available right now. Please try again later.")
        return redirect(_notifications_url())
    state = _signed_state(request.user.pk)
    redirect_uri = request.build_absolute_uri(reverse("hub_discord_callback"))
    return redirect(discord_oauth.authorize_url(redirect_uri, state))


#: The flash after a logged-in connect. The guild import runs on the task queue, so the
#: request no longer knows the guilds (feedback request 15).
_LINKED_MESSAGE = (
    "Your Discord account is connected. Your guilds are being set up now; "
    "if you picked any in Discord, we'll email you the list."
)


@login_required
def discord_callback(request: HttpRequest) -> HttpResponse:
    """Finish the logged-in OAuth flow: verify ``state``, link, and import guilds."""
    from membership.discord_sync import LinkOutcome, link_and_import

    member = _get_member(request)
    if member is None:
        messages.error(request, "Your account is not linked to a membership.")
        return redirect("hub_user_settings")
    if request.GET.get("error"):
        messages.info(request, "Discord connection was cancelled.")
        return redirect(_notifications_url())
    state = request.GET.get("state", "")
    code = request.GET.get("code", "")
    if not code or not state or not _state_is_for(state, request.user.pk):
        messages.error(request, "We couldn't verify that Discord sign-in. Please try connecting again.")
        return redirect(_notifications_url())
    redirect_uri = request.build_absolute_uri(reverse("hub_discord_callback"))
    result = link_and_import(code, redirect_uri, member=member)
    if result.outcome is LinkOutcome.LINKED:
        messages.success(request, _LINKED_MESSAGE)
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
        return _render_landing(request, state="linked")
    if result.outcome is LinkOutcome.NEEDS_LOGIN:
        return _render_landing(request, state="needs_login")
    if result.outcome is LinkOutcome.ALREADY_LINKED_ELSEWHERE:
        return _render_landing(request, state="already_linked_elsewhere")
    if result.outcome is LinkOutcome.ACCOUNT_HAS_OTHER_DISCORD:
        return _render_landing(request, state="account_has_other_discord")
    return _render_landing(request, state="oauth_failed")


def _render_landing(request: HttpRequest, *, state: str) -> HttpResponse:
    """Render the anon link-outcome landing for one state (every state has a button)."""
    connect_next = reverse("hub_discord_connect")
    login_next_url = f"{reverse('account_login')}?next={connect_next}"
    signup_next_url = f"{reverse('account_signup')}?next={connect_next}"
    return render(
        request,
        "hub/discord_link_landing.html",
        {
            "state": state,
            "retry_url": reverse("hub_discord_link_start"),
            "login_url": reverse("account_login"),
            "login_next_url": login_next_url,
            "signup_next_url": signup_next_url,
            "guilds_url": f"{reverse('hub_user_settings')}?tab=guilds",
            "discord_settings_url": _notifications_url(),
            "all_guilds_url": reverse("hub_guild_directory"),
        },
    )
