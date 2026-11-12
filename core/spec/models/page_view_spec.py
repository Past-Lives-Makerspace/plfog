"""BDD specs for PageView (#1078): the role a view records in one INSERT, readable page names, pruning."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.utils import timezone

from core.models import PageView
from hub.view_as import compute_actual_roles
from membership.models import Member
from tests.membership.factories import GuildFactory, GuildStaffMembershipFactory, MemberFactory

pytestmark = pytest.mark.django_db


def _user_with(member: Member) -> User:
    user = User.objects.create_user(username=f"u{member.pk}", email=f"u{member.pk}@example.com")
    Member.objects.filter(user=user).exclude(pk=member.pk).delete()
    member.user = user
    member.save(update_fields=["user"])
    return user


def _recorded_role(user: User) -> str:
    """Record a view the way the middleware does, with the request's actual roles, and read the role back."""
    PageView.record(
        user=user, actual_roles=compute_actual_roles(user), view_name="hub_home", surface="members", from_app=False
    )
    return PageView.objects.filter(user=user).latest("viewed_at").role


def describe_record():
    def it_records_a_superuser_and_an_admin_member_as_admin():
        assert _recorded_role(User.objects.create_superuser("su", "su@example.com", "p")) == PageView.Role.ADMIN
        assert _recorded_role(_user_with(MemberFactory(fog_role=Member.FogRole.ADMIN))) == PageView.Role.ADMIN

    def it_records_a_lead_or_staff_holder_as_guild_staff():
        lead_member = MemberFactory()
        GuildFactory(guild_lead=lead_member)
        staff_member = MemberFactory()
        GuildStaffMembershipFactory(member=staff_member)

        assert _recorded_role(_user_with(lead_member)) == PageView.Role.GUILD_STAFF
        assert _recorded_role(_user_with(staff_member)) == PageView.Role.GUILD_STAFF

    def it_records_an_instructor_below_guild_staff():
        teacher = MemberFactory(instructor_slug="ada")
        user = _user_with(teacher)
        assert _recorded_role(user) == PageView.Role.INSTRUCTOR
        GuildFactory(guild_lead=teacher)
        assert _recorded_role(user) == PageView.Role.GUILD_STAFF

    def it_records_a_plain_member_as_member():
        assert _recorded_role(_user_with(MemberFactory())) == PageView.Role.MEMBER

    def it_records_an_account_with_no_active_membership_as_a_guest():
        assert _recorded_role(_user_with(MemberFactory(status=Member.Status.FORMER))) == PageView.Role.GUEST

    def it_costs_one_insert_whatever_the_role(django_assert_num_queries):
        teacher = _user_with(MemberFactory(instructor_slug="ada"))
        admin = User.objects.create_superuser("su2", "su2@example.com", "p")
        for user in (teacher, admin):
            roles = compute_actual_roles(user)
            with django_assert_num_queries(1) as captured:
                PageView.record(user=user, actual_roles=roles, view_name="hub_home", surface="members", from_app=False)
            assert captured.captured_queries[0]["sql"].lstrip().upper().startswith("INSERT")


def describe_label_for():
    def it_names_the_common_pages():
        assert PageView.label_for("hub_home") == "Home"
        assert PageView.label_for("classes:public_list") == "Classes"
        assert PageView.label_for("hub_admin_usage") == "Usage"

    def it_humanizes_any_other_view_name():
        assert PageView.label_for("classes:admin_class_detail") == "Class detail"
        assert PageView.label_for("hub_guild_meeting_notes") == "Guild meeting notes"
        assert PageView.label_for("tours:settings") == "Settings"


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
