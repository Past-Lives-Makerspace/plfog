"""Core app views for PWA push notification infrastructure."""

import json
import logging
from pathlib import Path

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST

from allauth.account.internal.stagekit import clear_login

from .forms import FindAccountForm, NewsletterSignupForm
from .models import PushSubscription

logger = logging.getLogger(__name__)


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
        return redirect("hub_guild_voting")
    return render(request, "home.html")


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
