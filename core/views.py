"""Core app views for PWA push notification infrastructure."""

import json
import logging
from pathlib import Path
from typing import cast

from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404, HttpRequest, HttpResponse, HttpResponsePermanentRedirect, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

# ── Cross-surface session relay ────────────────────────────────────────────────
# Django's SESSION_COOKIE_DOMAIN=".localhost" is unreliable: browsers treat
# `localhost` as a public suffix and scope the cookie to the exact host.  The
# relay is a fallback: the book surface sends unauthenticated users to the
# members surface, which checks for an existing session and issues a short-lived
# signed token.  The book surface accepts the token and logs the user in locally.
#
# Security:
#   • Tokens are HMAC-signed via Django's signing module (SECRET_KEY + salt).
#   • max_age=30 means tokens expire in 30 seconds.
#   • book_host is validated against PUBLIC_HOSTS to prevent open-relay abuse.
#   • next is validated as a relative path to prevent open-redirect via tokens.

from allauth.account.internal.stagekit import clear_login

from .forms import FindAccountForm, NewsletterSignupForm
from .models import FcmDevice, PushSubscription, SiteActivity, TransactionalEmailLog

logger = logging.getLogger(__name__)


def relay_issue(request: HttpRequest) -> HttpResponse:
    """Issue a short-lived session relay token for cross-surface SSO.

    Called by the book surface when a member needs to authenticate.  If the
    requesting user is already logged in here, signs a 30-second token and
    redirects to the book surface relay-accept endpoint.  If not logged in,
    redirects to the book surface login page directly so the user can sign in
    without a second bounce.

    GET params:
        book_host  — full host[:port] of the book surface (validated against PUBLIC_HOSTS)
        next       — relative path to land on after accepting the relay
    """
    from urllib.parse import urlencode

    from django.core import signing

    from urllib.parse import urlsplit

    from django.utils.http import url_has_allowed_host_and_scheme

    book_host = request.GET.get("book_host", "")
    next_path = request.GET.get("next", "/")

    # Parse and validate book_host via urlsplit so userinfo (@) and path
    # components can't smuggle a different hostname past the allowlist check.
    parsed = urlsplit("//" + book_host)
    book_host_bare = (parsed.hostname or "").lower()
    public_hosts: set[str] = set(getattr(settings, "PUBLIC_HOSTS", []))
    if not book_host_bare or book_host_bare not in public_hosts or "@" in book_host or "/" in book_host:
        return redirect("account_login")

    if not url_has_allowed_host_and_scheme(next_path, allowed_hosts={book_host}):
        next_path = "/"

    scheme = "https" if request.is_secure() else "http"

    if not request.user.is_authenticated:
        return redirect(f"{scheme}://{book_host}/accounts/login/?next={next_path}")

    token = signing.dumps(request.user.pk, salt="session-relay")
    accept_url = f"{scheme}://{book_host}/auth/relay/accept/?{urlencode({'token': token, 'next': next_path})}"
    return redirect(accept_url)


def relay_accept(request: HttpRequest) -> HttpResponse:
    """Accept a relay token from the members surface and log the user in.

    Validates the signed token (max 30 seconds old), retrieves the matching
    user, logs them in via allauth's backend, and redirects to next_path.
    Falls back to the local login page on any error.
    """
    from django.contrib.auth import get_user_model
    from django.contrib.auth import login as auth_login
    from django.core import signing

    User = get_user_model()
    from django.utils.http import url_has_allowed_host_and_scheme

    token = request.GET.get("token", "")
    next_path = request.GET.get("next", "/")

    if not url_has_allowed_host_and_scheme(next_path, allowed_hosts={request.get_host()}):
        next_path = "/"

    try:
        user_pk = signing.loads(token, salt="session-relay", max_age=30)
    except (signing.SignatureExpired, signing.BadSignature):
        return redirect(f"/accounts/login/?next={next_path}")

    try:
        user = User.objects.get(pk=user_pk)
    except User.DoesNotExist:
        return redirect(f"/accounts/login/?next={next_path}")

    auth_login(request, user, backend="allauth.account.auth_backends.AuthenticationBackend")
    return redirect(next_path)


def health_check(request):
    """Health check endpoint."""
    return JsonResponse({"status": "ok"})


@csrf_exempt
@require_POST
def discord_interactions(request: HttpRequest) -> HttpResponse:
    """Discord Interactions Endpoint URL — the slash-command platform's single POST view.

    Discord POSTs here for every interaction. Signature verification runs FIRST (before
    JSON parse, member lookup, or any handler); a bad or missing signature is the only
    non-2xx we ever return (``401`` — Discord requires it and probes for it). A PING is
    answered with a PONG; an APPLICATION_COMMAND is dispatched to its handler; a
    MESSAGE_COMPONENT (button/select click) is dispatched by its ``custom_id`` prefix; a
    MODAL_SUBMIT is dispatched by its modal ``custom_id`` prefix.
    Every other interaction type is acked with an empty ``200``. The dispatchers convert
    any handler exception into an ephemeral error reply, so Discord never sees a 5xx
    (which would get the endpoint auto-disabled).
    """
    from core.events.discord_commands import dispatch, dispatch_component, dispatch_modal
    from core.events.discord_interactions import pong, verify_signature

    if not verify_signature(
        settings.DISCORD_INTERACTIONS_PUBLIC_KEY,
        request.headers.get("X-Signature-Ed25519", ""),
        request.headers.get("X-Signature-Timestamp", ""),
        request.body,
    ):
        return HttpResponse(status=401)
    interaction = json.loads(request.body)
    if interaction["type"] == 1:  # PING
        return JsonResponse(pong())
    if interaction["type"] == 2:  # APPLICATION_COMMAND
        return JsonResponse(dispatch(interaction, request))
    if interaction["type"] == 3:  # MESSAGE_COMPONENT (button/select click)
        return JsonResponse(dispatch_component(interaction, request))
    if interaction["type"] == 5:  # MODAL_SUBMIT
        return JsonResponse(dispatch_modal(interaction, request))
    return HttpResponse(status=200)  # future interaction types: ack, do nothing


def robots_txt(request: HttpRequest) -> HttpResponse:
    """Serve robots.txt on the members host — keep crawlers out of /admin/ and private areas."""
    lines = [
        "User-agent: *",
        "Disallow: /admin/",
        "Disallow: /accounts/",
        "Disallow: /settings/",
        "Disallow: /billing/",
        "Disallow: /tab/",
    ]
    return HttpResponse("\n".join(lines) + "\n", content_type="text/plain")


@require_GET
def privacy_policy(request: HttpRequest) -> HttpResponse:
    """Public privacy policy page.

    Deliberately reachable without login: Google Play's crawler and app
    reviewers must be able to fetch it, it is linked from the store listing,
    and it satisfies the Play Data safety requirement for a policy URL.
    """
    return render(request, "core/privacy_policy.html")


def restart_login(request: HttpRequest) -> HttpResponse:
    """Clear any pending login stage and redirect to the login page."""
    clear_login(request)
    return redirect("account_login")


def find_account(request: HttpRequest) -> HttpResponse:
    """Look up a member by name and send a login link to the email on file."""
    if request.method == "POST":
        form = FindAccountForm(request.POST)
        if form.is_valid():
            form.send_login_email()
            return render(request, "account/find_account_done.html")
    else:
        form = FindAccountForm()
    return render(request, "account/find_account.html", {"form": form})


def home(request):
    """Home page view."""
    if request.user.is_authenticated:
        return redirect("hub_home")
    return render(request, "home.html")


def guild_vanity_redirect(request: HttpRequest, slug: str) -> HttpResponse:
    """Public, human-typable pastlives.app/g/<slug> → 301 to the guest guild page.

    Reachable pre-login (no decorator). The default Guild manager hides soft-deleted
    guilds, so an unknown OR soft-deleted slug 404s. Permanent (301) because the
    vanity ↔ guild mapping is stable; the QR/flyer encode THIS route so the guest
    host can move without reprints.
    """
    from membership.models import Guild

    guild = get_object_or_404(Guild, slug=slug)
    target = f"{settings.GUILDS_BASE_URL}{reverse('hub_guild_detail', args=[guild.slug])}"
    return HttpResponsePermanentRedirect(target)


def newsletter_signup(request: HttpRequest) -> HttpResponse:
    """Standalone Mailchimp signup page — open to the public."""
    if request.method == "POST":
        form = NewsletterSignupForm(request.POST)
        if form.is_valid():
            success = form.subscribe()
            return render(
                request,
                "core/newsletter_signup.html",
                {"form": NewsletterSignupForm(), "success": success, "submitted": True},
            )
    else:
        form = NewsletterSignupForm()
    return render(request, "core/newsletter_signup.html", {"form": form})


@require_GET
def service_worker(request):
    """Serve the service worker JavaScript file.

    The Service-Worker-Allowed header is set by ServiceWorkerAllowedMiddleware,
    not by this view, to avoid redundancy and ensure mutation test coverage.
    """
    sw_path = Path(settings.BASE_DIR) / "static" / "js" / "sw.js"
    if not sw_path.exists():
        return HttpResponse("Service worker not found", status=404)

    with open(sw_path) as f:
        content = f.read()

    return HttpResponse(content, content_type="application/javascript")


@require_GET
@login_required
def vapid_key(request):
    """Return the VAPID public key for push subscription.

    iOS graceful degradation: Returns the key if configured.
    Client-side code handles unavailability gracefully.
    """
    vapid_public_key = settings.WEBPUSH_SETTINGS.get("VAPID_PUBLIC_KEY", "")
    return JsonResponse({"vapid_public_key": vapid_public_key})


@require_POST
@login_required
def subscribe(request):
    """Create a push subscription for the authenticated user.

    Expects JSON body with: endpoint, p256dh, auth
    Returns success/error JSON response.
    iOS graceful degradation: No errors if push features unavailable on client.
    """
    try:
        data = json.loads(request.body)
        endpoint = data.get("endpoint")
        p256dh = data.get("p256dh")
        auth = data.get("auth")

        if not all([endpoint, p256dh, auth]):
            return JsonResponse({"error": "Missing required fields"}, status=400)

        PushSubscription.objects.update_or_create(
            endpoint=endpoint,
            defaults={
                "user": request.user,
                "p256dh": p256dh,
                "auth": auth,
            },
        )

        return JsonResponse({"success": True})

    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception:
        logger.exception("Push subscription failed")
        return JsonResponse({"error": "Subscription failed. Please try again."}, status=500)


@require_POST
@login_required
def unsubscribe(request):
    """Delete a push subscription for the authenticated user.

    Expects JSON body with: endpoint
    Returns success/error JSON response.
    iOS graceful degradation: Silently succeeds even if subscription doesn't exist.
    """
    try:
        data = json.loads(request.body)
        endpoint = data.get("endpoint")

        if not endpoint:
            return JsonResponse({"error": "Missing endpoint"}, status=400)

        # Delete silently - no error if doesn't exist (iOS graceful degradation)
        PushSubscription.objects.filter(endpoint=endpoint, user=request.user).delete()

        return JsonResponse({"success": True})

    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception:
        logger.exception("Push unsubscription failed")
        return JsonResponse({"error": "Unsubscription failed. Please try again."}, status=500)


@require_POST
@login_required
def fcm_register(request):
    """Register a native (Capacitor/FCM) device token for the authenticated user.

    Expects JSON body with: token, and optional platform (defaults to android).
    Mirrors :func:`subscribe` — ``update_or_create`` keyed on the unique token, so a
    device that re-registers (token refresh) updates in place instead of duplicating.
    """
    try:
        data = json.loads(request.body)
        token = data.get("token")
        platform = data.get("platform", FcmDevice.Platform.ANDROID)

        if not token:
            return JsonResponse({"error": "Missing token"}, status=400)
        if platform not in FcmDevice.Platform.values:
            return JsonResponse({"error": "Invalid platform"}, status=400)

        # A token identifies one physical device and belongs to exactly one account.
        # Refuse to move a token already bound to a different user (device-takeover
        # guard): a shared device unregisters on logout, so the next user gets a
        # fresh row rather than silently claiming the previous user's device.
        existing = FcmDevice.objects.filter(token=token).first()
        if existing is not None and existing.user_id != request.user.id:
            return JsonResponse({"error": "Token already registered to another account"}, status=409)

        FcmDevice.objects.update_or_create(
            token=token,
            defaults={"user": request.user, "platform": platform},
        )

        return JsonResponse({"success": True})

    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception:
        logger.exception("FCM device registration failed")
        return JsonResponse({"error": "Registration failed. Please try again."}, status=500)


@require_POST
@login_required
def fcm_unregister(request):
    """Delete a native device token for the authenticated user (logout / opt-out).

    Expects JSON body with: token. Deletes silently — no error if the token is unknown.
    """
    try:
        data = json.loads(request.body)
        token = data.get("token")

        if not token:
            return JsonResponse({"error": "Missing token"}, status=400)

        FcmDevice.objects.filter(token=token, user=request.user).delete()

        return JsonResponse({"success": True})

    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception:
        logger.exception("FCM device unregistration failed")
        return JsonResponse({"error": "Unregistration failed. Please try again."}, status=500)


# ── Biometric login ───────────────────────────────────────────────────────────
# The app stores a rotating secret in the Keychain/Keystore behind Face ID or a
# fingerprint and trades it for a session. All of the logic is in
# core.models.BiometricCredentialManager; these views parse, call it, and answer.

_BIOMETRIC_UNLOCK_SCOPE = "biometric_unlock"
# These caps are a COST bound, not a guessing defense. Guessing is already hopeless: the
# secret is BIOMETRIC_SECRET_BYTES of urlsafe entropy, so no achievable number of tries
# moves the needle. What the cap actually buys is a ceiling on how much work one address
# can make the server do (see the limiter's real cost, below).
#
# So they are set where a shared connection cannot trip them. Every member on the shop
# wifi leaves through ONE egress address, and an enrolled phone spends a slot each time the
# app is opened signed out, so a cap tuned to one person's usage silently disables
# biometric sign in for everyone behind that NAT for the rest of the day. These numbers are
# far above a whole makerspace's daily app opens and far below anything that costs us.
_BIOMETRIC_UNLOCK_HOURLY_LIMIT = 240
_BIOMETRIC_UNLOCK_DAILY_LIMIT = 2000

# One message for every unlock failure. Saying which secrets exist would help an attacker.
# Platform neutral on purpose: an Android member unlocks with a fingerprint, not Face ID.
_BIOMETRIC_UNLOCK_FAILED = "We could not sign you in. Use an emailed code instead."

# A forwarded-for entry is an IP, so anything longer is junk; bound it before it becomes a
# cache key.
_RATE_LIMIT_KEY_MAX_CHARS = 64


def _rate_limit_key(request: HttpRequest) -> str:
    """The address to rate limit an unauthenticated caller by.

    Deliberately NOT the leftmost ``X-Forwarded-For`` entry, which is what a "client IP"
    helper normally reaches for (``classes.views._client_ip`` included). Proxies APPEND to
    that header, so the leftmost value is whatever the client chose to send. On an
    unauthenticated, csrf-exempt endpoint that makes the rate-limit key itself attacker
    controlled: rotate one header value per request and every attempt lands in a fresh
    bucket, so the cap never applies to the one caller it exists to bound.

    ``CF-Connecting-IP`` is preferred because production is Cloudflare in front of Render
    (a response carries both ``server: cloudflare`` and ``x-render-origin-server``). That is
    TWO proxies, so the rightmost forwarded entry is Render's view of *Cloudflare*, not of
    the member: keying on it would collapse every caller into one bucket, and a single
    attacker could then spend the whole day's allowance and disable biometric unlock for
    everybody. Cloudflare overwrites ``CF-Connecting-IP`` on every request it forwards, so a
    client cannot dictate it.

    The fallbacks are the rightmost forwarded entry (one proxy, appended by it) and then
    ``REMOTE_ADDR`` (a direct connection: local dev and tests).

    Known gap, accepted: someone who reaches the Render origin directly, bypassing
    Cloudflare, can send any ``CF-Connecting-IP`` they like. That is no weaker than the
    leftmost-entry behavior this replaced, it requires knowing the origin address, and the
    cap is a cost ceiling rather than a defense against guessing the secret, which has 384
    bits behind it.

    This keys on one address, so everyone behind a single NAT shares a bucket, which is why
    the caps above are sized for a whole makerspace on one connection.
    """
    connecting = request.META.get("HTTP_CF_CONNECTING_IP", "").strip()
    if connecting:
        return connecting[:_RATE_LIMIT_KEY_MAX_CHARS]
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.rsplit(",", 1)[-1].strip()[:_RATE_LIMIT_KEY_MAX_CHARS]
    return request.META.get("REMOTE_ADDR", "")


@require_POST
@login_required
def biometric_enroll(request: HttpRequest) -> JsonResponse:
    """Mint a biometric credential for the caller's device and return its raw secret.

    Being logged in IS the security boundary here: the secret is handed only to a session
    that has already proved who it belongs to with an emailed login code.

    Expects JSON body with: device_label, and optional platform (defaults to android).
    Returns ``{"secret": ..., "credential_id": ...}``. The secret is returned exactly once
    and is never stored. The id is not a secret and buys nothing on its own — it exists so
    the app can revoke THIS device on logout without reading the secret back out of the
    Keychain, which would mean a Face ID prompt in the middle of signing out.
    """
    from core.models import BiometricCredential

    try:
        data = json.loads(request.body)
        device_label = str(data.get("device_label", "")).strip()
        platform = data.get("platform", BiometricCredential.Platform.ANDROID)

        if not device_label:
            return JsonResponse({"error": "Missing device label"}, status=400)
        if platform not in BiometricCredential.Platform.values:
            return JsonResponse({"error": "Invalid platform"}, status=400)

        # The label is client-supplied cosmetic text shown back to the member. Trim it to
        # the column width rather than 500ing on an over-long one; it is escaped on render.
        credential, secret = BiometricCredential.objects.issue(
            cast(User, request.user), device_label=device_label[:120], platform=platform
        )
        return JsonResponse({"secret": secret, "credential_id": credential.pk})

    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception:
        logger.exception("Biometric enrollment failed")
        return JsonResponse({"error": "Enrollment failed. Please try again."}, status=500)


@csrf_exempt
@require_POST
def biometric_unlock(request: HttpRequest) -> JsonResponse:
    """Trade a biometric secret for a session, and hand back the rotated replacement.

    ``csrf_exempt`` is deliberate here, and it is the ONLY endpoint in this feature that
    gets it. The caller has no session yet — that is the whole point of unlocking — so it
    may have no CSRF cookie to send. Enroll and disable both keep CSRF, because both ride
    on an existing session cookie.

    Dropping CSRF here gives up two different things, and only one of them is harmless:

    * **Forging a request with the victim's authority** — not a risk. The request carries
      no ambient credential; its authority is entirely the secret in the body, which a
      cross-site attacker can neither read nor guess.
    * **Login CSRF** — a real risk, and the reason for the content-type check below. An
      attacker who posts THEIR OWN secret from a page the victim visits can silently sign
      the victim's browser into the ATTACKER's account, and then watch whatever the victim
      does there. A plain HTML form can only send urlencoded, multipart, or text/plain
      bodies, and this view parses JSON from any of them, so ``enctype="text/plain"`` would
      otherwise carry the attack with no script at all. Requiring ``application/json``
      closes it: a form cannot produce that content type, and a cross-origin ``fetch`` that
      sets it becomes preflighted — and nothing in this project answers a CORS preflight.

    Expects a JSON body (``Content-Type: application/json``) with: secret.
    Returns ``{"ok": true, "secret": <new secret>}``.
    """
    from core.abuse_limits import record_keyed_attempt
    from core.models import BiometricCredential, InvalidBiometricCredential

    # Login-CSRF guard, checked before the limiter so a cross-site form cannot even spend
    # the victim's rate budget. See the content-type reasoning in the docstring.
    if request.content_type != "application/json":
        return JsonResponse({"error": "Expected a JSON body."}, status=415)

    # Counted before the credential lookup, so a flood stops at a bounded cost instead of
    # reaching the credential table.
    #
    # "Bounded", not "free": production CACHES.default is DatabaseCache, so this limiter is
    # itself several database round trips, and a refused attempt still pays them. That is
    # the honest cost, and it is why the key must not be attacker controlled — see
    # _rate_limit_key. DatabaseCache also has no atomic incr (it inherits BaseCache's
    # get-then-set), so simultaneous attempts can undercount and the cap can overshoot
    # under concurrency. Acceptable here because the cap is a cost ceiling rather than a
    # guessing defense, and overshooting a generous ceiling by a few requests changes
    # nothing. It would NOT be acceptable for a limiter guarding something guessable.
    rate_key = _rate_limit_key(request)
    allowed, reason = record_keyed_attempt(
        _BIOMETRIC_UNLOCK_SCOPE,
        rate_key,
        hourly_limit=_BIOMETRIC_UNLOCK_HOURLY_LIMIT,
        daily_limit=_BIOMETRIC_UNLOCK_DAILY_LIMIT,
    )
    if not allowed:
        logger.warning("Biometric unlock rate limited (%s) for ip=%s", reason, rate_key)
        return JsonResponse({"error": "Too many tries. Sign in with an emailed code."}, status=429)

    try:
        data = json.loads(request.body)
        secret = data.get("secret")

        if not secret:
            return JsonResponse({"error": "Missing secret"}, status=400)

        try:
            # Rebound onto `secret` on purpose, rather than a `new_secret` local. Sentry
            # runs with send_default_pii=True and redacts frame locals by exact key match
            # against a denylist that contains "secret" and nothing like "new_secret", so a
            # more descriptive name here would ship a live bearer token with any 500 raised
            # below (login() included). See the naming note in core/models.py.
            user, secret = BiometricCredential.objects.redeem(secret)
        except InvalidBiometricCredential:
            return JsonResponse({"error": _BIOMETRIC_UNLOCK_FAILED}, status=401)

        # login() does not check is_active — only authenticate() does, and this path skips
        # it. A deleted account keeps its User row (deactivated), so without this a stale
        # credential would sign a locked-out member back in.
        if not user.is_active:
            BiometricCredential.objects.revoke_all(user)
            logger.warning("Biometric unlock refused for inactive user pk=%s; credentials revoked.", user.pk)
            return JsonResponse({"error": _BIOMETRIC_UNLOCK_FAILED}, status=401)

        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        return JsonResponse({"ok": True, "secret": secret})

    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception:
        logger.exception("Biometric unlock failed")
        return JsonResponse({"error": "Sign in failed. Please try again."}, status=500)


@require_POST
@login_required
def biometric_disable(request: HttpRequest) -> JsonResponse:
    """Revoke the caller's biometric credentials — one device, or all of them.

    Expects a JSON body with either ``secret`` or ``credential_id`` to revoke just that one
    device. An empty body revokes every credential on the account.

    ``credential_id`` is what app logout uses. Revoking by secret would mean reading the
    secret back out of the Keychain, which raises a Face ID prompt in the middle of signing
    out; revoking everything would kill the member's other phone at the same time. The id
    is not a secret, and every lookup here is scoped to the caller, so it grants nothing.

    Silent success when nothing matches, matching :func:`fcm_unregister`: the caller's goal
    (that credential no longer works) is already true, and saying otherwise would confirm
    which secrets and ids exist.
    """
    from core.models import BiometricCredential, hash_biometric_secret

    try:
        data = json.loads(request.body) if request.body else {}
        secret = data.get("secret", "")
        credential_id = data.get("credential_id")

        user = cast(User, request.user)
        # Scoped to the caller in every branch: neither a secret nor an id belonging to
        # someone else may be revocable by whoever happens to be logged in.
        owned = BiometricCredential.objects.filter(user=user)

        if secret:
            # Matched against the superseded hash too, so a logout still lands when the app
            # is holding a secret whose rotation reply never arrived.
            digest = hash_biometric_secret(secret)
            credential = owned.filter(Q(secret_hash=digest) | Q(previous_secret_hash=digest)).first()
        elif credential_id is not None:
            credential = owned.filter(pk=credential_id).first()
        else:
            BiometricCredential.objects.revoke_all(user)
            return JsonResponse({"success": True})

        if credential is not None:
            BiometricCredential.objects.revoke(credential)
        return JsonResponse({"success": True})

    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception:
        logger.exception("Biometric disable failed")
        return JsonResponse({"error": "We could not turn that off. Please try again."}, status=500)


@staff_member_required
def site_activity(request: HttpRequest) -> HttpResponse:
    """Staff dashboard: a chronological site-wide event feed and an email audit log."""
    tab = request.GET.get("tab", "feed")

    activities = SiteActivity.objects.select_related("actor", "target_ct", "email_log").all()
    kind = request.GET.get("kind", "").strip()
    if kind:
        activities = activities.filter(kind=kind)
    actor_q = request.GET.get("actor", "").strip()
    if actor_q:
        activities = activities.filter(actor__email__icontains=actor_q)
    feed_page = Paginator(activities, 50).get_page(request.GET.get("page"))

    emails = TransactionalEmailLog.objects.all()
    status = request.GET.get("status", "").strip()
    if status:
        emails = emails.filter(status=status)
    email_page = Paginator(emails, 50).get_page(request.GET.get("epage"))

    return render(
        request,
        "hub/admin/activity.html",
        {
            "active_tab": tab,
            "feed_page": feed_page,
            "email_page": email_page,
            "kinds": SiteActivity.Kind.choices,
            "kind": kind,
            "actor_q": actor_q,
            "status": status,
        },
    )


_NOTIFICATIONS_PAGE_SIZE = 20


@login_required
def notification_list(request: HttpRequest) -> HttpResponse:
    """The member's full Notifications page — newest-first, paginated, unread emphasized."""
    from .models import Notification

    user: User = request.user  # type: ignore[assignment]  # @login_required guarantees User
    qs = Notification.objects.for_user(user)
    paginator = Paginator(qs, _NOTIFICATIONS_PAGE_SIZE)
    page = paginator.get_page(request.GET.get("page", 1))
    return render(request, "hub/notifications.html", {"page": page, "unread_count": qs.unread().count()})


@login_required
def notification_unread_count(request: HttpRequest) -> HttpResponse:
    """Plain-text unread count for the badge (HTMX polling target)."""
    from .models import Notification

    user: User = request.user  # type: ignore[assignment]  # @login_required guarantees User
    count = Notification.objects.filter(user=user, read_at__isnull=True).count()
    return HttpResponse(str(count))


@require_POST
@login_required
def notification_read(request: HttpRequest, pk: int) -> HttpResponse:
    """Mark one notification read and redirect to its url (or the home page)."""
    from .models import Notification

    user: User = request.user  # type: ignore[assignment]  # @login_required guarantees User
    note = Notification.objects.filter(user=user, pk=pk).first()
    if note is None:
        return redirect("home")
    note.mark_read()
    return redirect(note.url or "home")


@require_POST
@login_required
def notification_read_all(request: HttpRequest) -> HttpResponse:
    """Mark all the user's notifications read, then return to the Notifications page."""
    from django.contrib import messages
    from django.utils import timezone

    from .models import Notification

    user: User = request.user  # type: ignore[assignment]  # @login_required guarantees User
    Notification.objects.for_user(user).unread().update(read_at=timezone.now())
    messages.success(request, "You're all caught up.")
    return redirect("notification_list")


# ── Signage slideshow (public, undecorated kiosk) ──────────────────────────────
# The player renders BYTE-IDENTICAL public content regardless of request.user: on
# .pastlives.space the session cookie means a logged-in admin can arrive here
# authenticated, so these views NEVER branch content or chrome on user state. The
# surface guard 404s the routes anywhere but the signage host (the routes live in
# the shared urlconf, so members.pastlives.space/<slug>/ resolves here but 404s).


def signage_player(request: HttpRequest, zone_slug: str) -> HttpResponse:
    """Full-screen kiosk slideshow for one zone. Public, undecorated, surface-guarded."""
    from membership.models import SlideshowZone
    from membership.signage import build_deck, deck_hash

    from .models import SiteConfiguration

    if getattr(request, "surface", None) != "signage":
        raise Http404("Not available on this surface.")
    zone = get_object_or_404(SlideshowZone, slug=zone_slug, is_enabled=True)
    config = SiteConfiguration.load()
    deck = build_deck(zone)
    ctx = {"zone": zone, "config": config, "deck": deck, "deck_hash": deck_hash(deck, config)}
    return render(request, "signage/player.html", ctx)


def signage_deck(request: HttpRequest, zone_slug: str) -> HttpResponse:
    """The 300s HTMX poll target. Returns 204 + HX-Reswap:none when nothing changed."""
    from membership.models import SlideshowZone
    from membership.signage import build_deck, deck_hash

    from .models import SiteConfiguration

    if getattr(request, "surface", None) != "signage":
        raise Http404("Not available on this surface.")
    zone = get_object_or_404(SlideshowZone, slug=zone_slug, is_enabled=True)
    config = SiteConfiguration.load()
    deck = build_deck(zone)
    current = deck_hash(deck, config)
    if request.GET.get("h") == current:
        # Nothing changed since the wall last rendered — skip the swap so the
        # rotation keeps running (no jump to slide 0, no blank frame).
        resp = HttpResponse(status=204)
        resp["HX-Reswap"] = "none"
        return resp
    return render(
        request,
        "signage/_deck.html",
        {"zone": zone, "config": config, "deck": deck, "deck_hash": current},
    )
