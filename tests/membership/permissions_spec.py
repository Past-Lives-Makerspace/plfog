"""BDD specs for guild-lead edit permissions — the single FK-based source of truth.

Covers ``Member.can_edit_class`` (role-based) and the request-level helpers in
``membership/permissions.py`` (``view_as``-aware). The positive request-helper
paths are exercised through the views (see the detail-page and hero-adjust
specs); here we lock the model rule and the defensive request branches.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import AnonymousUser
from django.contrib.contenttypes.models import ContentType
from django.test import RequestFactory

from classes.factories import CategoryFactory, ClassOfferingFactory, UserFactory
from membership.models import Member
from membership.permissions import can_edit_category, can_edit_class, can_edit_guild, is_effective_staff
from tests.membership.factories import GuildFactory, MemberFactory

pytestmark = pytest.mark.django_db


def describe_member_can_edit_class():
    def it_allows_the_instructor():
        me = MemberFactory()
        offering = ClassOfferingFactory(instructor=me)
        assert me.can_edit_class(offering) is True

    def it_allows_the_lead_of_the_categorys_guild():
        lead = MemberFactory()
        guild = GuildFactory(guild_lead=lead)
        offering = ClassOfferingFactory(category=CategoryFactory(guild=guild))
        assert lead.can_edit_class(offering) is True

    def it_allows_admins_and_officers():
        admin = MemberFactory(fog_role=Member.FogRole.ADMIN)
        officer = MemberFactory(fog_role=Member.FogRole.GUILD_OFFICER)
        offering = ClassOfferingFactory()
        assert admin.can_edit_class(offering) is True
        assert officer.can_edit_class(offering) is True

    def it_denies_a_lead_of_another_guild():
        lead = MemberFactory()
        GuildFactory(guild_lead=lead)
        offering = ClassOfferingFactory(category=CategoryFactory(guild=GuildFactory()))
        assert lead.can_edit_class(offering) is False

    def it_denies_a_plain_member_even_when_the_category_has_no_guild():
        plain = MemberFactory()
        offering = ClassOfferingFactory(category=CategoryFactory(guild=None))
        assert plain.can_edit_class(offering) is False


def describe_request_helpers_without_view_as():
    def it_denies_an_anonymous_request():
        request = RequestFactory().get("/")
        request.user = AnonymousUser()
        assert is_effective_staff(request) is False
        assert can_edit_guild(request, GuildFactory()) is False
        assert can_edit_category(request, CategoryFactory(guild=None)) is False
        # A category with a guild defers to can_edit_guild — still denied here.
        assert can_edit_category(request, CategoryFactory(guild=GuildFactory())) is False

    def it_denies_an_authenticated_request_with_no_view_as():
        request = RequestFactory().get("/")
        request.user = UserFactory(username="no-viewas@example.com")
        assert can_edit_class(request, ClassOfferingFactory()) is False


def describe_request_helpers_via_views():
    def it_lets_a_guild_officer_edit_any_guilds_class(client):
        user = UserFactory(username="officer@example.com")
        member = user.member
        member.fog_role = Member.FogRole.GUILD_OFFICER
        member.save(update_fields=["fog_role"])
        member.sync_user_permissions()
        offering = ClassOfferingFactory(category=CategoryFactory(guild=GuildFactory()))
        ct = ContentType.objects.get_for_model(offering)

        client.force_login(user)
        response = client.post(
            "/hero-adjust/",
            {"content_type_id": ct.id, "object_id": offering.id, "crop": {"x": 1, "y": 1, "w": 1, "h": 1}},
            content_type="application/json",
        )

        assert response.status_code == 200
