"""BDD specs for the Leadership Directory editor at /manage/leadership/ (#476; tabs and auto save, #564).

The editor page, then one small POST endpoint per object. A typed field posts ``field`` and
``value`` and gets the saved row back as JSON; a refused value is a 422 with the field's
errors and an error toast; a tab, card or line that is gone is a 404. The two modals and
Delete tab are plain POSTs that redirect back with a message. Assertions anchor on markup,
ids, URLs and factory strings, never on copy the changelog could also carry.

The data migration makes two tabs in every migrated test database, so each spec starts from
none (``_no_tabs``) and builds exactly the tabs it needs.
"""

from __future__ import annotations

import json
import re

import pytest
from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.http import HttpResponse
from django.test import Client
from django.urls import get_resolver, reverse

from hub.forms import LeadershipEditor
from membership.models import LeadershipListing, LeadershipPage, LeadershipRole, LeadershipTab, Member
from tests.membership.factories import (
    LeadershipListingFactory,
    LeadershipRoleFactory,
    LeadershipTabFactory,
    MemberFactory,
)

pytestmark = pytest.mark.django_db

PASSWORD = "pw12345!"
_PAGE = reverse("hub_admin_leadership")
_PAGE_SAVE = reverse("hub_admin_leadership_page_save")
_TAB_ADD = reverse("hub_admin_leadership_tab_add")
_TAB_ORDER = reverse("hub_admin_leadership_tab_order")


@pytest.fixture(autouse=True)
def _no_tabs() -> None:
    LeadershipTab.objects.all().delete()


def _login(client: Client, username: str, role: str) -> Member:
    user = User.objects.create_user(username=username, email=f"{username}@x.com", password=PASSWORD)
    member = Member.objects.get(user=user)
    member.fog_role = role
    member.save()
    client.login(username=username, password=PASSWORD)
    return member


def _admin(client: Client) -> Member:
    return _login(client, "lead-admin", Member.FogRole.ADMIN)


def _guild_leads(**kwargs: object) -> LeadershipTab:
    return LeadershipTabFactory(kind=LeadershipTab.Kind.GUILD_LEADS, **kwargs)


def _person(tab: LeadershipTab, name: str, title: str = "Founder", sort_order: int = 0) -> LeadershipListing:
    listing = LeadershipListingFactory(tab=tab, member=MemberFactory(full_legal_name=name), sort_order=sort_order)
    LeadershipRoleFactory(listing=listing, title=title)
    return listing


def _toast(response: HttpResponse) -> dict[str, str]:
    return json.loads(response["HX-Trigger"])["showToast"]


def _messages(response: HttpResponse) -> list[str]:
    return [str(message) for message in get_messages(response.wsgi_request)]


def _form(html: str, action: str) -> str:
    """The form posting to ``action``, from its opening tag to its close."""
    start = html.index(f'action="{action}"')
    return html[start : html.index("</form>", start)]


def _between(html: str, start_marker: str, end_marker: str) -> str:
    start = html.index(start_marker)
    end = html.find(end_marker, start + len(start_marker))
    return html[start : end if end != -1 else len(html)]


def _pane(html: str, tab: LeadershipTab) -> str:
    return _between(html, f'id="leadership-pane-{tab.pk}"', 'id="leadership-pane-')


def _strip(html: str) -> list[int]:
    nav = _between(html, "data-tab-strip", "</nav>")
    return [int(pk) for pk in re.findall(r'data-tab-id="(\d+)"', nav)]


def describe_editor_gating():
    def _endpoints() -> dict[str, tuple[str, dict[str, object]]]:
        """Every editor endpoint by URL name, with a payload that would change something."""
        tab = LeadershipTabFactory(title="Gated Tab")
        listing = LeadershipListingFactory(tab=tab)
        role = LeadershipRoleFactory(listing=listing, title="Gated Title")
        newcomer = MemberFactory()
        return {
            "hub_admin_leadership_page_save": (_PAGE_SAVE, {"field": "hero_title", "value": "Hijacked"}),
            "hub_admin_leadership_tab_add": (_TAB_ADD, {"newtab-title": "Hijacked"}),
            "hub_admin_leadership_tab_order": (_TAB_ORDER, {"order": [tab.pk]}),
            "hub_admin_leadership_tab_save": (
                reverse("hub_admin_leadership_tab_save", args=[tab.pk]),
                {"field": "title", "value": "Hijacked"},
            ),
            "hub_admin_leadership_tab_delete": (reverse("hub_admin_leadership_tab_delete", args=[tab.pk]), {}),
            "hub_admin_leadership_person_add": (
                reverse("hub_admin_leadership_person_add", args=[tab.pk]),
                {f"add-{tab.pk}-member": newcomer.pk, f"add-{tab.pk}-title": "Hijacked"},
            ),
            "hub_admin_leadership_people_order": (
                reverse("hub_admin_leadership_people_order", args=[tab.pk]),
                {"order": [listing.pk]},
            ),
            "hub_admin_leadership_person_remove": (
                reverse("hub_admin_leadership_person_remove", args=[listing.pk]),
                {},
            ),
            "hub_admin_leadership_role_add": (
                reverse("hub_admin_leadership_role_add", args=[listing.pk]),
                {"title": "Hijacked"},
            ),
            "hub_admin_leadership_role_save": (
                reverse("hub_admin_leadership_role_save", args=[role.pk]),
                {"field": "title", "value": "Hijacked"},
            ),
            "hub_admin_leadership_role_delete": (reverse("hub_admin_leadership_role_delete", args=[role.pk]), {}),
        }

    def _untouched() -> None:
        assert LeadershipPage.load().hero_title == "Leadership Directory"
        assert list(LeadershipTab.objects.values_list("title", flat=True)) == ["Gated Tab"]
        assert list(LeadershipRole.objects.values_list("title", flat=True)) == ["Gated Title"]
        assert LeadershipListing.objects.filter(is_listed=True).count() == 1

    def it_covers_every_editor_url():
        names = {
            name
            for name in get_resolver().reverse_dict
            if isinstance(name, str) and name.startswith("hub_admin_leadership")
        }
        assert names == {"hub_admin_leadership", *_endpoints()}

    def it_sends_anonymous_users_to_login_from_every_endpoint(client: Client):
        assert client.get(_PAGE).status_code == 302
        for url, payload in _endpoints().values():
            response = client.post(url, payload)
            assert response.status_code == 302, url
            assert "login" in response["Location"]
        _untouched()

    def it_forbids_a_plain_member_on_every_endpoint(client: Client):
        endpoints = _endpoints()
        _login(client, "plain", Member.FogRole.MEMBER)
        assert client.get(_PAGE).status_code == 403
        for url, payload in endpoints.values():
            assert client.post(url, payload).status_code == 403, url
        _untouched()

    def it_forbids_a_guild_officer_too(client: Client):
        endpoints = _endpoints()
        _login(client, "officer", Member.FogRole.GUILD_OFFICER)
        for url, payload in endpoints.values():
            assert client.post(url, payload).status_code == 403, url
        _untouched()

    def it_answers_every_endpoint_to_post_only(client: Client):
        endpoints = _endpoints()
        _admin(client)
        for url, _payload in endpoints.values():
            assert client.get(url).status_code == 405, url
        assert client.post(_PAGE, {}).status_code == 405
        _untouched()


def describe_editor_page():
    def it_autosaves_the_page_wording_and_has_no_save_button(client: Client):
        _admin(client)
        html = client.get(_PAGE).content.decode()
        page = _between(html, f'data-save-url="{_PAGE_SAVE}"', "</section>")
        assert 'data-autosave="hero_title"' in page
        assert 'data-autosave="hero_lead"' in page
        # The script holds a typing-pause save of a blank required field until blur; it reads this.
        assert re.search(r'<input[^>]*name="hero_title"[^>]*required', page)
        assert not re.search(r'<textarea[^>]*name="hero_lead"[^>]*required', page)
        assert 'value="Leadership Directory"' in page
        assert ">Save</button>" not in html
        assert "data-save-pill" in html

    def it_renders_the_strip_and_a_pane_per_tab_in_order_with_the_first_open(client: Client):
        _admin(client)
        later = LeadershipTabFactory(title="Board Tab", sort_order=2)
        guilds = _guild_leads(title="Guild Leads Tab", sort_order=1)
        first = LeadershipTabFactory(title="Leadership Tab", sort_order=0)
        html = client.get(_PAGE).content.decode()
        assert _strip(html) == [first.pk, guilds.pk, later.pk]
        assert f'class="vote-tab vote-tab--active" data-tab-id="{first.pk}"' in html
        assert f'class="vote-tab" data-tab-id="{later.pk}"' in html
        assert f'x-data="plLeadershipEditor({first.pk})"' in html
        for tab in (first, guilds, later):
            pane = _pane(html, tab)
            assert f'data-save-url="{reverse("hub_admin_leadership_tab_save", args=[tab.pk])}"' in pane
            assert f'value="{tab.title}"' in pane
            assert 'data-move-tab="left"' in pane and 'data-move-tab="right"' in pane
        assert "x-cloak" not in _pane(html, first).split(">", 1)[0]
        assert "x-cloak" in _pane(html, later).split(">", 1)[0]
        assert f'data-order-url="{_TAB_ORDER}"' in html

    def it_opens_the_tab_the_query_names(client: Client):
        _admin(client)
        LeadershipTabFactory(sort_order=0)
        second = LeadershipTabFactory(sort_order=1)
        html = client.get(f"{_PAGE}?tab={second.pk}").content.decode()
        assert f'class="vote-tab vote-tab--active" data-tab-id="{second.pk}"' in html

    def it_renders_each_person_with_their_lines_and_their_own_urls(client: Client):
        _admin(client)
        tab = LeadershipTabFactory()
        listing = _person(tab, "Ada Aldous", title="Founder")
        role = listing.roles.get()
        _person(tab, "Zed Zephyr", sort_order=1)
        LeadershipListingFactory(tab=tab, is_listed=False, member=MemberFactory(full_legal_name="Hidden Hank"))
        html = client.get(_PAGE).content.decode()
        people = _between(html, "data-people", "</section>")
        assert people.index("Ada Aldous") < people.index("Zed Zephyr")
        assert "Hidden Hank" not in people
        assert f'data-order-url="{reverse("hub_admin_leadership_people_order", args=[tab.pk])}"' in html
        assert f'data-listing-id="{listing.pk}"' in people
        assert f'data-remove-url="{reverse("hub_admin_leadership_person_remove", args=[listing.pk])}"' in people
        assert f'data-add-url="{reverse("hub_admin_leadership_role_add", args=[listing.pk])}"' in people
        assert f'data-role-id="{role.pk}"' in people
        assert f'data-save-url="{reverse("hub_admin_leadership_role_save", args=[role.pk])}"' in people
        assert f'data-delete-url="{reverse("hub_admin_leadership_role_delete", args=[role.pk])}"' in people
        assert 'value="Founder"' in people
        assert 'class="pl-slide-grip" draggable="true"' in people
        assert 'data-move="up"' in people and 'data-move="down"' in people

    def it_shows_the_photo_in_a_row_only_when_the_member_allows_it(client: Client):
        _admin(client)
        tab = LeadershipTabFactory()
        shown = _person(tab, "Ada Aldous")
        Member.objects.filter(pk=shown.member.pk).update(profile_photo="members/profile/shown.png")
        hidden = _person(tab, "Quiet Quill", sort_order=1)
        Member.objects.filter(pk=hidden.member.pk).update(
            profile_photo="members/profile/hidden.png", directory_visibility={"profile_photo": False}
        )
        html = client.get(_PAGE).content.decode()
        assert "members/profile/shown.png" in html
        assert "members/profile/hidden.png" not in html
        assert "QQ" in html

    def it_gives_a_people_tab_its_own_add_form_and_delete_confirm_and_guild_leads_neither(client: Client):
        _admin(client)
        people = LeadershipTabFactory()
        guilds = _guild_leads()
        html = client.get(_PAGE).content.decode()
        assert f'action="{reverse("hub_admin_leadership_person_add", args=[people.pk])}"' in html
        assert f'action="{reverse("hub_admin_leadership_tab_delete", args=[people.pk])}"' in html
        assert f"$dispatch('open-confirm', 'leadership-delete-{people.pk}')" in _pane(html, people)
        assert reverse("hub_admin_leadership_person_add", args=[guilds.pk]) not in html
        assert reverse("hub_admin_leadership_tab_delete", args=[guilds.pk]) not in html
        assert "data-people" not in _pane(html, guilds)

    def it_offers_each_people_tab_only_the_members_not_on_it(client: Client):
        _admin(client)
        leadership = LeadershipTabFactory()
        board = LeadershipTabFactory()
        on_leadership = _person(leadership, "Ada Aldous").member
        removed = LeadershipListingFactory(tab=leadership, is_listed=False).member
        never = MemberFactory(full_legal_name="Newcomer Nell")
        html = client.get(_PAGE).content.decode()
        leadership_form = _form(html, reverse("hub_admin_leadership_person_add", args=[leadership.pk]))
        board_form = _form(html, reverse("hub_admin_leadership_person_add", args=[board.pk]))
        assert f'<option value="{on_leadership.pk}"' not in leadership_form
        assert f'<option value="{removed.pk}"' in leadership_form
        assert f'<option value="{never.pk}"' in leadership_form
        assert f'<option value="{on_leadership.pk}"' in board_form

    def it_renders_the_add_a_role_template_unsaved(client: Client):
        _admin(client)
        html = client.get(_PAGE).content.decode()
        template = _between(html, "<template data-role-template>", "</template>")
        assert 'id="id_role-__prefix__-title"' in template
        assert 'data-autosave="title"' in template and 'data-autosave="email"' in template
        assert "data-save-url" not in template and "data-role-id" not in template

    def it_renders_with_no_tabs(client: Client):
        _admin(client)
        html = client.get(_PAGE).content.decode()
        assert 'x-data="plLeadershipEditor(null)"' in html
        assert 'class="pl-leadership-admin__empty"' in html

    def describe_the_member_edit_link():
        def it_opens_add_a_person_with_the_member_on_the_first_tab_they_are_not_on(client: Client):
            _admin(client)
            leadership = LeadershipTabFactory(sort_order=0)
            board = LeadershipTabFactory(sort_order=1)
            member = _person(leadership, "Morlock Mender").member
            html = client.get(f"{_PAGE}?add={member.pk}").content.decode()
            assert f"x-init=\"$dispatch('open-modal', 'leadership-add-{board.pk}')" in html
            assert f"x-init=\"$dispatch('open-modal', 'leadership-add-{leadership.pk}')" not in html
            assert f'class="vote-tab vote-tab--active" data-tab-id="{board.pk}"' in html
            board_form = _form(html, reverse("hub_admin_leadership_person_add", args=[board.pk]))
            assert f'<option value="{member.pk}" selected>' in board_form

        def it_falls_back_to_the_first_people_tab_when_they_are_on_every_one(client: Client):
            _admin(client)
            _guild_leads(sort_order=0)
            only = LeadershipTabFactory(sort_order=1)
            member = _person(only, "Everywhere Eve").member
            html = client.get(f"{_PAGE}?add={member.pk}").content.decode()
            assert f"x-init=\"$dispatch('open-modal', 'leadership-add-{only.pk}')" in html

        def it_opens_nothing_for_a_bad_id_or_with_no_people_tab(client: Client):
            _admin(client)
            guilds = _guild_leads()
            html = client.get(f"{_PAGE}?add=nope").content.decode()
            assert "x-init=\"$dispatch('open-modal', 'leadership-add-" not in html
            html = client.get(f"{_PAGE}?add=5").content.decode()
            assert "x-init=\"$dispatch('open-modal', 'leadership-add-" not in html
            assert f'class="vote-tab vote-tab--active" data-tab-id="{guilds.pk}"' in html

        def it_ignores_a_digit_that_is_not_a_number(client: Client):
            _admin(client)
            LeadershipTabFactory()
            response = client.get(f"{_PAGE}?add=²")
            assert response.status_code == 200
            assert "x-init=\"$dispatch('open-modal', 'leadership-add-" not in response.content.decode()

    def describe_cards_written_mid_deploy():
        def it_settles_them_onto_the_first_people_tab_when_an_admin_opens_the_editor(client: Client):
            _admin(client)
            leadership = LeadershipTabFactory(sort_order=0)
            stray = LeadershipListingFactory(tab=None, member=MemberFactory(full_legal_name="Stray Stella"))
            LeadershipRoleFactory(listing=stray, title="Stray Title")
            html = client.get(_PAGE).content.decode()
            stray.refresh_from_db()
            assert stray.tab == leadership
            assert f'data-listing-id="{stray.pk}"' in _pane(html, leadership)

        def it_folds_a_stray_into_the_card_already_on_the_tab(client: Client):
            _admin(client)
            leadership = LeadershipTabFactory()
            existing = _person(leadership, "Morlock Mender", title="Guild Executor")
            stray = LeadershipListingFactory(tab=None, member=existing.member)
            LeadershipRoleFactory(listing=stray, title="Board Advisor")
            assert client.get(_PAGE).status_code == 200
            assert list(existing.member.leadership_listings.values_list("pk", flat=True)) == [existing.pk]
            assert list(existing.roles.values_list("title", flat=True)) == ["Guild Executor", "Board Advisor"]

        def it_never_runs_from_the_public_page(client: Client):
            _admin(client)
            LeadershipTabFactory()
            stray = LeadershipListingFactory(tab=None)
            assert client.get(reverse("hub_leadership_directory")).status_code == 200
            stray.refresh_from_db()
            assert stray.tab is None


def describe_LeadershipEditorPane_delete_message():
    def it_says_who_comes_off_and_whose_kept_lines_go_too_in_every_case():
        empty = LeadershipTabFactory()
        one = LeadershipTabFactory()
        three = LeadershipTabFactory()
        hidden_only = LeadershipTabFactory()
        mixed = LeadershipTabFactory()
        _person(one, "Solo Sam")
        for name in ("A One", "B Two", "C Three"):
            _person(three, name)
        LeadershipRoleFactory(listing=LeadershipListingFactory(tab=hidden_only, is_listed=False))
        _person(mixed, "Shown Shay")
        LeadershipListingFactory(tab=mixed, is_listed=False)
        LeadershipListingFactory(tab=mixed, is_listed=False)
        messages = {pane.tab.pk: pane.delete_message for pane in LeadershipEditor().panes}
        assert messages[empty.pk] == "Nobody is on this tab, so only the tab goes."
        assert messages[one.pk] == "1 person comes off it. Everyone's lines on other tabs stay."
        assert messages[three.pk] == "3 people come off it. Everyone's lines on other tabs stay."
        assert messages[hidden_only.pk] == (
            "Nobody is shown on this tab. The lines kept for 1 person taken off it earlier go too. "
            "Everyone's lines on other tabs stay."
        )
        assert messages[mixed.pk] == (
            "1 person comes off it. The lines kept for 2 people taken off it earlier go too. "
            "Everyone's lines on other tabs stay."
        )

    def it_gives_each_confirm_its_own_id():
        tab = LeadershipTabFactory()
        (pane,) = LeadershipEditor().panes
        assert pane.delete_confirm_id == f"leadership-delete-{tab.pk}"


def describe_page_save():
    def it_saves_the_title_and_answers_the_saved_row(client: Client):
        _admin(client)
        response = client.post(_PAGE_SAVE, {"field": "hero_title", "value": "  Who Runs This Place  "})
        assert response.status_code == 200
        assert response.json() == {"id": 1, "hero_title": "Who Runs This Place"}
        assert LeadershipPage.load().hero_title == "Who Runs This Place"

    def it_saves_a_blank_lead_line_and_leaves_the_title(client: Client):
        _admin(client)
        assert client.post(_PAGE_SAVE, {"field": "hero_lead", "value": ""}).status_code == 200
        page = LeadershipPage.load()
        assert (page.hero_lead, page.hero_title) == ("", "Leadership Directory")

    def it_refuses_a_blank_title_with_the_field_error_and_an_error_toast(client: Client):
        _admin(client)
        response = client.post(_PAGE_SAVE, {"field": "hero_title", "value": "  "})
        assert response.status_code == 422
        assert list(response.json()["errors"]) == ["hero_title"]
        assert _toast(response)["type"] == "error"
        assert LeadershipPage.load().hero_title == "Leadership Directory"

    def it_refuses_a_field_it_does_not_edit_and_a_post_with_no_value(client: Client):
        _admin(client)
        assert client.post(_PAGE_SAVE, {"field": "team_heading", "value": "Old Column"}).status_code == 400
        assert client.post(_PAGE_SAVE, {"field": "hero_title"}).status_code == 400
        assert client.post(_PAGE_SAVE, {"value": "x"}).status_code == 400
        assert LeadershipPage.load().team_heading == "Leadership & Admin Team"


def describe_tab_add():
    def it_adds_a_people_tab_last_and_opens_the_editor_on_it(client: Client):
        _admin(client)
        _guild_leads(sort_order=3)
        response = client.post(_TAB_ADD, {"newtab-title": "Board Tab", "newtab-intro": "Who advises us."})
        tab = LeadershipTab.objects.get(title="Board Tab")
        assert (tab.kind, tab.intro, tab.sort_order) == (LeadershipTab.Kind.PEOPLE, "Who advises us.", 4)
        assert response.status_code == 302
        assert response["Location"] == f"{_PAGE}?tab={tab.pk}"
        assert any("Board Tab" in message for message in _messages(response))

    def it_reopens_the_modal_with_the_error_for_a_blank_title(client: Client):
        _admin(client)
        response = client.post(_TAB_ADD, {"newtab-title": "", "newtab-intro": "Typed Intro Kept"})
        assert response.status_code == 200
        html = response.content.decode()
        assert "x-init=\"$dispatch('open-modal', 'leadership-tab-add')" in html
        assert 'class="pl-field-error"' in _form(html, _TAB_ADD)
        assert "Typed Intro Kept" in _form(html, _TAB_ADD)
        assert LeadershipTab.objects.count() == 0


def describe_tab_save():
    def it_saves_a_title_and_an_intro(client: Client):
        _admin(client)
        tab = LeadershipTabFactory(title="Old Title")
        url = reverse("hub_admin_leadership_tab_save", args=[tab.pk])
        response = client.post(url, {"field": "title", "value": "Council Tab"})
        assert response.json() == {"id": tab.pk, "title": "Council Tab"}
        assert client.post(url, {"field": "intro", "value": "Who votes."}).json() == {
            "id": tab.pk,
            "intro": "Who votes.",
        }
        tab.refresh_from_db()
        assert (tab.title, tab.intro) == ("Council Tab", "Who votes.")

    def it_renames_guild_leads(client: Client):
        _admin(client)
        tab = _guild_leads(title="Guild Leaders")
        url = reverse("hub_admin_leadership_tab_save", args=[tab.pk])
        assert client.post(url, {"field": "title", "value": "Guild Leads Tab"}).status_code == 200
        tab.refresh_from_db()
        assert tab.title == "Guild Leads Tab"

    def it_refuses_a_blank_title_and_keeps_the_old_one(client: Client):
        _admin(client)
        tab = LeadershipTabFactory(title="Kept Title")
        response = client.post(reverse("hub_admin_leadership_tab_save", args=[tab.pk]), {"field": "title", "value": ""})
        assert response.status_code == 422
        assert "title" in response.json()["errors"]
        assert _toast(response)["type"] == "error"
        tab.refresh_from_db()
        assert tab.title == "Kept Title"

    def it_never_changes_the_kind(client: Client):
        _admin(client)
        tab = LeadershipTabFactory()
        url = reverse("hub_admin_leadership_tab_save", args=[tab.pk])
        assert client.post(url, {"field": "kind", "value": "guild_leads"}).status_code == 400
        tab.refresh_from_db()
        assert tab.kind == LeadershipTab.Kind.PEOPLE

    def it_answers_404_for_a_tab_that_is_gone(client: Client):
        _admin(client)
        url = reverse("hub_admin_leadership_tab_save", args=[999999])
        assert client.post(url, {"field": "title", "value": "x"}).status_code == 404


def describe_tab_order():
    def it_saves_the_strip_order(client: Client):
        _admin(client)
        a, b, c = LeadershipTabFactory(), _guild_leads(), LeadershipTabFactory()
        response = client.post(_TAB_ORDER, {"order": [c.pk, a.pk, b.pk]})
        assert response.status_code == 200
        assert response.json() == {"order": [c.pk, a.pk, b.pk]}
        assert list(LeadershipTab.objects.values_list("pk", flat=True)) == [c.pk, a.pk, b.pk]

    def it_answers_409_with_a_toast_for_a_partial_doubled_or_stale_order_and_moves_nothing(client: Client):
        _admin(client)
        a = LeadershipTabFactory(sort_order=0)
        guilds = _guild_leads(sort_order=1)
        b = LeadershipTabFactory(sort_order=2)
        for stale in ([b.pk], [b.pk, a.pk, a.pk, guilds.pk], [b.pk, a.pk, guilds.pk, 999999], [b.pk, 999999, a.pk]):
            response = client.post(_TAB_ORDER, {"order": stale})
            assert response.status_code == 409, stale
            assert _toast(response)["type"] == "error"
        assert list(LeadershipTab.objects.values_list("pk", "sort_order")) == [(a.pk, 0), (guilds.pk, 1), (b.pk, 2)]

    def it_refuses_an_id_that_is_not_a_number(client: Client):
        _admin(client)
        tab = LeadershipTabFactory()
        assert client.post(_TAB_ORDER, {"order": ["1", "x"]}).status_code == 400
        assert client.post(_TAB_ORDER, {"order": [str(tab.pk), "²"]}).status_code == 400


def describe_tab_delete():
    def it_deletes_a_people_tab_and_its_cards_and_keeps_the_same_people_on_other_tabs(client: Client):
        _admin(client)
        board = LeadershipTabFactory(title="Board Tab")
        leadership = LeadershipTabFactory()
        gone = _person(board, "Morlock Mender", title="Board Advisor")
        kept = LeadershipListingFactory(tab=leadership, member=gone.member)
        LeadershipRoleFactory(listing=kept, title="Guild Executor")
        response = client.post(reverse("hub_admin_leadership_tab_delete", args=[board.pk]))
        assert response.status_code == 302
        assert response["Location"] == _PAGE
        assert any("Board Tab" in message for message in _messages(response))
        assert not LeadershipTab.objects.filter(pk=board.pk).exists()
        assert list(LeadershipListing.objects.values_list("pk", flat=True)) == [kept.pk]
        assert list(LeadershipRole.objects.values_list("title", flat=True)) == ["Guild Executor"]

    def it_never_deletes_guild_leads(client: Client):
        _admin(client)
        tab = _guild_leads()
        assert client.post(reverse("hub_admin_leadership_tab_delete", args=[tab.pk])).status_code == 404
        assert LeadershipTab.objects.filter(pk=tab.pk).exists()

    def it_answers_404_for_a_tab_that_is_gone(client: Client):
        _admin(client)
        assert client.post(reverse("hub_admin_leadership_tab_delete", args=[999999])).status_code == 404


def describe_person_add():
    def _post(client: Client, tab: LeadershipTab, member: Member | str, title: str, email: str = "") -> HttpResponse:
        member_pk = member.pk if isinstance(member, Member) else member
        return client.post(
            reverse("hub_admin_leadership_person_add", args=[tab.pk]),
            {f"add-{tab.pk}-member": member_pk, f"add-{tab.pk}-title": title, f"add-{tab.pk}-email": email},
        )

    def it_lists_a_new_member_last_on_the_tab_with_the_first_line(client: Client):
        _admin(client)
        tab = LeadershipTabFactory(title="Board Tab")
        _person(tab, "Ada Aldous", sort_order=4)
        newcomer = MemberFactory(full_legal_name="Newcomer Nell")
        response = _post(client, tab, newcomer, "Council Secretary", "sec@x.com")
        assert response.status_code == 302
        assert response["Location"] == f"{_PAGE}?tab={tab.pk}"
        assert any("Newcomer Nell" in message and "Board Tab" in message for message in _messages(response))
        listing = LeadershipListing.objects.get(member=newcomer)
        assert (listing.tab, listing.is_listed, listing.sort_order) == (tab, True, 5)
        assert list(listing.roles.values_list("title", "email")) == [("Council Secretary", "sec@x.com")]

    def it_relists_a_member_taken_off_the_tab_with_the_lines_they_had(client: Client):
        _admin(client)
        tab = LeadershipTabFactory()
        removed = LeadershipListingFactory(tab=tab, is_listed=False)
        LeadershipRoleFactory(listing=removed, title="Old Title", email="old@x.com")
        assert _post(client, tab, removed.member, "Old Title").status_code == 302
        removed.refresh_from_db()
        assert removed.is_listed is True
        assert list(removed.roles.values_list("title", "email")) == [("Old Title", "old@x.com")]

    def it_adds_someone_already_on_another_tab_with_separate_lines(client: Client):
        _admin(client)
        leadership, board = LeadershipTabFactory(), LeadershipTabFactory()
        morlock = _person(leadership, "Morlock Mender", title="Guild Executor").member
        assert _post(client, board, morlock, "Board Advisor").status_code == 302
        lines = {
            row.tab_id: list(row.roles.values_list("title", flat=True)) for row in morlock.leadership_listings.all()
        }
        assert lines == {leadership.pk: ["Guild Executor"], board.pk: ["Board Advisor"]}

    def it_refuses_someone_already_on_the_tab_and_reopens_its_modal(client: Client):
        _admin(client)
        tab = LeadershipTabFactory(sort_order=1)
        LeadershipTabFactory(sort_order=0)
        listing = _person(tab, "Ada Aldous", title="Founder")
        response = _post(client, tab, listing.member, "Second Line")
        assert response.status_code == 200
        html = response.content.decode()
        assert f"x-init=\"$dispatch('open-modal', 'leadership-add-{tab.pk}')" in html
        assert f'class="vote-tab vote-tab--active" data-tab-id="{tab.pk}"' in html
        assert 'class="pl-field-error"' in _form(html, reverse("hub_admin_leadership_person_add", args=[tab.pk]))
        assert list(listing.roles.values_list("title", flat=True)) == ["Founder"]

    def it_requires_a_title(client: Client):
        _admin(client)
        tab = LeadershipTabFactory()
        newcomer = MemberFactory()
        assert _post(client, tab, newcomer, "").status_code == 200
        assert not LeadershipListing.objects.filter(member=newcomer).exists()

    def it_answers_404_on_guild_leads_or_a_tab_that_is_gone(client: Client):
        _admin(client)
        guilds = _guild_leads()
        newcomer = MemberFactory()
        assert _post(client, guilds, newcomer, "Lead").status_code == 404
        response = client.post(reverse("hub_admin_leadership_person_add", args=[999999]), {})
        assert response.status_code == 404
        assert LeadershipListing.objects.count() == 0


def describe_people_order():
    def it_saves_the_order_of_the_cards(client: Client):
        _admin(client)
        tab = LeadershipTabFactory()
        first = _person(tab, "Ada Aldous", sort_order=0)
        second = _person(tab, "Zed Zephyr", sort_order=1)
        url = reverse("hub_admin_leadership_people_order", args=[tab.pk])
        response = client.post(url, {"order": [second.pk, first.pk]})
        assert response.status_code == 200
        assert list(tab.listings.listed().values_list("member__full_legal_name", flat=True)) == [
            "Zed Zephyr",
            "Ada Aldous",
        ]

    def it_answers_409_with_a_toast_for_a_partial_doubled_or_stale_order_and_moves_nothing(client: Client):
        _admin(client)
        tab = LeadershipTabFactory()
        first = _person(tab, "Ada Aldous", sort_order=0)
        second = _person(tab, "Zed Zephyr", sort_order=1)
        removed = LeadershipListingFactory(tab=tab, is_listed=False, sort_order=2)
        elsewhere = LeadershipListingFactory()
        url = reverse("hub_admin_leadership_people_order", args=[tab.pk])
        for stale in (
            [second.pk],  # partial
            [second.pk, first.pk, first.pk],  # doubled
            [second.pk, first.pk, elsewhere.pk],  # a card on another tab
            [second.pk, first.pk, removed.pk],  # a card another window took off
            [second.pk, 999999],  # gone in place of one that is there
        ):
            response = client.post(url, {"order": stale})
            assert response.status_code == 409, stale
            assert _toast(response)["type"] == "error"
        rows = dict(LeadershipListing.objects.filter(tab=tab).values_list("pk", "sort_order"))
        assert rows == {first.pk: 0, second.pk: 1, removed.pk: 2}

    def it_answers_404_for_guild_leads_or_a_tab_that_is_gone_and_400_for_a_bad_id(client: Client):
        _admin(client)
        guilds = _guild_leads()
        assert client.post(reverse("hub_admin_leadership_people_order", args=[guilds.pk]), {}).status_code == 404
        assert client.post(reverse("hub_admin_leadership_people_order", args=[999999]), {}).status_code == 404
        tab = LeadershipTabFactory()
        listing = _person(tab, "Ada Aldous")
        url = reverse("hub_admin_leadership_people_order", args=[tab.pk])
        assert client.post(url, {"order": ["-1"]}).status_code == 400
        assert client.post(url, {"order": [str(listing.pk), "²"]}).status_code == 400


def describe_person_remove():
    def it_hides_the_card_at_once_and_keeps_the_lines(client: Client):
        _admin(client)
        tab = LeadershipTabFactory()
        listing = _person(tab, "Ada Aldous", title="Founder")
        response = client.post(reverse("hub_admin_leadership_person_remove", args=[listing.pk]))
        assert response.status_code == 204
        listing.refresh_from_db()
        assert listing.is_listed is False
        assert list(listing.roles.values_list("title", flat=True)) == ["Founder"]
        assert "Ada Aldous" not in client.get(reverse("hub_leadership_directory")).content.decode()

    def it_answers_404_for_a_card_that_is_gone(client: Client):
        _admin(client)
        assert client.post(reverse("hub_admin_leadership_person_remove", args=[999999])).status_code == 404


def describe_role_add():
    def it_adds_the_line_last_and_answers_its_id_and_urls(client: Client):
        _admin(client)
        listing = _person(LeadershipTabFactory(), "Ada Aldous", title="Founder")
        response = client.post(reverse("hub_admin_leadership_role_add", args=[listing.pk]), {"title": "Advisor"})
        assert response.status_code == 200
        role = listing.roles.get(title="Advisor")
        assert role.sort_order == 1
        assert response.json() == {
            "id": role.pk,
            "title": "Advisor",
            "email": "",
            "save_url": reverse("hub_admin_leadership_role_save", args=[role.pk]),
            "delete_url": reverse("hub_admin_leadership_role_delete", args=[role.pk]),
        }

    def it_never_adds_a_line_twice_once_the_first_save_has_answered(client: Client):
        """The editor's sequence for a new line: create with the title, then update the email at the URL it got."""
        _admin(client)
        listing = _person(LeadershipTabFactory(), "Ada Aldous")
        created = client.post(reverse("hub_admin_leadership_role_add", args=[listing.pk]), {"title": "Advisor"}).json()
        client.post(created["save_url"], {"field": "email", "value": "advisor@x.com"})
        client.post(created["save_url"], {"field": "title", "value": "Senior Advisor"})
        assert list(listing.roles.order_by("sort_order").values_list("title", "email")) == [
            ("Founder", ""),
            ("Senior Advisor", "advisor@x.com"),
        ]

    def it_refuses_a_blank_title_and_a_bad_email(client: Client):
        _admin(client)
        listing = _person(LeadershipTabFactory(), "Ada Aldous")
        url = reverse("hub_admin_leadership_role_add", args=[listing.pk])
        blank = client.post(url, {"title": ""})
        assert blank.status_code == 422
        assert list(blank.json()["errors"]) == ["title"]
        assert _toast(blank)["type"] == "error"
        bad = client.post(url, {"title": "Advisor", "email": "not-an-email"})
        assert bad.status_code == 422
        assert list(bad.json()["errors"]) == ["email"]
        assert listing.roles.count() == 1

    def it_answers_404_for_a_card_that_is_gone(client: Client):
        _admin(client)
        assert client.post(reverse("hub_admin_leadership_role_add", args=[999999]), {"title": "x"}).status_code == 404

    def it_answers_404_for_a_card_another_window_took_off_its_tab(client: Client):
        _admin(client)
        listing = _person(LeadershipTabFactory(), "Ada Aldous")
        listing.remove_from_tab()
        response = client.post(reverse("hub_admin_leadership_role_add", args=[listing.pk]), {"title": "Late Line"})
        assert response.status_code == 404
        assert not listing.roles.filter(title="Late Line").exists()


def describe_role_save():
    def it_saves_a_title_and_an_email(client: Client):
        _admin(client)
        role = LeadershipRoleFactory(title="Founder")
        url = reverse("hub_admin_leadership_role_save", args=[role.pk])
        assert client.post(url, {"field": "title", "value": "Executive Director"}).json()["title"] == (
            "Executive Director"
        )
        assert client.post(url, {"field": "email", "value": "ed@x.com"}).json()["email"] == "ed@x.com"
        role.refresh_from_db()
        assert (role.title, role.email) == ("Executive Director", "ed@x.com")

    def it_refuses_a_bad_email_and_keeps_the_old_one(client: Client):
        _admin(client)
        role = LeadershipRoleFactory(email="kept@x.com")
        response = client.post(
            reverse("hub_admin_leadership_role_save", args=[role.pk]), {"field": "email", "value": "nope"}
        )
        assert response.status_code == 422
        assert list(response.json()["errors"]) == ["email"]
        assert _toast(response)["type"] == "error"
        role.refresh_from_db()
        assert role.email == "kept@x.com"

    def it_refuses_a_field_it_does_not_edit(client: Client):
        _admin(client)
        role = LeadershipRoleFactory(sort_order=3)
        url = reverse("hub_admin_leadership_role_save", args=[role.pk])
        assert client.post(url, {"field": "sort_order", "value": "0"}).status_code == 400
        role.refresh_from_db()
        assert role.sort_order == 3

    def it_answers_404_for_a_line_that_is_gone(client: Client):
        _admin(client)
        url = reverse("hub_admin_leadership_role_save", args=[999999])
        assert client.post(url, {"field": "title", "value": "x"}).status_code == 404

    def it_answers_404_for_a_line_on_a_card_another_window_took_off_its_tab(client: Client):
        _admin(client)
        role = LeadershipRoleFactory(title="Kept Line")
        role.listing.remove_from_tab()
        url = reverse("hub_admin_leadership_role_save", args=[role.pk])
        assert client.post(url, {"field": "title", "value": "Late Edit"}).status_code == 404
        role.refresh_from_db()
        assert role.title == "Kept Line"


def describe_role_delete():
    def it_removes_the_line_at_once(client: Client):
        _admin(client)
        role = LeadershipRoleFactory()
        assert client.post(reverse("hub_admin_leadership_role_delete", args=[role.pk])).status_code == 204
        assert not LeadershipRole.objects.filter(pk=role.pk).exists()

    def it_answers_404_for_a_line_that_is_gone(client: Client):
        _admin(client)
        assert client.post(reverse("hub_admin_leadership_role_delete", args=[999999])).status_code == 404

    def it_answers_404_and_keeps_a_line_on_a_card_another_window_took_off_its_tab(client: Client):
        _admin(client)
        role = LeadershipRoleFactory(title="Kept Line")
        role.listing.remove_from_tab()
        assert client.post(reverse("hub_admin_leadership_role_delete", args=[role.pk])).status_code == 404
        assert LeadershipRole.objects.filter(pk=role.pk).exists()
