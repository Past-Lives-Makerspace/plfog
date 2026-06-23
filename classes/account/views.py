"""Views for the public /account/ dashboard on book.pastlives.space.

Anonymous users are bounced to the themed login page (see Phase 8). The
LookupView is the one exception — guests with a confirmation order number
can look up their booking without an account.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpRequest, HttpResponseRedirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import FormView, TemplateView


class _RelayAwareLoginMixin(LoginRequiredMixin):
    """LoginRequiredMixin that relays unauthenticated users to the members surface
    for single-sign-on when on the public/book surface.

    Flow when unauthenticated on book surface:
    1. Redirect → {MEMBER_HOST}/auth/relay/?book_host=...&next=...
    2. Members surface checks session; if logged in → issues signed token → redirects
       to book surface /auth/relay/accept/ → user is now logged in on book surface.
    3. If not logged in on members surface either → falls back to book-surface login.

    Falls back to the standard login redirect when MEMBER_HOST is unset or matches
    the current host (e.g., when running on the members surface itself).
    """

    login_url = "/accounts/login/"

    request: HttpRequest

    def handle_no_permission(self) -> HttpResponseRedirect:
        if not self.request.user.is_authenticated:
            relay_url = self._relay_url()
            if relay_url:
                return HttpResponseRedirect(relay_url)
        return super().handle_no_permission()

    def _relay_url(self) -> str:
        from urllib.parse import urlencode

        from django.conf import settings

        surface = getattr(self.request, "surface", "members")
        if surface != "public":
            return ""

        member_host = (getattr(settings, "MEMBER_HOST", "") or "").lower().strip()
        current_host = self.request.get_host()
        current_bare = current_host.split(":", 1)[0].lower()

        if not member_host or member_host == current_bare:
            return ""

        scheme = "https" if self.request.is_secure() else "http"
        port = self.request.get_port()
        port_part = f":{port}" if port not in ("80", "443") else ""
        return (
            f"{scheme}://{member_host}{port_part}/auth/relay/"
            f"?{urlencode({'book_host': current_host, 'next': self.request.get_full_path()})}"
        )


class _LoggedInAccountView(_RelayAwareLoginMixin, TemplateView):
    active_tab: str = ""

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["active_tab"] = self.active_tab
        return ctx


class OverviewView(_LoggedInAccountView):
    template_name = "classes/account/overview.html"
    active_tab = "overview"

    def get_context_data(self, **kwargs):
        from classes.account.selectors import upcoming_registrations
        from classes.models import ClassOffering

        ctx = super().get_context_data(**kwargs)
        ctx["upcoming"] = list(upcoming_registrations(self.request.user))

        # Instructor banner count — how many classes is this user teaching that
        # still have a future session?
        from membership.models import Member as MemberModel

        member = MemberModel.objects.filter(user=self.request.user).first()
        if member is not None and member.is_instructor:
            ctx["instructor_upcoming_count"] = (
                ClassOffering.objects.filter(instructor=member, status=ClassOffering.Status.PUBLISHED)
                .filter(sessions__starts_at__gte=timezone.now())
                .distinct()
                .count()
            )
        else:
            ctx["instructor_upcoming_count"] = 0

        ctx["nudge_dismissed"] = self.request.COOKIES.get("pl_nudge_dismissed") == "1"
        return ctx


class HistoryView(_LoggedInAccountView):
    template_name = "classes/account/history.html"
    active_tab = "history"

    def get_context_data(self, **kwargs):
        from collections import defaultdict

        from classes.account.selectors import past_registrations

        ctx = super().get_context_data(**kwargs)
        grouped: dict[int, list] = defaultdict(list)
        for reg in past_registrations(self.request.user):
            sessions = sorted(reg.class_offering.sessions.all(), key=lambda s: s.starts_at, reverse=True)
            year = sessions[0].starts_at.year if sessions else reg.registered_at.year
            grouped[year].append(reg)
        # Convert to a sorted list of (year, regs) tuples — descending year.
        ctx["grouped"] = sorted(grouped.items(), reverse=True)
        return ctx


class ReceiptsView(_LoggedInAccountView):
    template_name = "classes/account/receipts.html"
    active_tab = "receipts"

    def get_context_data(self, **kwargs):
        from classes.account.selectors import paid_registrations

        ctx = super().get_context_data(**kwargs)
        ctx["receipts"] = list(paid_registrations(self.request.user))
        return ctx


class ProfileView(_RelayAwareLoginMixin, FormView):
    """Editable for non-members; read-only with an "Edit on FOG" link for members.

    Member-persona POSTs are silently redirected (no error) — the form is
    invisible to them anyway.
    """

    template_name = "classes/account/profile.html"
    active_tab = "profile"
    success_url = reverse_lazy("account:profile")

    @property
    def form_class(self):
        from classes.account.forms import AccountProfileForm

        return AccountProfileForm

    def _is_readonly_for_member(self) -> bool:
        """True when the active persona is 'member' — page is read-only."""
        from core.context_processors import persona

        return persona(self.request)["persona"] == "member"

    def get_form_kwargs(self):
        kw = super().get_form_kwargs()
        kw["instance"] = self.request.user
        return kw

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["active_tab"] = self.active_tab
        ctx["is_readonly"] = self._is_readonly_for_member()
        return ctx

    def post(self, request, *args, **kwargs):
        if self._is_readonly_for_member():
            messages.info(request, "Your member profile is managed on FOG.")
            return HttpResponseRedirect(str(self.success_url))
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        form.save()
        messages.success(self.request, "Profile updated.")
        return super().form_valid(form)


class LookupView(FormView):
    """Three states: form (initial GET), result (POST + match), notfound (POST + no match)."""

    template_name = "classes/account/lookup.html"

    @property
    def form_class(self):
        from classes.account.forms import LookupForm

        return LookupForm

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.setdefault("lookup_state", "form")
        ctx.setdefault("result", None)
        return ctx

    def form_valid(self, form):
        result = form.find()
        ctx = self.get_context_data(form=form)
        ctx["lookup_state"] = "result" if result else "notfound"
        ctx["result"] = result
        return self.render_to_response(ctx)

    def form_invalid(self, form):
        # Form-level validation error (bad order_number format) — re-render the form
        # state with the field errors visible.
        ctx = self.get_context_data(form=form)
        ctx["lookup_state"] = "form"
        return self.render_to_response(ctx)
