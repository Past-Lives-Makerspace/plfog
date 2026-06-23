import pytest
from django.test import RequestFactory
from django.urls import reverse

from classes.factories import UserFactory


@pytest.fixture
def rf() -> RequestFactory:
    return RequestFactory()


def describe_get_login_redirect_url():
    def describe_on_public_surface():
        def it_lands_on_the_account_overview(rf, db):
            from plfog.adapters import AdminRedirectAccountAdapter

            user = UserFactory()
            req = rf.get("/accounts/login/")
            req.surface = "public"
            req.user = user
            adapter = AdminRedirectAccountAdapter()
            assert adapter.get_login_redirect_url(req) == "/account/"

    def describe_on_members_surface():
        def it_uses_existing_redirect(rf, db):
            from plfog.adapters import AdminRedirectAccountAdapter

            user = UserFactory()
            req = rf.get("/accounts/login/")
            req.surface = "members"
            req.user = user
            adapter = AdminRedirectAccountAdapter()
            assert adapter.get_login_redirect_url(req) == reverse("hub_community_calendar")
