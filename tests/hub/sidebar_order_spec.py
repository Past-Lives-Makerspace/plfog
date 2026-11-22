"""The sidebar's main group and every Tools page section render alphabetically, for every role (#1286).

Each spec reads the order the page renders and checks it is sorted, so an entry added out of
place fails here without anyone editing an expected list. A couple of specs also pin the full
list for one viewer, as the record of what Felix approved.
"""

from __future__ import annotations

import re
from datetime import timedelta
from html.parser import HTMLParser
from pathlib import Path

import pytest
from django.conf import settings as django_settings
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from hub import views as hub_views
from hub.views import ToolCard
from membership.models import AdminCapability, Member
from tests.features import coming_soon, hide, turn_on
from tests.hq.factories import EventHubFactory
from tests.membership.factories import GuildFactory, MembershipPlanFactory, UserFactory

pytestmark = pytest.mark.django_db

_KB = "https://kb.example.org/"
_DIVIDER = '<hr class="hub-sidebar__divider" />'


class _Labels(HTMLParser):
    """The visible label of every top level ``hub-sidebar__link``, without its icon, New! badge or bubble."""

    _SKIP = ("pl-new", "pl-help__bubble")

    def __init__(self) -> None:
        super().__init__()
        self.labels: list[str] = []
        self._depth = 0  # depth inside the current entry, 0 when outside one
        self._skip_at = 0  # the depth a skipped child opened at, 0 when not skipping
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = (dict(attrs).get("class") or "").split()
        if self._depth == 0:
            if tag in ("a", "span") and "hub-sidebar__link" in classes:
                self._depth, self._text = 1, []
            return
        self._depth += 1
        if not self._skip_at and (tag == "svg" or any(name in classes for name in self._SKIP)):
            self._skip_at = self._depth

    def handle_endtag(self, tag: str) -> None:
        if self._depth == 0:
            return
        if self._skip_at == self._depth:
            self._skip_at = 0
        self._depth -= 1
        if self._depth == 0:
            self.labels.append(" ".join("".join(self._text).split()))

    def handle_data(self, data: str) -> None:
        if self._depth and not self._skip_at:
            self._text.append(data)


def _labels(html: str) -> list[str]:
    parser = _Labels()
    parser.feed(html)
    return parser.labels


def _sidebar_groups(body: str) -> tuple[list[str], list[str], list[str]]:
    """The sidebar's three groups as visible labels: above the first divider, the main group, the resources."""
    nav = re.search(r'<nav class="hub-sidebar__nav".*?</nav>', body, re.S)
    assert nav is not None, "the sidebar did not render"
    top, main, rest = nav.group(0).split(_DIVIDER, 2)
    resources = rest.split("hub-sidebar__section-toggle", 1)[0]  # Guilds follows, in its own order
    return _labels(top), _labels(main), _labels(resources)


def _alphabetical(labels: list[str]) -> list[str]:
    return sorted(labels, key=str.casefold)


def _member(username: str, **fields: object) -> User:
    MembershipPlanFactory()
    user = UserFactory(username=username)
    Member.objects.filter(user=user).update(status=Member.Status.ACTIVE, **fields)
    return User.objects.get(pk=user.pk)


def _admin() -> User:
    return User.objects.create_superuser(username="order_admin", email="order_admin@example.com", password="p")


def _lead() -> User:
    user = _member("order_lead")
    GuildFactory(guild_lead=user.member)  # type: ignore[attr-defined]
    return user


def _instructor() -> User:
    return _member("order_instructor", instructor_oriented_at=timezone.now() - timedelta(days=1))


def _plain() -> User:
    return _member("order_plain")


def _tour_guide() -> User:
    user = _member("order_guide")
    user.member.admin_capabilities.create(capability=AdminCapability.Capability.TOUR_GUIDE)  # type: ignore[attr-defined]
    return user


# Each viewer the issue names, with one entry only that viewer's sidebar carries, so a spec cannot
# pass on a sidebar that lost its role's entries.
_ROLES = [
    pytest.param(_admin, "Admin Tools", id="admin"),
    pytest.param(_lead, "Guild Tools", id="guild_lead"),
    pytest.param(_instructor, "Teaching", id="instructor"),
    pytest.param(_plain, "Host a Class", id="plain_member"),
    pytest.param(_tour_guide, "Tours", id="tour_guide"),
]


def _sidebar(client: Client, user: User) -> tuple[list[str], list[str], list[str]]:
    client.force_login(user)
    return _sidebar_groups(client.get(reverse("hub_member_directory")).content.decode())


def describe_the_sidebar_order():
    @pytest.fixture(autouse=True)
    def _setup(settings):
        settings.KNOWLEDGE_BASE_URL = _KB
        turn_on("wiki")  # a fresh database seeds the wiki off
        EventHubFactory()  # the Holiday Bizarre entry shows to an admin once an HQ exists

    @pytest.mark.parametrize(("make_user", "role_entry"), _ROLES)
    def it_sorts_the_main_group_by_visible_label(client: Client, make_user, role_entry: str):
        _, main, _ = _sidebar(client, make_user())
        assert role_entry in main
        assert main == _alphabetical(main)

    @pytest.mark.parametrize(("make_user", "role_entry"), _ROLES)
    def it_keeps_home_alone_on_top_and_the_resources_in_their_order(client: Client, make_user, role_entry: str):
        top, _, resources = _sidebar(client, make_user())
        assert top == ["Home"]
        assert resources == ["Help", "Knowledge Base", "Member Wiki"]

    def it_gives_an_admin_the_whole_group_in_order(client: Client):
        _, main, _ = _sidebar(client, _admin())
        assert main == [
            "Admin Tools",
            "Calendar",
            "Classes",
            "Guild Voting",
            "Holiday Bizarre",
            "Host a Class",
            "Leadership Directory",
            "Manage Bookings",
            "Member Directory",
            "My Tab",
            "Orientations",
            "Reservations",
            "Spaces",
            "Tours",
        ]

    def it_gives_a_plain_member_the_whole_group_in_order(client: Client):
        _, main, _ = _sidebar(client, _plain())
        assert main == [
            "Calendar",
            "Classes",
            "Guild Voting",
            "Host a Class",
            "Leadership Directory",
            "Member Directory",
            "My Tab",
            "Orientations",
            "Reservations",
            "Spaces",
            "Tours",
        ]

    def it_sorts_an_admin_previewing_as_a_member(client: Client):
        user = _admin()
        client.force_login(user)
        session = client.session
        session["view_as_role"] = "member"
        session.save()
        _, main, _ = _sidebar_groups(client.get(reverse("hub_member_directory")).content.decode())
        assert "Admin Tools" not in main
        assert "Guild Voting" in main
        assert main == _alphabetical(main)

    def it_points_guild_voting_at_the_results_for_an_admin_and_the_ballot_for_a_member(client: Client):
        client.force_login(_admin())
        admin_body = client.get(reverse("hub_member_directory")).content.decode()
        assert f'href="{reverse("hub_admin_voting_overview")}"' in admin_body
        client.force_login(_plain())
        member_body = client.get(reverse("hub_member_directory")).content.decode()
        assert (
            f'href="{reverse("hub_guild_voting")}" class="hub-sidebar__link " data-help-key="voting.rank-guilds"'
            in (member_body)
        )

    def it_keeps_a_coming_soon_entry_in_its_place_and_drops_a_hidden_one(client: Client):
        coming_soon("spaces")
        hide("orientations")
        _, main, _ = _sidebar(client, _plain())
        assert "Spaces" in main
        assert "Orientations" not in main
        assert main == _alphabetical(main)

    def it_lights_the_entry_that_owns_the_page(client: Client):
        client.force_login(_plain())
        body = client.get(reverse("hub_tab_history")).content.decode()
        assert f'href="{reverse("hub_tab_detail")}" class="hub-sidebar__link active"' in body

    def it_gives_a_guest_preview_no_guild_voting(client: Client):
        client.force_login(_admin())
        session = client.session
        session["view_as_role"] = "guest"
        session.save()
        _, main, _ = _sidebar_groups(client.get(reverse("hub_member_directory")).content.decode())
        assert "Guild Voting" not in main
        assert main == _alphabetical(main)


def _tool_sections(client: Client, query: str = "") -> list[tuple[str, list[str]]]:
    body = client.get(reverse("hub_admin_tools") + query).content.decode()
    found = re.findall(r'data-tools-section="(\w+)".*?<div class="pl-tools-grid">(.*?)</div>\s*</section>', body, re.S)
    return [(key, re.findall(r'pl-tool-card__title">([^<]+?)\s*<', cards)) for key, cards in found]


def _webmaster() -> User:
    user = _admin()
    user.member.admin_capabilities.create(capability=AdminCapability.Capability.WEBMASTER)  # type: ignore[attr-defined]
    return user


def _lead_who_teaches() -> User:
    user = _lead()
    Member.objects.filter(user=user).update(
        instructor_slug="order-lead-teaches", instructor_oriented_at=timezone.now() - timedelta(days=1)
    )
    return user


def _teaches() -> User:
    return _member(
        "order_teaches", instructor_slug="order-teaches", instructor_oriented_at=timezone.now() - timedelta(days=1)
    )


def describe_the_tools_page_order():
    @pytest.mark.parametrize(
        ("make_user", "section_keys"),
        [
            pytest.param(_webmaster, ["admin", "webmaster"], id="admin_with_webmaster"),
            pytest.param(_admin, ["admin"], id="admin_without_webmaster"),
            pytest.param(_lead, ["guild"], id="guild_lead"),
            pytest.param(_teaches, ["classes"], id="instructor"),
            pytest.param(_lead_who_teaches, ["guild", "classes"], id="guild_lead_who_teaches"),
        ],
    )
    def it_sorts_every_section_by_card_title(client: Client, make_user, section_keys: list[str]):
        client.force_login(make_user())
        sections = _tool_sections(client)
        assert [key for key, _ in sections] == section_keys
        for key, titles in sections:
            assert titles, key
            assert titles == _alphabetical(titles), key

    def it_sorts_whatever_order_the_table_lists_its_cards_in(client: Client, monkeypatch):
        reversed_table = tuple((key, tuple(reversed(cards))) for key, cards in hub_views.ADMIN_TOOL_SECTIONS)
        monkeypatch.setattr(hub_views, "ADMIN_TOOL_SECTIONS", reversed_table)
        client.force_login(_webmaster())
        for key, titles in _tool_sections(client):
            assert titles == _alphabetical(titles), key

    def it_names_every_card_once_with_a_branch_to_draw_it():
        cards: list[ToolCard] = [card for _, section in hub_views.ADMIN_TOOL_SECTIONS for card in section]
        assert len({card.key for card in cards}) == len(cards)
        template = Path(django_settings.BASE_DIR) / "templates/hub/partials/_tool_card.html"
        branches = set(re.findall(r'card\.key == "(\w+)"', template.read_text(encoding="utf-8")))
        assert branches == {card.key for card in cards}
