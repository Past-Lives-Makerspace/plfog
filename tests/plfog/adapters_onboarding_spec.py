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
        def it_lands_on_hub_home(rf, db):
            from plfog.adapters import AdminRedirectAccountAdapter

            user = UserFactory()
            # Stamped = already answered the guild-updates prompt; the unanswered
            # routing is specced in describe_guild_updates_prompt_routing below.
            user.member.mark_guild_updates_answered()
            req = rf.get("/accounts/login/")
            req.surface = "members"
            req.user = user
            adapter = AdminRedirectAccountAdapter()
            assert adapter.get_login_redirect_url(req) == reverse("hub_home")

    def describe_guild_updates_prompt_routing():
        @pytest.fixture
        def members_request(rf):
            def build(user):
                req = rf.get("/accounts/login/")
                req.surface = "members"
                req.user = user
                return req

            return build

        def it_routes_a_member_with_no_stamp_and_no_subscriptions_to_the_prompt(members_request, db):
            from plfog.adapters import AdminRedirectAccountAdapter

            from tests.membership.factories import MembershipPlanFactory

            MembershipPlanFactory()
            user = UserFactory()  # signal auto-links a Member once a plan exists
            adapter = AdminRedirectAccountAdapter()
            assert adapter.get_login_redirect_url(members_request(user)) == reverse("hub_guild_updates_prompt")

        def it_lands_a_member_with_a_subscription_on_hub_home(members_request, db):
            from plfog.adapters import AdminRedirectAccountAdapter

            from tests.membership.factories import GuildMembershipFactory, MembershipPlanFactory

            MembershipPlanFactory()
            user = UserFactory()
            GuildMembershipFactory(member=user.member)
            adapter = AdminRedirectAccountAdapter()
            assert adapter.get_login_redirect_url(members_request(user)) == reverse("hub_home")

        def it_lands_a_stamped_member_on_hub_home(members_request, db):
            from plfog.adapters import AdminRedirectAccountAdapter

            from tests.membership.factories import MembershipPlanFactory

            MembershipPlanFactory()
            user = UserFactory()
            user.member.mark_guild_updates_answered()
            adapter = AdminRedirectAccountAdapter()
            assert adapter.get_login_redirect_url(members_request(user)) == reverse("hub_home")

        def it_leaves_the_public_surface_alone(rf, db):
            from plfog.adapters import AdminRedirectAccountAdapter

            from tests.membership.factories import MembershipPlanFactory

            MembershipPlanFactory()
            user = UserFactory()
            req = rf.get("/accounts/login/")
            req.surface = "public"
            req.user = user
            adapter = AdminRedirectAccountAdapter()
            assert adapter.get_login_redirect_url(req) == "/account/"
