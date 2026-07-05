"""Core app views for PWA push notification infrastructure."""

import json
import logging
from pathlib import Path

from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.http import HttpRequest, HttpResponse, HttpResponsePermanentRedirect, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
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
from .models import PushSubscription, SiteActivity, TransactionalEmailLog

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
    guilds, so an unknown OR soft-deleted slug 404s. A private guild (``is_public=False``)
    also 404s — we never publicly redirect to a page that would itself 404. Permanent (301)
    because the vanity ↔ guild mapping is stable; the QR/flyer encode THIS route so the guest
    host can move without reprints.
    """
    from membership.models import Guild

    guild = get_object_or_404(Guild, slug=slug, is_public=True)
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


@login_required
def notification_feed(request: HttpRequest) -> HttpResponse:
    """HTMX partial: the user's 15 most recent notifications."""
    from .models import Notification

    user: User = request.user  # type: ignore[assignment]  # @login_required guarantees User
    items = Notification.objects.filter(user=user)[:15]
    return render(request, "hub/_notification_feed.html", {"notifications": items})


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
    """Mark all the user's notifications read."""
    from django.utils import timezone

    from .models import Notification

    user: User = request.user  # type: ignore[assignment]  # @login_required guarantees User
    Notification.objects.filter(user=user, read_at__isnull=True).update(read_at=timezone.now())
    return HttpResponse(status=204)
