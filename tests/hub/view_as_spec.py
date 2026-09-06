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
def describe_view_as_capability_set_endpoint():
    def it_lets_an_admin_grant_their_own_capability(client):
        user, member = _make_user_member(Member.FogRole.ADMIN, username="cap_grant")
        client.login(username=user.username, password="p")

        response = client.post(
            "/view-as/capability/set/",
            data=json.dumps({"capability": "billing_approver", "enabled": True}),
            content_type="application/json",
        )

        assert response.status_code == 200
        row = member.admin_capabilities.get(capability="billing_approver")
        assert row.granted_by == user

    def it_lets_an_admin_revoke_their_own_capability(client):
        user, member = _make_user_member(Member.FogRole.ADMIN, username="cap_revoke")
        member.admin_capabilities.create(capability="refunds")
        client.login(username=user.username, password="p")

        response = client.post(
            "/view-as/capability/set/",
            data=json.dumps({"capability": "refunds", "enabled": False}),
            content_type="application/json",
        )

        assert response.status_code == 200
        assert member.admin_capabilities.filter(capability="refunds").exists() is False

    def it_rejects_a_non_admin_member_and_creates_no_row(client):
        user, member = _make_user_member(Member.FogRole.MEMBER, username="cap_attacker")
        client.login(username=user.username, password="p")

        response = client.post(
            "/view-as/capability/set/",
            data=json.dumps({"capability": "billing_approver", "enabled": True}),
            content_type="application/json",
        )

        assert response.status_code == 403
        assert member.admin_capabilities.count() == 0

    def it_does_not_let_a_view_as_admin_preview_escalate_a_non_admin(client):
        # A non-admin with a crafted session view-as of "admin" still can't grant:
        # the gate reads the ACTUAL role, so the preview illusion grants nothing.
        user, member = _make_user_member(Member.FogRole.MEMBER, username="cap_preview")
        client.login(username=user.username, password="p")
        session = client.session
        session["view_as_role"] = "admin"
        session.save()

        response = client.post(
            "/view-as/capability/set/",
            data=json.dumps({"capability": "billing_approver", "enabled": True}),
            content_type="application/json",
        )

        assert response.status_code == 403
        assert member.admin_capabilities.count() == 0

    def it_rejects_an_unknown_capability(client):
        user, member = _make_user_member(Member.FogRole.ADMIN, username="cap_bad")
        client.login(username=user.username, password="p")

        response = client.post(
            "/view-as/capability/set/",
            data=json.dumps({"capability": "not_a_capability", "enabled": True}),
            content_type="application/json",
        )

        assert response.status_code == 400
        assert member.admin_capabilities.count() == 0

    def it_only_touches_the_callers_own_member(client):
        user, member = _make_user_member(Member.FogRole.ADMIN, username="cap_self")
        _, other_member = _make_user_member(Member.FogRole.ADMIN, username="cap_other")
        client.login(username=user.username, password="p")

        # A smuggled target member id must be ignored — only the caller's own member changes.
        response = client.post(
            "/view-as/capability/set/",
            data=json.dumps({"capability": "billing_approver", "enabled": True, "member": other_member.pk}),
            content_type="application/json",
        )

        assert response.status_code == 200
        assert member.has_admin_capability("billing_approver") is True
        assert other_member.has_admin_capability("billing_approver") is False


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

    def it_renders_capability_toggles_for_an_actual_admin(client):
        user, _ = _make_user_member(Member.FogRole.ADMIN, username="tmpl_cap_admin")
        client.login(username=user.username, password="p")

        response = client.get("/guilds/voting/")

        assert response.status_code == 200
        assert b"Your admin duties" in response.content
        assert b"Billing Administrator" in response.content

    def it_omits_capability_toggles_for_a_non_admin_who_still_sees_the_dropdown(client):
        # A guild officer gets the view-as dropdown (a downgrade tool) but is NOT an
        # actual admin, so the self-service duty toggles must be absent.
        user, _ = _make_user_member(Member.FogRole.GUILD_OFFICER, username="tmpl_cap_officer")
        client.login(username=user.username, password="p")

        response = client.get("/guilds/voting/")

        assert response.status_code == 200
        assert b"pl-view-as-popover" in response.content
        assert b"Your admin duties" not in response.content


@pytest.mark.django_db
def describe_view_as_instructor_set_endpoint():
    def it_lets_an_admin_grant_their_own_instructor_permission(client):
        user, member = _make_user_member(Member.FogRole.ADMIN, username="inst_grant")
        client.login(username=user.username, password="p")

        response = client.post(
            "/view-as/instructor/set/",
            data=json.dumps({"enabled": True}),
            content_type="application/json",
        )

        assert response.status_code == 200
        member.refresh_from_db()
        assert member.is_instructor is True
        assert member.can_create_classes is True

    def it_lets_an_admin_revoke_their_own_instructor_permission(client):
        user, member = _make_user_member(Member.FogRole.ADMIN, username="inst_revoke")
        member.grant_instructor(granted_by=member)
        client.login(username=user.username, password="p")

        response = client.post(
            "/view-as/instructor/set/",
            data=json.dumps({"enabled": False}),
            content_type="application/json",
        )

        assert response.status_code == 200
        member.refresh_from_db()
        assert member.is_instructor is False
        assert member.can_create_classes is False

    def it_rejects_a_non_admin_member_and_grants_nothing(client):
        user, member = _make_user_member(Member.FogRole.MEMBER, username="inst_attacker")
        client.login(username=user.username, password="p")

        response = client.post(
            "/view-as/instructor/set/",
            data=json.dumps({"enabled": True}),
            content_type="application/json",
        )

        assert response.status_code == 403
        member.refresh_from_db()
        assert member.is_instructor is False

    def it_does_not_let_a_view_as_admin_preview_escalate_a_non_admin(client):
        # The gate reads the ACTUAL role, so a crafted session preview grants nothing.
        user, member = _make_user_member(Member.FogRole.MEMBER, username="inst_preview")
        client.login(username=user.username, password="p")
        session = client.session
        session["view_as_role"] = "admin"
        session.save()

        response = client.post(
            "/view-as/instructor/set/",
            data=json.dumps({"enabled": True}),
            content_type="application/json",
        )

        assert response.status_code == 403
        member.refresh_from_db()
        assert member.is_instructor is False

    def it_rejects_a_body_with_no_enabled_flag(client):
        user, member = _make_user_member(Member.FogRole.ADMIN, username="inst_bad")
        client.login(username=user.username, password="p")

        response = client.post(
            "/view-as/instructor/set/",
            data=json.dumps({"nope": True}),
            content_type="application/json",
        )

        assert response.status_code == 400
        member.refresh_from_db()
        assert member.is_instructor is False

    def it_only_touches_the_callers_own_member(client):
        user, member = _make_user_member(Member.FogRole.ADMIN, username="inst_self")
        _, other_member = _make_user_member(Member.FogRole.ADMIN, username="inst_other")
        client.login(username=user.username, password="p")

        # A smuggled target member id must be ignored — only the caller's own member changes.
        response = client.post(
            "/view-as/instructor/set/",
            data=json.dumps({"enabled": True, "member": other_member.pk}),
            content_type="application/json",
        )

        assert response.status_code == 200
        member.refresh_from_db()
        other_member.refresh_from_db()
        assert member.is_instructor is True
        assert other_member.is_instructor is False

    def it_403s_an_admin_user_with_no_linked_member(client):
        user, member = _make_user_member(Member.FogRole.ADMIN, username="inst_nomember")
        user.is_superuser = True  # keeps the admin role once the Member row is gone
        user.save(update_fields=["is_superuser"])
        member.delete()
        client.login(username=user.username, password="p")

        response = client.post(
            "/view-as/instructor/set/",
            data=json.dumps({"enabled": True}),
            content_type="application/json",
        )

        assert response.status_code == 403
        # Distinguishes this gate from the admin-role gate above, which 403s with its own message.
        assert response.json()["error"] == "No member on this account."


def _toggle_input(body: str, handler: str) -> str:
    """The rendered ``<input>`` tag whose @change calls ``handler`` (e.g. ``setInstructor``)."""
    import re

    match = re.search(rf"<input[^>]*{handler}\([^>]*>", body, re.S)
    assert match is not None, f"no toggle input calling {handler}"
    return match.group(0)


@pytest.mark.django_db
def describe_instructor_toggle_in_hub_template():
    def it_renders_the_instructor_toggle_above_the_admin_duties(client):
        user, _ = _make_user_member(Member.FogRole.ADMIN, username="tmpl_inst_admin")
        client.login(username=user.username, password="p")

        response = client.get("/guilds/voting/")

        assert response.status_code == 200
        body = response.content.decode()
        assert Member.INSTRUCTOR_PERMISSION_DESCRIPTION in body
        instructor_header = '<div class="pl-view-as-popover__header">Instructor</div>'
        duties_header = '<div class="pl-view-as-popover__header">Your admin duties</div>'
        assert body.index(instructor_header) < body.index(duties_header)

    def it_renders_the_toggles_unchecked_for_permissions_the_admin_does_not_hold(client):
        user, _ = _make_user_member(Member.FogRole.ADMIN, username="tmpl_inst_unchecked")
        client.login(username=user.username, password="p")

        response = client.get("/guilds/voting/")

        assert response.status_code == 200
        body = response.content.decode()
        # `checked` also appears inside the @change expression, so match the attribute position.
        assert 'type="checkbox" checked' not in _toggle_input(body, "setInstructor")
        assert 'type="checkbox" checked' not in _toggle_input(body, "setCapability")

    def it_renders_the_toggles_checked_for_permissions_the_admin_holds(client):
        user, member = _make_user_member(Member.FogRole.ADMIN, username="tmpl_inst_checked")
        member.grant_instructor(granted_by=member)
        member.admin_capabilities.create(capability="class_approver")
        client.login(username=user.username, password="p")

        response = client.get("/guilds/voting/")

        assert response.status_code == 200
        body = response.content.decode()
        assert 'type="checkbox" checked' in _toggle_input(body, "setInstructor")
        assert 'type="checkbox" checked' in _toggle_input(body, "setCapability")

    def it_omits_the_instructor_toggle_for_a_non_admin_who_still_sees_the_dropdown(client):
        user, _ = _make_user_member(Member.FogRole.GUILD_OFFICER, username="tmpl_inst_officer")
        client.login(username=user.username, password="p")

        response = client.get("/guilds/voting/")

        assert response.status_code == 200
        assert b"pl-view-as-popover" in response.content
        assert Member.INSTRUCTOR_PERMISSION_DESCRIPTION not in response.content.decode()

    def it_renders_a_help_tooltip_for_every_permission(client):
        from django.utils.html import escape

        from membership.models import AdminCapability

        user, _ = _make_user_member(Member.FogRole.ADMIN, username="tmpl_cap_tips")
        client.login(username=user.username, password="p")

        response = client.get("/guilds/voting/")

        assert response.status_code == 200
        body = response.content.decode()
        for description in AdminCapability.DESCRIPTIONS.values():
            bubble = f'<span class="pl-help__bubble">{escape(description)}</span>'
            assert bubble in body, f"missing tooltip copy: {description}"
