"""BDD specs: the wiki home separates written pages from stubs nobody has written yet.

Recently Updated lists only pages a person has edited; the stubs the seeders make go to
Pages Waiting For You, an invitation that opens each one in the editor.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from membership.models import Member, WikiPage
from tests.features import turn_on
from tests.membership.factories import GuildFactory, MembershipPlanFactory, WikiPageFactory


@pytest.fixture(autouse=True)
def _wiki_on(db):
    turn_on("wiki")


def _login(
    client: Client, username: str, *, status: str = Member.Status.ACTIVE, fog_role: str = Member.FogRole.MEMBER
) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    member = user.member
    member.status = status
    member.fog_role = fog_role
    member.save()
    member.sync_user_permissions()
    client.login(username=username, password="pass")
    return user


def _home(client: Client, **params: str):
    return client.get(reverse("hub_wiki_home"), params)


def describe_WikiPageQuerySet_written_and_unwritten():
    def it_splits_pages_on_whether_a_person_has_edited_them(db):
        written = WikiPageFactory(title="Written")
        stub = WikiPageFactory(title="Stub", seeded=True)
        assert list(WikiPage.objects.written()) == [written]
        assert list(WikiPage.objects.unwritten()) == [stub]


def describe_the_wiki_home():
    def it_keeps_stubs_out_of_recently_updated(client: Client):
        _login(client, "waiting_recent")
        written = WikiPageFactory(title="Finishing Walnut")
        WikiPageFactory(title="Dust Collection", seeded=True)
        assert _home(client).context["recent_pages"] == [written]

    def it_lists_stubs_waiting_for_a_writer_with_a_link_into_the_editor(client: Client):
        _login(client, "waiting_card")
        stub = WikiPageFactory(title="Dust Collection", seeded=True)
        response = _home(client)
        assert response.context["waiting_pages"] == [stub]
        assert b'pl-wp-section__title">Pages Waiting For You' in response.content
        assert reverse("hub_wiki_edit", args=[stub.slug]).encode() in response.content

    def it_lists_space_wide_stubs_before_guild_ones_then_by_title(client: Client):
        _login(client, "waiting_order")
        wood = WikiPageFactory(title="A Guild Page", guild=GuildFactory(name="Woodworking"), seeded=True)
        later = WikiPageFactory(title="Where Things Are", seeded=True)
        first = WikiPageFactory(title="Dust Collection", seeded=True)
        assert _home(client).context["waiting_pages"] == [first, later, wood]

    def it_caps_the_list_at_six(client: Client):
        _login(client, "waiting_cap")
        for index in range(8):
            WikiPageFactory(title=f"Stub {index}", seeded=True)
        assert len(_home(client).context["waiting_pages"]) == 6

    def it_leaves_out_official_stubs_because_a_member_cannot_edit_them(client: Client):
        _login(client, "waiting_official")
        WikiPageFactory(title="House Rules", seeded=True, official=True)
        assert _home(client).context["waiting_pages"] == []

    def it_leaves_out_an_unpublished_stub(client: Client):
        _login(client, "waiting_unpublished")
        WikiPageFactory(title="Held For A Read", seeded=True, is_published=False)
        assert _home(client).context["waiting_pages"] == []

    def it_leaves_out_an_archived_stub_even_for_staff_who_can_see_it(client: Client):
        _login(client, "waiting_archived", fog_role=Member.FogRole.ADMIN)
        WikiPageFactory(title="Unwanted Starter", seeded=True, archived=True)
        assert _home(client).context["waiting_pages"] == []

    def it_leaves_out_machine_stubs_which_the_machines_card_lists(client: Client):
        _login(client, "waiting_machine")
        WikiPageFactory(title="Table Saw", kind=WikiPage.Kind.MACHINE, seeded=True)
        assert _home(client).context["waiting_pages"] == []

    def it_follows_the_filters(client: Client):
        _login(client, "waiting_filtered")
        WikiPageFactory(title="A Material", kind=WikiPage.Kind.MATERIAL, seeded=True)
        assert _home(client, kind="howto").context["waiting_pages"] == []

    def it_offers_no_invitation_to_someone_who_cannot_write(client: Client):
        _login(client, "waiting_invited", status=Member.Status.INVITED)
        WikiPageFactory(title="Dust Collection", seeded=True)
        response = _home(client)
        assert response.status_code == 200
        assert response.context["waiting_pages"] == []

    def it_does_not_show_the_empty_filter_state_when_only_stubs_match(client: Client):
        _login(client, "waiting_only")
        WikiPageFactory(title="A Material", kind=WikiPage.Kind.MATERIAL, seeded=True)
        assert b"Nothing matches those filters." not in _home(client, kind="material").content

    def it_does_not_add_a_query_per_waiting_page(client: Client):
        _login(client, "waiting_queries")
        WikiPageFactory(title="Stub 0", guild=GuildFactory(name="Guild 0"), seeded=True)
        _home(client)
        with CaptureQueriesContext(connection) as one:
            _home(client)
        for index in range(1, 6):
            WikiPageFactory(title=f"Stub {index}", guild=GuildFactory(name=f"Guild {index}"), seeded=True)
        with CaptureQueriesContext(connection) as six:
            response = _home(client)
        assert len(response.context["waiting_pages"]) == 6
        assert len(six) == len(one)
