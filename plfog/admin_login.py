"""Replacement for the stock Django-admin password login.

Members authenticate only via allauth email login codes — the admin's
username/password form is vestigial and should never render for anyone. This
module swaps ``admin.site.login`` for a thin redirect/deny so ``/admin/login/``
is unreachable as a password form.
"""

from urllib.parse import quote

from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse


def admin_login_redirect(request: HttpRequest, extra_context: dict | None = None) -> HttpResponse:
    """Replace the stock admin password login.

    - active staff .................. straight to the admin index (they're already
                                       authenticated via allauth; stock convenience preserved)
    - authenticated non-staff ....... 403 (never a redirect — that would loop)
    - anonymous ..................... the allauth email-code login, preserving ?next
    """
    if request.user.is_authenticated:
        if admin.site.has_permission(request):  # is_active and is_staff
            return redirect("admin:index")
        raise PermissionDenied
    next_url = request.GET.get("next") or reverse("admin:index")
    return redirect(f"{reverse('account_request_login_code')}?next={quote(next_url)}")


def install_admin_login_redirect() -> None:
    """Point ``admin.site.login`` at :func:`admin_login_redirect`.

    MUST run before ``admin.site.urls`` is first evaluated (i.e. before the
    ``path("admin/", admin.site.urls)`` line in ``plfog/urls.py``), because
    ``AdminSite.get_urls()`` binds ``self.login`` at URLconf-build time.
    """
    admin.site.login = admin_login_redirect  # type: ignore[method-assign]  # deliberate override of stock login
