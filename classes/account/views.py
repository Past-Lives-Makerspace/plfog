"""Views for the public /account/ dashboard on book.pastlives.space.

Anonymous users are bounced to the themed login page (see Phase 8). The
LookupView is the one exception — guests with a confirmation order number
can look up their booking without an account.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponseRedirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import FormView, TemplateView


class _LoggedInAccountView(LoginRequiredMixin, TemplateView):
    login_url = "/accounts/login/"
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
        instructor = getattr(self.request.user, "instructor", None)
        if instructor is not None:
            ctx["instructor_upcoming_count"] = (
                ClassOffering.objects.filter(instructor=instructor, status=ClassOffering.Status.PUBLISHED)
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
            sess = reg.class_offering.sessions.order_by("-starts_at").first()
            year = sess.starts_at.year if sess else reg.registered_at.year
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


class ProfileView(LoginRequiredMixin, FormView):
    """Editable for non-members; read-only with an "Edit on FOG" link for members.

    Member-persona POSTs are silently redirected (no error) — the form is
    invisible to them anyway.
    """

    template_name = "classes/account/profile.html"
    active_tab = "profile"
    login_url = "/accounts/login/"
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


class LookupView(TemplateView):
    template_name = "classes/account/lookup.html"
