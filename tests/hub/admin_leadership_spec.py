"""BDD specs for the Leadership Directory admin page at /manage/leadership/ (#476).

Three sibling forms on one page: Page Wording (posts to the page itself), the team roster
(the order, who stays listed and each person's role lines, one Save) and Add a Person (a
modal with its own form). Assertions anchor on markup, ids and factory strings, never on
copy the changelog could also carry.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from membership.models import LeadershipListing, LeadershipPage, LeadershipRole, Member
from tests.membership.factories import LeadershipListingFactory, LeadershipRoleFactory, MemberFactory

pytestmark = pytest.mark.django_db

PASSWORD = "pw12345!"
_PAGE = reverse("hub_admin_leadership")
_ADD = reverse("hub_admin_leadership_add")
_SAVE = reverse("hub_admin_leadership_roster_save")
_DIRECTORY = reverse("hub_leadership_directory")


def _login(client: Client, username: str, role: str) -> Member:
    user = User.objects.create_user(username=username, email=f"{username}@x.com", password=PASSWORD)
    member = Member.objects.get(user=user)
    member.fog_role = role
    member.save()
    client.login(username=username, password=PASSWORD)
    return member


def _admin(client: Client) -> Member:
    return _login(client, "lead-admin", Member.FogRole.ADMIN)


def _listed(name: str, sort_order: int = 0, title: str = "Founder", email: str = "") -> LeadershipListing:
    listing = LeadershipListingFactory(member=MemberFactory(full_legal_name=name), sort_order=sort_order)
    LeadershipRoleFactory(listing=listing, title=title, email=email)
    return listing


def _role_row(role: LeadershipRole, **overrides: str) -> dict[str, str]:
    """The posted fields of one saved role line, exactly as the page rendered them."""
    row = {"id": str(role.pk), "title": role.title, "email": role.email, "sort_order": str(role.sort_order)}
    row.update(overrides)
    return row


def _roster_post(
    rows: list[tuple[LeadershipListing, list[dict[str, str]]]], unlist: set[int] | None = None
) -> dict[str, str]:
    """The roster Save payload: one order row per listing in the posted order, plus each listing's role lines.

    ``rows`` is the visual order; ``sort_order`` is stamped from the index the way the reorder
    script does. A listing in ``unlist`` posts ``is_listed`` False, which is what Remove does.
    """
    unlist = unlist or set()
    data = {
        "roster-TOTAL_FORMS": str(len(rows)),
        "roster-INITIAL_FORMS": str(len(rows)),
        "roster-MIN_NUM_FORMS": "0",
        "roster-MAX_NUM_FORMS": "1000",
    }
    for index, (listing, roles) in enumerate(rows):
        data[f"roster-{index}-id"] = str(listing.pk)
        data[f"roster-{index}-sort_order"] = str(index)
        data[f"roster-{index}-is_listed"] = "False" if listing.pk in unlist else "True"
        prefix = f"roles-{listing.pk}"
        data[f"{prefix}-TOTAL_FORMS"] = str(len(roles))
        data[f"{prefix}-INITIAL_FORMS"] = str(sum(1 for row in roles if "id" in row))
        data[f"{prefix}-MIN_NUM_FORMS"] = "0"
        data[f"{prefix}-MAX_NUM_FORMS"] = "1000"
        for row_index, row in enumerate(roles):
            for key, value in row.items():
                data[f"{prefix}-{row_index}-{key}"] = value
    return data


def _wording_post(**overrides: str) -> dict[str, str]:
    data = {
        "hero_title": "Who Runs This Place",
        "hero_lead": "Every name in one place.",
        "team_heading": "The Crew",
        "team_intro": "",
        "guilds_heading": "Shop Leads",
        "guilds_intro": "One card per guild.",
    }
    data.update(overrides)
    return data


def describe_leadership_admin_gating():
    def it_redirects_anonymous_users(client: Client):
        assert client.get(_PAGE).status_code == 302
        assert client.post(_ADD, {}).status_code == 302
        assert client.post(_SAVE, {}).status_code == 302

    def it_forbids_a_plain_member(client: Client):
        _login(client, "plain", Member.FogRole.MEMBER)
        assert client.get(_PAGE).status_code == 403
        assert client.post(_ADD, {}).status_code == 403
        assert client.post(_SAVE, {}).status_code == 403


def describe_leadership_admin_page():
    def it_keeps_the_three_forms_as_siblings_never_nested(client: Client):
        _admin(client)
        _listed("Ada Aldous")
        html = client.get(_PAGE).content.decode()
        wording_at = html.index(f'action="{_PAGE}"')
        roster_at = html.index(f'action="{_SAVE}"')
        add_at = html.index(f'action="{_ADD}"')
        assert roster_at > html.index("</form>", wording_at)
        assert add_at > html.index("</form>", roster_at)

    def it_renders_each_listed_person_as_a_reorder_row_with_hidden_order_and_flag(client: Client):
        _admin(client)
        listing = _listed("Ada Aldous", title="Founder")
        LeadershipListingFactory(is_listed=False, member=MemberFactory(full_legal_name="Hidden Hank"))
        html = client.get(_PAGE).content.decode()
        # The roster form only: the add picker below it offers Hidden Hank on purpose.
        roster_at = html.index(f'action="{_SAVE}"')
        roster = html[roster_at : html.index("</form>", roster_at)]
        assert 'id="leadership-rows"' in roster
        assert "Ada Aldous" in roster
        assert "Hidden Hank" not in roster
        assert 'class="pl-slide-grip" draggable="true"' in html
        assert 'data-move="up"' in html
        assert 'data-move="down"' in html
        assert '<input type="hidden" name="roster-0-sort_order"' in html
        assert 'name="roster-0-is_listed" value="True"' in html
        assert 'value="Founder"' in html
        assert f'name="roles-{listing.pk}-TOTAL_FORMS"' in html
        assert f'id="leadership-role-empty-template-{listing.pk}"' in html
        assert f'data-add-role="{listing.pk}"' in html
        assert f'name="roles-{listing.pk}-0-DELETE"' in html
        assert 'x-data="{ expanded: false }"' in html

    def it_shows_the_photo_in_a_row_only_when_the_member_allows_it(client: Client):
        _admin(client)
        shown = _listed("Ada Aldous")
        Member.objects.filter(pk=shown.member.pk).update(profile_photo="members/profile/shown.png")
        hidden = _listed("Quiet Quill", sort_order=1)
        Member.objects.filter(pk=hidden.member.pk).update(
            profile_photo="members/profile/hidden.png", directory_visibility={"profile_photo": False}
        )
        html = client.get(_PAGE).content.decode()
        assert "members/profile/shown.png" in html
        assert "members/profile/hidden.png" not in html
        assert "QQ" in html

    def it_shows_the_empty_state_when_nobody_is_listed(client: Client):
        _admin(client)
        html = client.get(_PAGE).content.decode()
        assert 'class="pl-leadership-admin__empty"' in html
        assert "pl-roster-row" not in html

    def it_renders_the_wording_fields_inside_the_wording_form(client: Client):
        _admin(client)
        html = client.get(_PAGE).content.decode()
        start = html.index(f'action="{_PAGE}"')
        wording_form = html[start : html.index("</form>", start)]
        for field in ("hero_title", "hero_lead", "team_heading", "team_intro", "guilds_heading", "guilds_intro"):
            assert f'name="{field}"' in wording_form

    def it_offers_only_members_who_are_not_on_the_page_in_the_add_picker(client: Client):
        _admin(client)
        listed = _listed("Ada Aldous")
        removed = LeadershipListingFactory(is_listed=False, member=MemberFactory(full_legal_name="Hidden Hank"))
        never = MemberFactory(full_legal_name="Newcomer Nell")
        html = client.get(_PAGE).content.decode()
        start = html.index(f'action="{_ADD}"')
        add_form = html[start : html.index("</form>", start)]
        assert f'<option value="{listed.member.pk}"' not in add_form
        assert f'<option value="{removed.member.pk}"' in add_form
        assert f'<option value="{never.pk}"' in add_form

    def it_reopens_the_add_modal_with_the_errors_after_a_failed_add(client: Client):
        _admin(client)
        response = client.post(_ADD, {"member": "", "title": "", "email": ""})
        assert response.status_code == 200
        html = response.content.decode()
        assert "$dispatch('open-modal', 'leadership-add')" in html
        assert 'class="pl-field-error"' in html
        assert LeadershipListing.objects.count() == 0


def describe_page_wording_save():
    def it_saves_every_field_and_the_directory_shows_them(client: Client):
        _admin(client)
        response = client.post(_PAGE, _wording_post())
        assert response.status_code == 302
        assert response["Location"] == _PAGE
        page = LeadershipPage.load()
        assert page.hero_title == "Who Runs This Place"
        assert page.hero_lead == "Every name in one place."
        assert page.team_heading == "The Crew"
        assert page.team_intro == ""
        assert page.guilds_heading == "Shop Leads"
        assert page.guilds_intro == "One card per guild."
        body = client.get(_DIRECTORY).content.decode()
        assert "Who Runs This Place" in body
        assert 'class="pl-leadership__intro"' in body.split('id="leadership-guilds"')[1]
        assert 'class="pl-leadership__intro"' not in body.split('id="leadership-guilds"')[0]

    def it_re_renders_with_the_typed_values_on_an_invalid_save(client: Client):
        _admin(client)
        response = client.post(_PAGE, _wording_post(hero_title="", team_heading="The Crew Typed"))
        assert response.status_code == 200
        html = response.content.decode()
        assert 'value="The Crew Typed"' in html
        assert 'class="pl-field-error"' in html
        assert LeadershipPage.load().team_heading == "Leadership & Admin Team"


def describe_add_a_person():
    def it_lists_a_new_member_last_with_the_first_role_line(client: Client):
        _admin(client)
        _listed("Ada Aldous", sort_order=4)
        newcomer = MemberFactory(full_legal_name="Newcomer Nell")
        response = client.post(_ADD, {"member": newcomer.pk, "title": "Council Secretary", "email": "sec@x.com"})
        assert response.status_code == 302
        assert response["Location"] == _PAGE
        listing = LeadershipListing.objects.get(member=newcomer)
        assert listing.is_listed is True
        assert listing.sort_order == 5
        assert list(listing.roles.values_list("title", "email", "sort_order")) == [
            ("Council Secretary", "sec@x.com", 0)
        ]

    def it_starts_the_order_at_zero_when_nobody_is_listed(client: Client):
        _admin(client)
        LeadershipListingFactory(is_listed=False, sort_order=7)  # an unlisted row never sets the pace
        newcomer = MemberFactory(full_legal_name="Newcomer Nell")
        client.post(_ADD, {"member": newcomer.pk, "title": "Founder", "email": ""})
        assert LeadershipListing.objects.get(member=newcomer).sort_order == 0

    def it_relists_a_removed_member_last_and_keeps_the_lines_they_had(client: Client):
        _admin(client)
        _listed("Ada Aldous", sort_order=0)
        removed = LeadershipListingFactory(
            is_listed=False, sort_order=0, member=MemberFactory(full_legal_name="Back Again")
        )
        LeadershipRoleFactory(listing=removed, title="Old Title", email="old@x.com")
        response = client.post(_ADD, {"member": removed.member.pk, "title": "Old Title", "email": ""})
        assert response.status_code == 302
        removed.refresh_from_db()
        assert removed.is_listed is True
        assert removed.sort_order == 1
        # The same title again is not a second line, and the address they had stays.
        assert list(removed.roles.values_list("title", "email")) == [("Old Title", "old@x.com")]

    def it_adds_the_typed_line_when_relisting_with_a_new_title(client: Client):
        _admin(client)
        removed = LeadershipListingFactory(is_listed=False, member=MemberFactory(full_legal_name="Back Again"))
        LeadershipRoleFactory(listing=removed, title="Old Title")
        client.post(_ADD, {"member": removed.member.pk, "title": "New Title", "email": "new@x.com"})
        assert list(removed.roles.values_list("title", "email", "sort_order")) == [
            ("Old Title", "", 0),
            ("New Title", "new@x.com", 1),
        ]

    def it_refuses_a_member_who_is_already_listed(client: Client):
        _admin(client)
        listing = _listed("Ada Aldous", title="Founder")
        response = client.post(_ADD, {"member": listing.member.pk, "title": "Second Line", "email": ""})
        assert response.status_code == 200
        assert 'class="pl-field-error"' in response.content.decode()
        assert list(listing.roles.values_list("title", flat=True)) == ["Founder"]

    def it_requires_a_title(client: Client):
        _admin(client)
        newcomer = MemberFactory(full_legal_name="Newcomer Nell")
        response = client.post(_ADD, {"member": newcomer.pk, "title": "", "email": "sec@x.com"})
        assert response.status_code == 200
        assert not LeadershipListing.objects.filter(member=newcomer).exists()


def describe_roster_save():
    def it_persists_a_reorder_and_changes_no_role_line(client: Client):
        _admin(client)
        first = _listed("Ada Aldous", sort_order=0, title="Founder", email="founder@x.com")
        second = _listed("Zed Zephyr", sort_order=1, title="Liaison")
        stamp = timezone.now() - timedelta(days=3)
        LeadershipRole.objects.update(updated_at=stamp)
        first_role = first.roles.get()
        second_role = second.roles.get()
        # The drag that lifts Zed above Ada, each row carrying its lines untouched.
        payload = _roster_post([(second, [_role_row(second_role)]), (first, [_role_row(first_role)])])
        response = client.post(_SAVE, payload)
        assert response.status_code == 302
        assert response["Location"] == _PAGE
        assert list(LeadershipListing.objects.listed().values_list("member__full_legal_name", flat=True)) == [
            "Zed Zephyr",
            "Ada Aldous",
        ]
        first_role.refresh_from_db()
        second_role.refresh_from_db()
        assert (first_role.title, first_role.email, first_role.updated_at) == ("Founder", "founder@x.com", stamp)
        assert (second_role.title, second_role.email, second_role.updated_at) == ("Liaison", "", stamp)

    def it_edits_a_title_and_an_email_in_place(client: Client):
        _admin(client)
        listing = _listed("Ada Aldous", title="Founder")
        role = listing.roles.get()
        payload = _roster_post([(listing, [_role_row(role, title="Executive Director", email="ed@x.com")])])
        assert client.post(_SAVE, payload).status_code == 302
        role.refresh_from_db()
        assert (role.title, role.email) == ("Executive Director", "ed@x.com")
        body = client.get(_DIRECTORY).content.decode()
        assert "Executive Director" in body
        assert "mailto:ed@x.com" in body

    def it_adds_a_role_line_to_a_person(client: Client):
        _admin(client)
        listing = _listed("Ada Aldous", title="Founder")
        role = listing.roles.get()
        new_row = {"title": "Class Administrator", "email": "classes@x.com", "sort_order": "1"}
        payload = _roster_post([(listing, [_role_row(role), new_row])])
        assert client.post(_SAVE, payload).status_code == 302
        assert list(listing.roles.values_list("title", "email", "sort_order")) == [
            ("Founder", "", 0),
            ("Class Administrator", "classes@x.com", 1),
        ]

    def it_deletes_a_role_line(client: Client):
        _admin(client)
        listing = _listed("Ada Aldous", title="Founder")
        gone = LeadershipRoleFactory(listing=listing, title="Old Title", sort_order=1)
        keep = listing.roles.get(title="Founder")
        payload = _roster_post([(listing, [_role_row(keep), _role_row(gone, DELETE="on")])])
        assert client.post(_SAVE, payload).status_code == 302
        assert list(listing.roles.values_list("title", flat=True)) == ["Founder"]

    def it_ignores_an_abandoned_blank_added_line(client: Client):
        _admin(client)
        listing = _listed("Ada Aldous", title="Founder")
        role = listing.roles.get()
        # The add button stamped sort_order and the admin typed nothing.
        payload = _roster_post([(listing, [_role_row(role), {"title": "", "email": "", "sort_order": "1"}])])
        assert client.post(_SAVE, payload).status_code == 302
        assert listing.roles.count() == 1

    def it_removes_a_person_and_keeps_their_lines(client: Client):
        _admin(client)
        listing = _listed("Ada Aldous", title="Founder")
        role = listing.roles.get()
        assert client.post(_SAVE, _roster_post([(listing, [_role_row(role)])], unlist={listing.pk})).status_code == 302
        listing.refresh_from_db()
        assert listing.is_listed is False
        assert list(listing.roles.values_list("title", flat=True)) == ["Founder"]
        assert "Ada Aldous" not in client.get(_DIRECTORY).content.decode()

    def it_refuses_a_save_whose_rows_no_longer_match_the_page(client: Client):
        _admin(client)
        listing = _listed("Ada Aldous", title="Founder")
        role = listing.roles.get()
        payload = _roster_post([(listing, [_role_row(role, title="Renamed")])])
        # Another admin took Ada off the page after this page was loaded.
        LeadershipListing.objects.filter(pk=listing.pk).update(is_listed=False)
        response = client.post(_SAVE, payload)
        assert response.status_code == 200
        html = response.content.decode()
        assert "The team changed while you were editing" in html
        assert "pl-roster-row" not in html  # the stale row is not drawn, so nothing reads its lines
        role.refresh_from_db()
        assert role.title == "Founder"

    def it_re_renders_the_row_open_with_the_error_when_a_line_has_no_title(client: Client):
        _admin(client)
        listing = _listed("Ada Aldous", title="Founder")
        role = listing.roles.get()
        bad_row = {"title": "", "email": "typed@x.com", "sort_order": "1"}
        response = client.post(_SAVE, _roster_post([(listing, [_role_row(role, title="Renamed"), bad_row])]))
        assert response.status_code == 200
        html = response.content.decode()
        assert 'class="pl-field-error"' in html
        assert 'x-data="{ expanded: true }"' in html
        assert 'value="typed@x.com"' in html  # the admin's typing survives
        role.refresh_from_db()
        assert role.title == "Founder"  # nothing persisted
        assert listing.roles.count() == 1
