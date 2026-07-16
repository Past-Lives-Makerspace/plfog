"""BDD specs for must_be_listed_in_directory + related role-derived properties."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User

from classes.factories import InstructorFactory
from membership.models import Member
from tests.membership.factories import GuildFactory, MemberFactory

pytestmark = pytest.mark.django_db


def describe_must_be_listed_in_directory():
    def it_is_true_for_admins():
        member = MemberFactory(fog_role=Member.FogRole.ADMIN)
        assert member.must_be_listed_in_directory is True

    def it_is_true_for_guild_officers():
        member = MemberFactory(fog_role=Member.FogRole.GUILD_OFFICER)
        assert member.must_be_listed_in_directory is True

    def it_is_true_for_guild_leads():
        member = MemberFactory(fog_role=Member.FogRole.MEMBER)
        GuildFactory(guild_lead=member)
        assert member.is_guild_lead is True
        assert member.must_be_listed_in_directory is True

    def it_is_true_for_instructors():
        user = User.objects.create_user(username="teach", email="teach@x.com", password="p")
        InstructorFactory(user=user)
        member = user.member
        member.refresh_from_db()
        member.fog_role = Member.FogRole.MEMBER
        member.save(update_fields=["fog_role"])
        assert member.is_instructor is True
        assert member.must_be_listed_in_directory is True

    def it_is_false_for_plain_members():
        member = MemberFactory(fog_role=Member.FogRole.MEMBER)
        assert member.is_guild_lead is False
        assert member.is_instructor is False
        assert member.must_be_listed_in_directory is False

    def it_returns_false_for_instructor_check_when_user_is_unlinked():
        member = MemberFactory(fog_role=Member.FogRole.MEMBER, user=None)
        assert member.is_instructor is False


def describe_profile_settings_form_protects_locked_roles():
    def it_force_shows_admins_in_directory_even_if_post_says_false(client):
        from hub.forms import ProfileSettingsForm

        member = MemberFactory(fog_role=Member.FogRole.ADMIN, show_in_directory=False)
        form = ProfileSettingsForm(
            data={
                "preferred_name": "",
                "pronouns": "",
                "phone": "",
                "discord_handle": "",
                "about_me": "",
                "show_in_directory": "",
            },
            instance=member,
        )
        assert form.is_valid(), form.errors
        saved = form.save()
        assert saved.show_in_directory is True

    def it_disables_the_field_for_protected_roles():
        from hub.forms import ProfileSettingsForm

        member = MemberFactory(fog_role=Member.FogRole.GUILD_OFFICER)
        form = ProfileSettingsForm(instance=member)
        assert form.fields["show_in_directory"].disabled is True

    def it_lets_plain_members_hide_themselves():
        from hub.forms import ProfileSettingsForm

        member = MemberFactory(fog_role=Member.FogRole.MEMBER, show_in_directory=True)
        form = ProfileSettingsForm(
            data={
                "preferred_name": "",
                "pronouns": "",
                "phone": "",
                "discord_handle": "",
                "about_me": "",
                "show_in_directory": "",
            },
            instance=member,
        )
        assert form.is_valid(), form.errors
        saved = form.save()
        assert saved.show_in_directory is False


def describe_member_directory_shows_protected_roles_to_non_admins():
    def it_includes_admins_who_marked_themselves_hidden(client):
        viewer_user = User.objects.create_user(username="viewer", email="v@x.com", password="p")
        viewer_member = viewer_user.member
        viewer_member.fog_role = Member.FogRole.MEMBER
        viewer_member.save(update_fields=["fog_role"])
        client.login(username="viewer", password="p")

        hidden_admin = MemberFactory(
            full_legal_name="Hidden Admin",
            fog_role=Member.FogRole.ADMIN,
            show_in_directory=False,
        )
        Member.objects.filter(pk=hidden_admin.pk).update(show_in_directory=False)
        response = client.get("/members/")
        assert response.status_code == 200
        assert b"Hidden Admin" in response.content
