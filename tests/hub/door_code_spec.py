"""BDD specs for #813: the Door Code on Home and its admin only Change save.

Covers who sees the code (active members and admins only; never an unlinked account, a guest,
an invited, former or suspended member, nor an admin previewing as Guest), the Change control
for admins alone, the save recording who and when, a crafted POST from anyone else, the 1 to 20
character trimmed validation and the no code state. Home renders the code twice, a compact box in
the header (wide screens) and a card under the quick links (narrower screens); CSS shows one.

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
from hub.door_code import BLANK_MESSAGE, DoorCodeView
from membership.models import Member
from tests.membership.factories import GuildFactory, MembershipPlanFactory

pytestmark = pytest.mark.django_db

HOME = reverse("hub_home")
SAVE = reverse("hub_admin_door_code")
FAKE_CODE = "4242#"
BOX = b"data-door-code-box"
CHANGE = b"data-door-code-edit"
CODE_MARKUP = b"data-door-code>4242#</span>"


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
    def it_sees_the_code_and_when_it_changed_in_both_boxes(client: Client):
        _set_code(by=_setter())
        _login(client, "doormember")

        content = client.get(HOME).content

        assert content.count(CODE_MARKUP) == 2
        assert content.count(b"data-door-code-changed>Changed Oct 3, 2026</p>") == 2

    def it_does_not_see_the_change_control_or_who_changed_it(client: Client):
        _set_code(by=_setter())
        _login(client, "doormember")

        content = client.get(HOME).content

        assert CHANGE not in content
        assert b'id="door-code-change-title"' not in content
        assert b"data-door-code-form" not in content
        assert b"Quillon Setter" not in content

    def it_sees_the_code_as_a_guild_officer_too(client: Client):
        _set_code()
        _login(client, "doorofficer", fog_role=Member.FogRole.GUILD_OFFICER)

        content = client.get(HOME).content

        assert content.count(CODE_MARKUP) == 2
        assert CHANGE not in content

    def it_sees_no_door_code_while_no_code_is_set(client: Client):
        _login(client, "doormember")

        content = client.get(HOME).content

        assert BOX not in content
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
        assert content.count(b"Changed Oct 3, 2026 by Quillon Setter</p>") == 2
        assert b'value="4242#"' in content

    def it_sees_the_date_alone_when_the_changer_account_is_gone(client: Client):
        setter = _setter()
        _set_code(by=setter)
        setter.delete()
        _admin(client)

        content = client.get(HOME).content

        assert b"data-door-code-changed>Changed Oct 3, 2026</p>" in content

    def it_sees_both_boxes_with_a_prompt_while_no_code_is_set(client: Client):
        _admin(client)

        content = client.get(HOME).content

        assert content.count(BOX) == 2
        assert content.count(b"data-door-code-empty>No code set yet.</p>") == 2
        assert content.count(b">Set Code</button>") == 2
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

        def it_sees_no_door_code_as_a_guest(client: Client):
            _set_code()
            _admin(client)
            _preview(client, "guest")

            content = client.get(HOME).content

            assert BOX not in content
            assert FAKE_CODE.encode() not in content

        def it_sees_the_change_control_when_viewing_as_admin(client: Client):
            _set_code()
            _admin(client)
            _preview(client, "admin")

            assert CHANGE in client.get(HOME).content


def _element(content: str, marker: str) -> str:
    """The whole ``<div>`` or ``<button>`` that carries ``marker``, nested tags of its name balanced."""
    at = content.index(marker)
    open_at = content.rindex("<", 0, at)
    name = content[open_at + 1 : content.index(" ", open_at)]
    depth, pos = 0, open_at
    while True:
        next_open = content.find(f"<{name}", pos)
        next_close = content.index(f"</{name}>", pos)
        if next_open != -1 and next_open < next_close:
            depth, pos = depth + 1, next_open + len(name) + 1
            continue
        depth, pos = depth - 1, next_close + len(name) + 3
        if depth == 0:
            return content[open_at:pos]


def _box(content: str, variant: str) -> str:
    return _element(content, f'data-door-code-box="{variant}"')


def describe_the_two_boxes():
    @pytest.mark.parametrize("variant", ["header", "card"])
    def it_hides_the_code_on_every_load_in_each(client: Client, variant: str):
        _set_code()
        _login(client, "doorbox")

        box = _box(client.get(HOME).content.decode(), variant)

        assert 'x-data="{ open: false }"' in box.split(">", 1)[0]
        code = _element(box, "data-door-code>")
        assert 'x-show="open"' in code
        assert "x-cloak" in code
        assert code.endswith(">4242#</span>")
        mask = _element(box, "pl-door-code__mask")
        assert 'aria-hidden="true"' in mask
        assert 'x-show="!open"' in mask
        assert FAKE_CODE not in mask

    @pytest.mark.parametrize("variant", ["header", "card"])
    def it_wires_each_toggle_for_assistive_tech(client: Client, variant: str):
        _set_code()
        _login(client, "doorbox")

        toggle = _element(_box(client.get(HOME).content.decode(), variant), "data-door-code-toggle")

        assert toggle.startswith("<button")
        assert 'aria-expanded="false"' in toggle
        assert ":aria-expanded=\"open ? 'true' : 'false'\"" in toggle
        assert f'aria-controls="door-code-value-{variant}"' in toggle
        assert f'id="door-code-value-{variant}"' in toggle
        assert "x-text=\"open ? 'Tap to hide' : 'Tap to reveal'\">Tap to reveal</span>" in toggle

    def it_gives_each_box_its_own_value_id(client: Client):
        _set_code()
        _login(client, "doorbox")

        content = client.get(HOME).content.decode()

        assert content.count('id="door-code-value-header"') == 1
        assert content.count('id="door-code-value-card"') == 1

    def it_puts_the_header_box_in_the_page_header_and_show_me_around_under_the_description(client: Client):
        _set_code()
        _login(client, "doorbox")

        header = _element(client.get(HOME).content.decode(), 'class="hub-page-header"')

        actions = _element(header, 'class="pl-home-header-actions"')
        assert 'data-door-code-box="header"' in actions
        assert "Show Me Around" not in actions
        tour_row = _element(header, 'class="pl-home-tour-link"')
        assert header.index("Your home base") < header.index(tour_row)
        assert ">Show Me Around</a>" in tour_row
        assert 'data-door-code-box="card"' not in header

    def it_keeps_show_me_around_in_the_header_without_a_door_code(client: Client):
        _login(client, "doorbox")

        header = _element(client.get(HOME).content.decode(), 'class="hub-page-header"')

        assert '<a href="?tour=member-welcome" class="hub-btn hub-btn--sm">Show Me Around</a>' in header
        assert "data-door-code" not in header

    def it_puts_the_card_under_the_quick_links_and_none_inside_them(client: Client):
        _set_code()
        _login(client, "doorbox")

        content = client.get(HOME).content.decode()

        quicklinks = _element(content, 'class="pl-quicklinks"')
        assert "door-code" not in quicklinks
        assert content.index('data-door-code-box="card"') > content.index(quicklinks) + len(quicklinks)
        assert 'class="pl-door-code pl-door-code--card hub-card pl-home-block"' in _box(content, "card")

    @pytest.mark.parametrize("variant", ["header", "card"])
    def it_puts_the_admins_change_and_name_in_each(client: Client, variant: str):
        _set_code(by=_setter())
        _admin(client)

        box = _box(client.get(HOME).content.decode(), variant)

        assert "data-door-code-edit>Change</button>" in box
        assert "@click=\"$dispatch('open-modal', 'door-code-change')\"" in box
        assert "by Quillon Setter</p>" in box

    @pytest.mark.parametrize("variant", ["header", "card"])
    def it_shows_an_admin_no_code_set_yet_with_set_code_in_each(client: Client, variant: str):
        _admin(client)

        box = _box(client.get(HOME).content.decode(), variant)

        assert "data-door-code-empty>No code set yet.</p>" in box
        assert "data-door-code-edit>Set Code</button>" in box
        assert "data-door-code-toggle" not in box

    def it_includes_the_change_modal_exactly_once(client: Client):
        _set_code()
        _admin(client)

        content = client.get(HOME).content.decode()

        assert content.count('id="door-code-change-title"') == 1
        assert content.count("data-door-code-form") == 1


def describe_home_caching():
    def it_keeps_home_out_of_every_cache_for_a_member(client: Client):
        _set_code()
        _login(client, "doorcache")

        response = client.get(HOME)

        directives = {part.strip() for part in response["Cache-Control"].split(",")}
        assert {"private", "no-store"} <= directives

    def it_keeps_home_out_of_every_cache_for_an_unlinked_account(client: Client):
        user = User.objects.create_user(username="doorcacheorphan", password="pass")
        Member.objects.filter(user=user).delete()
        client.login(username="doorcacheorphan", password="pass")

        response = client.get(HOME)

        directives = {part.strip() for part in response["Cache-Control"].split(",")}
        assert {"private", "no-store"} <= directives


def describe_who_never_sees_the_code():
    def it_hides_it_from_an_unlinked_account(client: Client):
        _set_code()
        user = User.objects.create_user(username="doororphan", password="pass")
        Member.objects.filter(user=user).delete()
        client.login(username="doororphan", password="pass")

        response = client.get(HOME)

        assert response.status_code == 200
        assert FAKE_CODE.encode() not in response.content
        assert BOX not in response.content

    @pytest.mark.parametrize(
        "status",
        [Member.Status.GUEST, Member.Status.FORMER, Member.Status.SUSPENDED, Member.Status.INVITED],
    )
    def it_hides_it_from_a_member_who_is_not_active(client: Client, status: str):
        _set_code()
        _login(client, f"door{status}", status=status)

        response = client.get(HOME, follow=True)

        assert FAKE_CODE.encode() not in response.content
        assert BOX not in response.content

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
        assert BOX not in response.content


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

    def it_keeps_a_pasted_leading_space_from_cutting_a_twenty_character_code(client: Client):
        _admin(client)

        client.post(SAVE, {"door_code": " " + "A1#" * 6 + "Z9"})

        assert SiteConfiguration.load().door_code == "A1#A1#A1#A1#A1#A1#Z9"

    def it_puts_no_html_maxlength_on_the_input(client: Client):
        _admin(client)

        content = client.get(HOME).content.decode()

        start = content.index('name="door_code"')
        tag = content[content.rindex("<input", 0, start) : content.index(">", start)]
        assert "maxlength" not in tag

    def describe_saving_the_code_already_stored():
        def it_keeps_the_date_and_name_of_the_real_change(client: Client):
            setter = _setter()
            changed = datetime(2026, 10, 3, 18, 0, tzinfo=dt_timezone.utc)
            _set_code(by=setter, at=changed)
            _admin(client)

            response = client.post(SAVE, {"door_code": "  4242#  "})

            assert [str(m) for m in get_messages(response.wsgi_request)] == ["Door code saved. Members see it now."]
            config = SiteConfiguration.load()
            assert config.door_code == FAKE_CODE
            assert config.door_code_changed_at == changed
            assert config.door_code_changed_by == setter

        def it_reports_whether_the_code_changed():
            config = _set_code()
            by = _setter()

            assert config.set_door_code(FAKE_CODE, by=by, now=timezone.now()) is False
            assert config.set_door_code("1111#", by=by, now=timezone.now()) is True

    def it_accepts_twenty_characters(client: Client):
        _admin(client)

        client.post(SAVE, {"door_code": "A1#" * 6 + "Z9"})

        assert SiteConfiguration.load().door_code == "A1#A1#A1#A1#A1#A1#Z9"

    def it_saves_only_the_door_code_columns(client: Client):
        config = SiteConfiguration.load()
        config.org_name = "Zorblax line"
        config.save()
        _admin(client)
        stale = SiteConfiguration.load()
        SiteConfiguration.objects.filter(pk=1).update(org_name="Another admin edit")

        stale.set_door_code(FAKE_CODE, by=User.objects.get(username="dooradmin"), now=timezone.now())

        assert SiteConfiguration.load().org_name == "Another admin edit"

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


def describe_door_code_view_read_model():
    def it_leaves_the_code_out_of_its_repr():
        # Sentry ships frame locals with send_default_pii on; the repr must not carry the code.
        view = DoorCodeView(code=FAKE_CODE, changed_at=None, can_change=True, changed_by="Zorblax Admin")

        assert FAKE_CODE not in repr(view)
        assert "code=" not in repr(view).replace("changed_", "")

    def it_is_shown_to_members_only_once_a_code_is_set():
        assert DoorCodeView(code="", changed_at=None, can_change=False).is_shown is False
        assert DoorCodeView(code=FAKE_CODE, changed_at=None, can_change=False).is_shown is True

    def it_is_always_shown_to_admins():
        assert DoorCodeView(code="", changed_at=None, can_change=True).is_shown is True
