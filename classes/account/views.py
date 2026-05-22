"""Views for the public /account/ dashboard on book.pastlives.space.

Anonymous users are bounced to the themed login page (see Phase 8). The
LookupView is the one exception — guests with a confirmation order number
can look up their booking without an account.
"""

from __future__ import annotations

from django.contrib.auth.mixins import LoginRequiredMixin
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
