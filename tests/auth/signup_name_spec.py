"""BDD specs for name collection on account signup (issue #274).

Signup now requires a full name (and optionally the name the person goes by),
so a self-signed-up Member gets a real ``full_legal_name`` instead of the
lowercased email local part allauth auto-generates as the username.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from core.models import SiteConfiguration
from membership.models import Member
from plfog.adapters import MarketingOptInSignupForm
from tests.membership.factories import MemberFactory, MembershipPlanFactory

pytestmark = pytest.mark.django_db

User = get_user_model()


@pytest.fixture
def open_signup():
    config = SiteConfiguration.load()
    config.registration_mode = SiteConfiguration.RegistrationMode.OPEN
    config.save()
    return config


def describe_MarketingOptInSignupForm_name_fields():
    def it_requires_full_name_and_leaves_preferred_name_optional():
        form = MarketingOptInSignupForm()
        assert form.fields["full_name"].required is True
        assert form.fields["preferred_name"].required is False

    def it_caps_full_name_so_the_split_halves_fit_the_user_columns():
        # User.first_name/last_name are varchar(150); a longer full name would
        # DataError on Postgres when either half lands whole in one column.
        form = MarketingOptInSignupForm()
        assert form.fields["full_name"].max_length == 150

    def describe_clean():
        def it_splits_full_name_into_first_and_last_for_allauth(open_signup):
            form = MarketingOptInSignupForm(data={"email": "split@example.com", "full_name": "Jessica Rivera"})
            assert form.is_valid(), form.errors
            assert form.cleaned_data["first_name"] == "Jessica"
            assert form.cleaned_data["last_name"] == "Rivera"

        def it_puts_a_mononym_entirely_in_first_name(open_signup):
            form = MarketingOptInSignupForm(data={"email": "mono@example.com", "full_name": "Cher"})
            assert form.is_valid(), form.errors
            assert form.cleaned_data["first_name"] == "Cher"
            assert form.cleaned_data["last_name"] == ""

        def it_strips_surrounding_whitespace_from_full_name(open_signup):
            form = MarketingOptInSignupForm(data={"email": "pad@example.com", "full_name": "  Ada Lovelace  "})
            assert form.is_valid(), form.errors
            assert form.cleaned_data["full_name"] == "Ada Lovelace"

        def it_rejects_a_missing_full_name(open_signup):
            form = MarketingOptInSignupForm(data={"email": "noname@example.com"})
            assert not form.is_valid()
            assert "full_name" in form.errors


def describe_signup_post_with_name():
    def it_creates_a_member_with_the_collected_full_legal_name(client, open_signup):
        MembershipPlanFactory()
        response = client.post(
            "/accounts/signup/",
            {"email": "jessica@example.com", "full_name": "Jessica Rivera"},
        )
        assert response.status_code == 302
        user = User.objects.get(email="jessica@example.com")
        assert user.first_name == "Jessica"
        assert user.last_name == "Rivera"
        member = Member.objects.get(user=user)
        assert member.full_legal_name == "Jessica Rivera"
        assert member.preferred_name == ""

    def it_stamps_the_preferred_name_when_given(client, open_signup):
        MembershipPlanFactory()
        client.post(
            "/accounts/signup/",
            {"email": "jess@example.com", "full_name": "Jessica Rivera", "preferred_name": "Jess"},
        )
        member = Member.objects.get(user__email="jess@example.com")
        assert member.full_legal_name == "Jessica Rivera"
        assert member.preferred_name == "Jess"
        assert member.display_name == "Jess"

    def it_preserves_the_exact_typed_name_on_the_member(client, open_signup):
        # get_full_name() joins the split halves with a single space; the Member
        # is re-stamped with the raw form value so interior spacing survives.
        MembershipPlanFactory()
        client.post(
            "/accounts/signup/",
            {"email": "mary@example.com", "full_name": "Mary  Jane Watson"},
        )
        member = Member.objects.get(user__email="mary@example.com")
        assert member.full_legal_name == "Mary  Jane Watson"

    def it_never_uses_the_username_as_the_legal_name(client, open_signup):
        MembershipPlanFactory()
        client.post(
            "/accounts/signup/",
            {"email": "jessica.smith@example.com", "full_name": "Jessica Smith"},
        )
        member = Member.objects.get(user__email="jessica.smith@example.com")
        assert member.full_legal_name != "jessica.smith"

    def it_rerenders_the_form_and_creates_no_user_without_a_full_name(client, open_signup):
        response = client.post("/accounts/signup/", {"email": "incomplete@example.com"})
        assert response.status_code == 200
        assert not User.objects.filter(email="incomplete@example.com").exists()

    def it_overrides_an_invite_placeholder_name_with_the_collected_name(client, open_signup):
        # Invited placeholders are seeded with the raw email as their name
        # (core.models.Invite.create_and_send); the name the person actually
        # types at signup wins over that placeholder.
        plan = MembershipPlanFactory()
        placeholder = MemberFactory(
            user=None,
            _pre_signup_email="invitee@example.com",
            full_legal_name="invitee@example.com",
            status=Member.Status.INVITED,
            membership_plan=plan,
        )
        response = client.post(
            "/accounts/signup/",
            {"email": "invitee@example.com", "full_name": "Real Name", "preferred_name": "Ree"},
        )
        assert response.status_code == 302
        placeholder.refresh_from_db()
        assert placeholder.user is not None
        assert placeholder.status == Member.Status.ACTIVE
        assert placeholder.full_legal_name == "Real Name"
        assert placeholder.preferred_name == "Ree"

    def it_completes_signup_when_no_membership_plan_exists(client, open_signup):
        # The signal skips Member creation without a plan; stamping must not crash.
        from membership.models import MembershipPlan

        MembershipPlan.objects.all().delete()
        response = client.post(
            "/accounts/signup/",
            {"email": "planless@example.com", "full_name": "Plan Less"},
        )
        assert response.status_code == 302
        user = User.objects.get(email="planless@example.com")
        assert not Member.objects.filter(user=user).exists()


def describe_signup_template_name_fields():
    def it_renders_both_name_inputs_on_the_members_surface(client, open_signup):
        content = client.get("/accounts/signup/").content.decode()
        assert 'name="full_name"' in content
        assert 'name="preferred_name"' in content

    def it_renders_both_name_inputs_on_the_guest_surface(open_signup, settings):
        settings.ALLOWED_HOSTS = ["book.pastlives.space", "members.pastlives.space"]
        settings.PUBLIC_HOSTS = ["book.pastlives.space"]
        settings.MEMBER_HOST = "members.pastlives.space"

        content = Client(HTTP_HOST="book.pastlives.space").get("/accounts/signup/").content.decode()

        assert 'name="full_name"' in content
        assert 'name="preferred_name"' in content
