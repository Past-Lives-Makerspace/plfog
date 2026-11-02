"""BDD specs for PageView (#1078): the role a view records, and pruning raw views."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.utils import timezone

from core.models import PageView
from membership.models import Member
from tests.membership.factories import GuildFactory, GuildStaffMembershipFactory, MemberFactory

pytestmark = pytest.mark.django_db


def _user_with(member: Member) -> User:
    user = User.objects.create_user(username=f"u{member.pk}", email=f"u{member.pk}@example.com")
    Member.objects.filter(user=user).exclude(pk=member.pk).delete()
    member.user = user
    member.save(update_fields=["user"])
    return user


def describe_role_for():
    def it_reads_a_superuser_as_admin():
        assert PageView.role_for(User.objects.create_superuser("su", "su@example.com", "p")) == PageView.Role.ADMIN

    def it_reads_an_admin_member_as_admin():
        assert PageView.role_for(_user_with(MemberFactory(fog_role=Member.FogRole.ADMIN))) == PageView.Role.ADMIN

    def it_reads_a_guild_officer_lead_or_staff_holder_as_guild_staff():
        officer = _user_with(MemberFactory(fog_role=Member.FogRole.GUILD_OFFICER))
        lead_member = MemberFactory()
        GuildFactory(guild_lead=lead_member)
        staff_member = MemberFactory()
        GuildStaffMembershipFactory(member=staff_member)

        assert PageView.role_for(officer) == PageView.Role.GUILD_STAFF
        assert PageView.role_for(_user_with(lead_member)) == PageView.Role.GUILD_STAFF
        assert PageView.role_for(_user_with(staff_member)) == PageView.Role.GUILD_STAFF

    def it_reads_an_instructor_below_guild_staff():
        teacher = MemberFactory(instructor_slug="ada")
        assert PageView.role_for(_user_with(teacher)) == PageView.Role.INSTRUCTOR
        GuildFactory(guild_lead=teacher)
        assert PageView.role_for(teacher.user) == PageView.Role.GUILD_STAFF

    def it_reads_a_plain_member_as_member():
        assert PageView.role_for(_user_with(MemberFactory())) == PageView.Role.MEMBER

    def it_reads_an_account_with_no_active_membership_as_a_guest():
        former = _user_with(MemberFactory(status=Member.Status.FORMER))
        assert PageView.role_for(former) == PageView.Role.GUEST

    def it_asks_the_database_once(django_assert_num_queries):
        user = _user_with(MemberFactory(instructor_slug="ada"))
        with django_assert_num_queries(1):
            PageView.role_for(user)


def describe_prune():
    def it_deletes_only_views_older_than_ninety_days():
        user = User.objects.create_user("p", "p@example.com")
        now = timezone.now()
        PageView.objects.create(
            user=user, view_name="old", role="member", surface="members", viewed_at=now - timedelta(days=91)
        )
        PageView.objects.create(
            user=user, view_name="kept", role="member", surface="members", viewed_at=now - timedelta(days=89)
        )

        assert PageView.prune(now) == 1
        assert list(PageView.objects.values_list("view_name", flat=True)) == ["kept"]
