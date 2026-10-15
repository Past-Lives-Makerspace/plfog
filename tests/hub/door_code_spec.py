"""BDD specs for #813: the Door Code card on Home and its admin only Change save.

Covers who sees the code (active members and admins only; never an unlinked account, a guest,
an invited, former or suspended member, nor an admin previewing as Guest), the Change control
for admins alone, the save recording who and when, a crafted POST from anyone else, the 1 to 20
character trimmed validation and the no code state.

The repo is public: every code here is an obviously fake value.
"""

from __future__ import annotations

from datetime import datetime
from datetime import timezone as dt_timezone

import pytest
from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core.models import SiteConfiguration
from hub.door_code import BLANK_MESSAGE, DoorCodeCard
from membership.models import Member
from tests.membership.factories import GuildFactory, MembershipPlanFactory

pytestmark = pytest.mark.django_db

HOME = reverse("hub_home")
SAVE = reverse("hub_admin_door_code")
FAKE_CODE = "4242#"
CARD = b"data-door-code-card"
CHANGE = b"data-door-code-edit"
CODE_MARKUP = b"data-door-code>4242#</p>"


def _login(
    client: Client,
    username: str,
    *,
    fog_role: str = Member.FogRole.MEMBER,
    status: str = Member.Status.ACTIVE,
    preferred_name: str = "",
) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="pass")
    member = user.member
    member.fog_role = fog_role
    member.status = status
    member.preferred_name = preferred_name
    member.save(update_fields=["fog_role", "status", "preferred_name"])
    member.sync_user_permissions()
    client.login(username=username, password="pass")
    return user


def _admin(client: Client, username: str = "dooradmin", preferred_name: str = "Zorblax Admin") -> User:
    return _login(client, username, fog_role=Member.FogRole.ADMIN, preferred_name=preferred_name)


def _preview(client: Client, role: str) -> None:
    session = client.session
    session["view_as_role"] = role
    session.save()


def _set_code(code: str = FAKE_CODE, *, by: User | None = None, at: datetime | None = None) -> SiteConfiguration:
    config = SiteConfiguration.load()
    config.door_code = code
    config.door_code_changed_by = by
    config.door_code_changed_at = at or datetime(2026, 10, 3, 18, 0, tzinfo=dt_timezone.utc)
    config.save()
    return config


def _setter() -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username="setter", email="setter@example.com", password="pass")
    user.member.preferred_name = "Quillon Setter"
    user.member.save(update_fields=["preferred_name"])
    return user


def describe_an_active_member():
    def it_sees_the_card_with_the_code_and_when_it_changed(client: Client):
        _set_code(by=_setter())
        _login(client, "doormember")

        content = client.get(HOME).content

        assert CARD in content
        assert CODE_MARKUP in content
        assert b"data-door-code-changed>Changed Oct 3, 2026</p>" in content

    def it_does_not_see_the_change_control_or_who_changed_it(client: Client):
        _set_code(by=_setter())
        _login(client, "doormember")

        content = client.get(HOME).content

        assert CHANGE not in content
        assert b'id="door-code-change-title"' not in content
        assert b"data-door-code-form" not in content
        assert b"Quillon Setter" not in content

    def it_sees_the_card_as_a_guild_officer_too(client: Client):
        _set_code()
        _login(client, "doorofficer", fog_role=Member.FogRole.GUILD_OFFICER)

        content = client.get(HOME).content

        assert CODE_MARKUP in content
        assert CHANGE not in content

    def it_sees_no_card_while_no_code_is_set(client: Client):
        _login(client, "doormember")

        content = client.get(HOME).content

        assert CARD not in content
        assert b"data-door-code-empty" not in content

    def it_sees_no_changed_line_when_the_code_has_no_date(client: Client):
        config = SiteConfiguration.load()
        config.door_code = FAKE_CODE
        config.save()
        _login(client, "doormember")

        content = client.get(HOME).content

        assert CODE_MARKUP in content
        assert b"data-door-code-changed" not in content


def describe_an_admin():
    def it_sees_the_change_control_and_the_changers_name(client: Client):
        _set_code(by=_setter())
        _admin(client)

        content = client.get(HOME).content

        assert CODE_MARKUP in content
        assert CHANGE in content
        assert b">Change</button>" in content
        assert b'id="door-code-change-title"' in content
        assert b"Changed Oct 3, 2026 by Quillon Setter</p>" in content
        assert b'value="4242#"' in content

    def it_sees_the_date_alone_when_the_changer_account_is_gone(client: Client):
        setter = _setter()
        _set_code(by=setter)
        setter.delete()
        _admin(client)

        content = client.get(HOME).content

        assert b"data-door-code-changed>Changed Oct 3, 2026</p>" in content

    def it_sees_the_card_with_a_prompt_while_no_code_is_set(client: Client):
        _admin(client)

        content = client.get(HOME).content

        assert CARD in content
        assert b"data-door-code-empty" in content
        assert b">Set Code</button>" in content
        assert b"data-door-code>" not in content

    def it_sees_the_change_control_as_a_superuser_with_a_member(client: Client):
        _set_code()
        user = _login(client, "doorsuper")
        User.objects.filter(pk=user.pk).update(is_superuser=True, is_staff=True)

        content = client.get(HOME).content

        assert CHANGE in content

    def describe_previewing_another_role():
        def it_sees_the_code_without_change_as_a_member(client: Client):
            _set_code(by=_setter())
            _admin(client)
            _preview(client, "member")

            content = client.get(HOME).content

            assert CODE_MARKUP in content
            assert CHANGE not in content
            assert b"Quillon Setter" not in content

        def it_sees_no_card_as_a_guest(client: Client):
            _set_code()
            _admin(client)
            _preview(client, "guest")

            content = client.get(HOME).content

            assert CARD not in content
            assert FAKE_CODE.encode() not in content

        def it_sees_the_change_control_when_viewing_as_admin(client: Client):
            _set_code()
            _admin(client)
            _preview(client, "admin")

            assert CHANGE in client.get(HOME).content


def describe_who_never_sees_the_code():
    def it_hides_it_from_an_unlinked_account(client: Client):
        _set_code()
        user = User.objects.create_user(username="doororphan", password="pass")
        Member.objects.filter(user=user).delete()
        client.login(username="doororphan", password="pass")

        response = client.get(HOME)

        assert response.status_code == 200
        assert FAKE_CODE.encode() not in response.content
        assert CARD not in response.content

    @pytest.mark.parametrize(
        "status",
        [Member.Status.GUEST, Member.Status.FORMER, Member.Status.SUSPENDED, Member.Status.INVITED],
    )
    def it_hides_it_from_a_member_who_is_not_active(client: Client, status: str):
        _set_code()
        _login(client, f"door{status}", status=status)

        response = client.get(HOME, follow=True)

        assert FAKE_CODE.encode() not in response.content
        assert CARD not in response.content

    @pytest.mark.parametrize("status", [Member.Status.SUSPENDED, Member.Status.INVITED])
    def it_hides_it_on_home_itself_when_the_lockout_lets_them_in(client: Client, status: str):
        config = _set_code()
        config.suspended_members_locked_out = False
        config.save()
        _login(client, f"door{status}", status=status)

        response = client.get(HOME)

        assert response.status_code == 200
        assert b"Welcome back" in response.content
        assert FAKE_CODE.encode() not in response.content
        assert CARD not in response.content


def describe_saving_a_new_code():
    def it_saves_and_every_member_sees_it_on_the_next_load(client: Client):
        _set_code("1111#")
        admin = _admin(client)
        before = timezone.now()

        response = client.post(SAVE, {"door_code": FAKE_CODE})

        assert response.status_code == 302
        assert response["Location"] == HOME
        config = SiteConfiguration.load()
        assert config.door_code == FAKE_CODE
        assert config.door_code_changed_by == admin
        assert config.door_code_changed_at is not None
        assert config.door_code_changed_at >= before
        assert [str(m) for m in get_messages(response.wsgi_request)] == ["Door code saved. Members see it now."]

        member_client = Client()
        _login(member_client, "doorreader")
        assert CODE_MARKUP in member_client.get(HOME).content

    def it_shows_admins_the_name_of_the_admin_who_saved(client: Client):
        _admin(client)

        client.post(SAVE, {"door_code": FAKE_CODE})
        content = client.get(HOME).content

        assert b" by Zorblax Admin</p>" in content

    def it_trims_the_code(client: Client):
        _admin(client)

        client.post(SAVE, {"door_code": "   4242#  "})

        assert SiteConfiguration.load().door_code == FAKE_CODE

    def it_accepts_twenty_characters(client: Client):
        _admin(client)

        client.post(SAVE, {"door_code": "A1#" * 6 + "Z9"})

        assert SiteConfiguration.load().door_code == "A1#A1#A1#A1#A1#A1#Z9"

    def it_saves_only_the_door_code_columns(client: Client):
        config = SiteConfiguration.load()
        config.spotlight_first_line = "Zorblax line"
        config.save()
        _admin(client)
        stale = SiteConfiguration.load()
        SiteConfiguration.objects.filter(pk=1).update(spotlight_first_line="Another admin edit")

        stale.set_door_code(FAKE_CODE, by=User.objects.get(username="dooradmin"), now=timezone.now())

        assert SiteConfiguration.load().spotlight_first_line == "Another admin edit"

    def describe_refusals():
        @pytest.mark.parametrize("value", ["", "    "])
        def it_refuses_a_blank_code_with_a_plain_message(client: Client, value: str):
            _set_code("1111#")
            _admin(client)

            response = client.post(SAVE, {"door_code": value})

            assert response.status_code == 302
            assert response["Location"] == HOME
            assert [str(m) for m in get_messages(response.wsgi_request)] == [BLANK_MESSAGE]
            assert BLANK_MESSAGE == "Enter a door code."
            assert SiteConfiguration.load().door_code == "1111#"

        def it_refuses_a_missing_field(client: Client):
            _set_code("1111#")
            _admin(client)

            client.post(SAVE, {})

            assert SiteConfiguration.load().door_code == "1111#"

        def it_refuses_more_than_twenty_characters(client: Client):
            _set_code("1111#")
            _admin(client)

            response = client.post(SAVE, {"door_code": "1" * 21})

            assert [str(m) for m in get_messages(response.wsgi_request)] == [
                "Keep the door code to 20 characters or fewer."
            ]
            assert SiteConfiguration.load().door_code == "1111#"

        def it_answers_a_get_with_405(client: Client):
            _admin(client)

            assert client.get(SAVE).status_code == 405


def describe_a_crafted_post_from_a_non_admin():
    @pytest.mark.parametrize(
        "fog_role",
        [Member.FogRole.MEMBER, Member.FogRole.GUILD_OFFICER],
    )
    def it_is_refused_and_changes_nothing(client: Client, fog_role: str):
        _set_code("1111#")
        _login(client, f"doorcraft{fog_role}", fog_role=fog_role)

        response = client.post(SAVE, {"door_code": FAKE_CODE})

        assert response.status_code == 403
        config = SiteConfiguration.load()
        assert config.door_code == "1111#"
        assert config.door_code_changed_by is None

    def it_is_refused_for_a_guild_lead(client: Client):
        _set_code("1111#")
        user = _login(client, "doorlead")
        GuildFactory(guild_lead=user.member)

        assert client.post(SAVE, {"door_code": FAKE_CODE}).status_code == 403
        assert SiteConfiguration.load().door_code == "1111#"

    def it_is_refused_for_an_unlinked_account(client: Client):
        _set_code("1111#")
        user = User.objects.create_user(username="doorcraftorphan", password="pass")
        Member.objects.filter(user=user).delete()
        client.login(username="doorcraftorphan", password="pass")

        assert client.post(SAVE, {"door_code": FAKE_CODE}).status_code == 403
        assert SiteConfiguration.load().door_code == "1111#"

    def it_sends_a_signed_out_visitor_to_login(client: Client):
        _set_code("1111#")

        response = client.post(SAVE, {"door_code": FAKE_CODE})

        assert response.status_code == 302
        assert "/accounts/login/" in response["Location"]
        assert SiteConfiguration.load().door_code == "1111#"

    def it_still_lets_an_admin_previewing_as_a_member_save(client: Client):
        # The gate reads the actual role, like every fog_admin_required view: a preview
        # changes what the page shows, never what the account may do.
        _admin(client)
        _preview(client, "member")

        assert client.post(SAVE, {"door_code": FAKE_CODE}).status_code == 302
        assert SiteConfiguration.load().door_code == FAKE_CODE


def describe_door_code_card():
    def it_is_shown_to_members_only_once_a_code_is_set():
        assert DoorCodeCard(code="", changed_at=None, can_change=False).is_shown is False
        assert DoorCodeCard(code=FAKE_CODE, changed_at=None, can_change=False).is_shown is True

    def it_is_always_shown_to_admins():
        assert DoorCodeCard(code="", changed_at=None, can_change=True).is_shown is True
