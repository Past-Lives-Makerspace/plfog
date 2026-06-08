"""BDD specs for the Viewing-as helper."""

from __future__ import annotations

import json

import pytest
from django.contrib.auth.models import AnonymousUser, User
from django.test import RequestFactory

from hub.view_as import (
    ROLE_ADMIN,
    ROLE_GUILD_OFFICER,
    ROLE_MEMBER,
    ViewAs,
    ViewAsMiddleware,
    compute_actual_roles,
)
from membership.models import Member


@pytest.fixture
def rf() -> RequestFactory:
    return RequestFactory()


def _make_user_member(fog_role: str, *, username: str = "u") -> tuple[User, Member]:
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="p")
    member = user.member
    member.fog_role = fog_role
    member.save(update_fields=["fog_role"])
    return user, member


@pytest.mark.django_db
def describe_compute_actual_roles():
    def it_returns_guest_for_anonymous_user():
        from hub.view_as import ROLE_GUEST

        assert compute_actual_roles(AnonymousUser()) == frozenset({ROLE_GUEST})

    def it_returns_guest_when_user_has_no_member_or_instructor():
        from hub.view_as import ROLE_GUEST

        user = User.objects.create_user(username="u", password="p")
        Member.objects.filter(user=user).delete()
        user = User.objects.get(pk=user.pk)
        assert compute_actual_roles(user) == frozenset({ROLE_GUEST})

    def it_returns_admin_guild_officer_and_member_for_fog_admin():
        user, _ = _make_user_member(Member.FogRole.ADMIN, username="admin_u")
        assert compute_actual_roles(user) == frozenset({ROLE_ADMIN, ROLE_GUILD_OFFICER, ROLE_MEMBER})

    def it_returns_guild_officer_and_member_for_fog_officer():
        user, _ = _make_user_member(Member.FogRole.GUILD_OFFICER, username="officer_u")
        assert compute_actual_roles(user) == frozenset({ROLE_GUILD_OFFICER, ROLE_MEMBER})

    def it_returns_only_member_for_regular_members():
        user, _ = _make_user_member(Member.FogRole.MEMBER, username="member_u")
        assert compute_actual_roles(user) == frozenset({ROLE_MEMBER})

    def it_treats_django_superuser_without_member_as_admin():
        user = User.objects.create_superuser(username="root", email="r@x.com", password="p")
        Member.objects.filter(user=user).delete()
        user = User.objects.get(pk=user.pk)
        assert compute_actual_roles(user) == frozenset({ROLE_ADMIN, ROLE_GUILD_OFFICER, ROLE_MEMBER})


def describe_ViewAs():
    def describe_default_view_as_role():
        def it_picks_the_highest_actual_role_when_nothing_is_picked():
            v = ViewAs(actual=frozenset({ROLE_ADMIN, ROLE_GUILD_OFFICER, ROLE_MEMBER}), picked=None)
            assert v.view_as_role == ROLE_ADMIN
            assert v.effective == frozenset({ROLE_ADMIN, ROLE_GUILD_OFFICER, ROLE_MEMBER})

    def describe_effective_roles():
        def it_caps_effective_to_roles_at_or_below_picked():
            v = ViewAs(actual=frozenset({ROLE_ADMIN, ROLE_GUILD_OFFICER, ROLE_MEMBER}), picked=ROLE_GUILD_OFFICER)
            assert v.is_admin is False
            assert v.is_guild_officer is True
            assert v.is_member is True

        def it_picking_member_reduces_effective_to_just_member():
            v = ViewAs(actual=frozenset({ROLE_ADMIN, ROLE_GUILD_OFFICER, ROLE_MEMBER}), picked=ROLE_MEMBER)
            assert v.is_admin is False
            assert v.is_member is True

    def describe_has_actual():
        def it_reports_true_for_roles_the_user_holds_regardless_of_pick():
            v = ViewAs(actual=frozenset({ROLE_ADMIN, ROLE_MEMBER}), picked=ROLE_MEMBER)
            assert v.has_actual(ROLE_ADMIN) is True
            assert v.has(ROLE_ADMIN) is False

    def describe_show_dropdown():
        def it_is_true_for_admins():
            v = ViewAs(actual=frozenset({ROLE_ADMIN, ROLE_GUILD_OFFICER, ROLE_MEMBER}), picked=None)
            assert v.show_dropdown is True

        def it_is_true_for_guild_officers():
            v = ViewAs(actual=frozenset({ROLE_GUILD_OFFICER, ROLE_MEMBER}), picked=None)
            assert v.show_dropdown is True

        def it_is_false_for_plain_members():
            v = ViewAs(actual=frozenset({ROLE_MEMBER}), picked=None)
            assert v.show_dropdown is False

    def describe_dropdown_options():
        def it_lists_every_role_for_admins_so_they_can_preview():
            from hub.view_as import ROLE_GUEST

            v = ViewAs(actual=frozenset({ROLE_ADMIN, ROLE_GUILD_OFFICER, ROLE_MEMBER}), picked=ROLE_GUILD_OFFICER)
            names = [row["name"] for row in v.dropdown_options]
            assert names == [ROLE_ADMIN, ROLE_GUILD_OFFICER, ROLE_MEMBER, ROLE_GUEST]
            selected = [row["name"] for row in v.dropdown_options if row["selected"]]
            assert selected == [ROLE_GUILD_OFFICER]

        def it_skips_roles_not_actually_held_for_non_admins():
            v = ViewAs(actual=frozenset({ROLE_GUILD_OFFICER, ROLE_MEMBER}), picked=None)
            names = [row["name"] for row in v.dropdown_options]
            assert names == [ROLE_GUILD_OFFICER, ROLE_MEMBER]


@pytest.mark.django_db
def describe_ViewAsMiddleware():
    def it_attaches_view_as_to_request(rf: RequestFactory):
        user, _ = _make_user_member(Member.FogRole.ADMIN, username="mw_admin")
        request = rf.get("/")
        request.user = user
        request.session = {}

        captured: dict[str, object] = {}

        def get_response(req):
            captured["view_as"] = req.view_as
            return "ok"

        ViewAsMiddleware(get_response)(request)

        assert captured["view_as"].view_as_role == ROLE_ADMIN
        assert captured["view_as"].is_admin is True

    def it_respects_picked_role_in_session(rf: RequestFactory):
        user, _ = _make_user_member(Member.FogRole.ADMIN, username="mw_picked")
        request = rf.get("/")
        request.user = user
        request.session = {"view_as_role": "guild_officer"}

        captured: dict[str, object] = {}

        def get_response(req):
            captured["view_as"] = req.view_as
            return "ok"

        ViewAsMiddleware(get_response)(request)

        assert captured["view_as"].view_as_role == ROLE_GUILD_OFFICER
        assert captured["view_as"].is_admin is False

    def it_ignores_picked_role_the_user_does_not_hold(rf: RequestFactory):
        user, _ = _make_user_member(Member.FogRole.MEMBER, username="mw_rogue")
        request = rf.get("/")
        request.user = user
        request.session = {"view_as_role": "admin"}

        captured: dict[str, object] = {}

        def get_response(req):
            captured["view_as"] = req.view_as
            return "ok"

        ViewAsMiddleware(get_response)(request)

        assert captured["view_as"].is_admin is False


@pytest.mark.django_db
def describe_view_as_set_endpoint():
    def it_sets_picked_role_in_session(client):
        user, _ = _make_user_member(Member.FogRole.ADMIN, username="set_admin")
        client.login(username=user.username, password="p")

        response = client.post(
            "/view-as/set/",
            data=json.dumps({"role": "guild_officer"}),
            content_type="application/json",
        )

        assert response.status_code == 200
        assert client.session["view_as_role"] == "guild_officer"

    def it_rejects_unknown_role_names(client):
        user, _ = _make_user_member(Member.FogRole.ADMIN, username="set_wizard")
        client.login(username=user.username, password="p")

        response = client.post(
            "/view-as/set/",
            data=json.dumps({"role": "wizard"}),
            content_type="application/json",
        )

        assert response.status_code == 400

    def it_rejects_viewing_as_a_role_the_user_does_not_hold(client):
        user, _ = _make_user_member(Member.FogRole.MEMBER, username="set_plain")
        client.login(username=user.username, password="p")

        response = client.post(
            "/view-as/set/",
            data=json.dumps({"role": "admin"}),
            content_type="application/json",
        )

        assert response.status_code == 403

    def it_lets_admins_preview_any_role(client):
        from hub.view_as import SESSION_ROLE_KEY

        user, _ = _make_user_member(Member.FogRole.ADMIN, username="set_preview")
        client.login(username=user.username, password="p")
        for preview_role in ("guest", "member", "guild_officer"):
            response = client.post(
                "/view-as/set/",
                data=json.dumps({"role": preview_role}),
                content_type="application/json",
            )
            assert response.status_code == 200, f"admin blocked from previewing {preview_role}"
            assert client.session[SESSION_ROLE_KEY] == preview_role


@pytest.mark.django_db
def describe_dropdown_in_hub_template():
    def it_renders_dropdown_for_admins(client):
        user, _ = _make_user_member(Member.FogRole.ADMIN, username="tmpl_admin")
        client.login(username=user.username, password="p")

        response = client.get("/guilds/voting/")

        assert response.status_code == 200
        assert b"pl-view-as-popover" in response.content
        assert b"Viewing as: Admin" in response.content

    def it_hides_dropdown_for_plain_members(client):
        user, _ = _make_user_member(Member.FogRole.MEMBER, username="tmpl_plain")
        client.login(username=user.username, password="p")

        response = client.get("/guilds/voting/")

        assert response.status_code == 200
        assert b"pl-view-as-popover" not in response.content
