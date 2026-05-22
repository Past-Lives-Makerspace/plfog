"""Views for the public /account/ dashboard on book.pastlives.space.

Anonymous users are bounced to the themed login page (see Phase 8). The
LookupView is the one exception — guests with a confirmation order number
can look up their booking without an account.
"""

from __future__ import annotations

from django.contrib.auth.mixins import LoginRequiredMixin
from django.utils import timezone
from django.views.generic import TemplateView


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


class ReceiptsView(_LoggedInAccountView):
    template_name = "classes/account/receipts.html"
    active_tab = "receipts"


class ProfileView(_LoggedInAccountView):
    template_name = "classes/account/profile.html"
    active_tab = "profile"


class LookupView(TemplateView):
    template_name = "classes/account/lookup.html"
