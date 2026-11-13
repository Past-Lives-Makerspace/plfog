"""The capability Get Set Up cards on their pages and the shared Guide page (#1167, part 2).

Each of the six host pages shows a holder their card and nobody else one; Reservations stacks two.
A hidden card folds to one line with its Guide and Show Get Set Up. The Guide page shows the wiki
page an admin linked to the permission, to its holder and to admins only.

Names are factory strings no changelog could contain (STANDARDS.md section 8).
"""

from __future__ import annotations

import importlib

import pytest
from django.apps import apps as django_apps
from django.contrib.messages import get_messages
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from membership import capability_setup as cs
from membership.models import AdminCapability, Member, PermissionSetup
from tests.membership.factories import CommunityEventFactory, MemberFactory
from tests.tours.conftest import signed_in

pytestmark = pytest.mark.django_db

Cap = AdminCapability.Capability
SEED = importlib.import_module("membership.migrations.0279_seed_capability_guides")

#: Each card and the page that hosts it.
HOSTS = [
    (Cap.CLASS_APPROVER, "classes:admin_classes"),
    (Cap.SPACE_APPROVER, "hub_space_request_review_queue"),
    (Cap.EVENTS_APPROVER, "hub_admin_events"),
    (Cap.EVENT_CREATOR, "hub_community_calendar"),
    (Cap.BILLING_APPROVER, "billing_admin_dashboard"),
    (Cap.EQUIPMENT, "hub_equipment_index"),
    (Cap.SPACE_MANAGER, "hub_equipment_index"),
]


def _holder(*capabilities: str, name: str = "Corvina Halloway") -> Member:
    member = MemberFactory(full_legal_name=name)
    for capability in capabilities:
        member.admin_capabilities.create(capability=capability)
    return member


def _guide(permission: str) -> str:
    return reverse("hub_permission_guide", args=[permission])


def _flashes(response) -> list[str]:
    return [str(message) for message in get_messages(response.wsgi_request)]


def describe_the_card_on_its_page():
    @pytest.mark.parametrize(("capability", "url_name"), HOSTS)
    def it_shows_the_holder_their_card(capability: str, url_name: str):
        response = signed_in(_holder(capability)).get(reverse(url_name))
        assert response.status_code == 200
        assert f'data-permission-setup="{capability}"'.encode() in response.content
        assert b"permission-setup.css" in response.content

    def it_shows_no_card_to_an_admin_without_the_grant():
        admin = MemberFactory(fog_role=Member.FogRole.ADMIN)
        for _capability, url_name in HOSTS:
            assert b"data-permission-setup" not in signed_in(admin).get(reverse(url_name)).content

    def it_stacks_both_cards_on_reservations_in_order():
        content = signed_in(_holder(Cap.SPACE_MANAGER, Cap.EQUIPMENT)).get(reverse("hub_equipment_index")).content
        assert content.index(b'data-permission-setup="equipment"') < content.index(
            b'data-permission-setup="space_manager"'
        )
        assert content.count(b'data-guidance-target="permissions.get-set-up"') == 1

    def it_opens_the_first_step_and_points_open_the_guide_at_the_guide_page():
        member = _holder(Cap.CLASS_APPROVER)
        PermissionSetup.for_member(member, Cap.CLASS_APPROVER).mark_read(cs.LEARN)
        content = signed_in(member).get(reverse("classes:admin_classes")).content.decode()
        assert 'data-setup-step="first_review" data-setup-open' in content
        assert "?status=awaiting_admin" in content
        assert _guide(Cap.CLASS_APPROVER) in content

    def it_folds_a_hidden_card_to_one_line_with_the_way_back():
        member = _holder(Cap.SPACE_APPROVER)
        PermissionSetup.for_member(member, Cap.SPACE_APPROVER).hide()
        content = signed_in(member).get(reverse("hub_space_request_review_queue")).content.decode()
        assert 'data-permission-setup-hidden="space_approver"' in content
        assert 'data-permission-setup="space_approver"' not in content
        assert reverse("hub_permission_setup_show", args=[Cap.SPACE_APPROVER]) in content
        assert _guide(Cap.SPACE_APPROVER) in content

    def it_toasts_the_badge_once_on_the_page_that_sees_setup_finish():
        member = _holder(Cap.BILLING_APPROVER)
        row = PermissionSetup.for_member(member, Cap.BILLING_APPROVER)
        row.mark_read(cs.LEARN)
        row.mark_read(cs.ALERTS)
        client = signed_in(member)
        first = client.get(reverse("billing_admin_dashboard"))
        assert cs.BILLING_ADMIN_SETUP.celebration in _flashes(first)
        assert b"data-setup-done" in first.content
        assert cs.BILLING_ADMIN_SETUP.celebration not in _flashes(client.get(reverse("billing_admin_dashboard")))

    def it_lands_got_it_back_on_the_page():
        member = _holder(Cap.EQUIPMENT)
        page = reverse("hub_equipment_index")
        response = signed_in(member).post(
            reverse("hub_permission_setup_got_it", args=[Cap.EQUIPMENT]), {"step": cs.LEARN, "next": page}
        )
        assert response.status_code == 302 and response["Location"] == page
        assert PermissionSetup.for_member(member, Cap.EQUIPMENT).read_steps == [cs.LEARN]

    def it_falls_back_to_the_cards_page_without_a_next():
        response = signed_in(_holder(Cap.EVENT_CREATOR)).post(
            reverse("hub_permission_setup_got_it", args=[Cap.EVENT_CREATOR]), {"step": cs.LEARN}
        )
        assert response["Location"] == reverse("hub_community_calendar")

    def it_reads_the_same_number_of_queries_however_much_the_holder_has_done():
        member = _holder(Cap.EVENTS_APPROVER)
        client = signed_in(member)
        CommunityEventFactory(community=True, reviewed_by=member.user)
        client.get(reverse("hub_admin_events"))  # the first visit makes the setup row
        with CaptureQueriesContext(connection) as one:
            client.get(reverse("hub_admin_events"))
        for _ in range(4):
            CommunityEventFactory(community=True, reviewed_by=member.user)
        with CaptureQueriesContext(connection) as five:
            client.get(reverse("hub_admin_events"))
        assert len(five) == len(one)


def describe_the_guide_page():
    def it_shows_the_holder_the_seeded_page_and_the_way_back():
        SEED.seed(django_apps, None)
        content = signed_in(_holder(Cap.SPACE_MANAGER)).get(_guide(Cap.SPACE_MANAGER)).content.decode()
        assert "data-guide-page=" in content
        assert "Managing Your Spaces" in content
        assert f'href="{reverse("hub_equipment_index")}"' in content
        assert "Ask an admin." in content

    def it_offers_an_admin_without_the_grant_the_edit():
        SEED.seed(django_apps, None)
        admin = MemberFactory(fog_role=Member.FogRole.ADMIN)
        content = signed_in(admin).get(_guide(Cap.EVENT_CREATOR)).content.decode()
        assert "Event Creator Guide" in content
        assert "Open to Edit on the Wiki" in content

    def it_tells_an_admin_how_to_link_one_when_none_is_picked():
        admin = MemberFactory(fog_role=Member.FogRole.ADMIN)
        content = signed_in(admin).get(_guide(Cap.EQUIPMENT)).content.decode()
        assert "data-guide-empty" in content
        assert "pick Equipment Administrator under Guide For" in content

    def it_brings_a_hidden_card_back_to_its_page():
        member = _holder(Cap.CLASS_APPROVER)
        PermissionSetup.for_member(member, Cap.CLASS_APPROVER).hide()
        client = signed_in(member)
        content = client.get(_guide(Cap.CLASS_APPROVER)).content.decode()
        assert "data-setup-restore" in content
        response = client.post(
            reverse("hub_permission_setup_show", args=[Cap.CLASS_APPROVER]),
            {"next": reverse("classes:admin_classes")},
        )
        assert response["Location"] == reverse("classes:admin_classes")
        assert PermissionSetup.for_member(member, Cap.CLASS_APPROVER).hidden_at is None

    def it_toasts_a_finish_it_is_first_to_see():
        member = _holder(Cap.EQUIPMENT)
        row = PermissionSetup.for_member(member, Cap.EQUIPMENT)
        row.mark_read(cs.LEARN)
        row.mark_read(cs.BOOKINGS)
        response = signed_in(member).get(_guide(Cap.EQUIPMENT))
        assert cs.EQUIPMENT_ADMIN_SETUP.celebration in _flashes(response)

    def it_sends_tour_guide_to_its_own_guide_tab():
        response = signed_in(_holder(Cap.TOUR_GUIDE)).get(_guide(Cap.TOUR_GUIDE))
        assert response.status_code == 302 and response["Location"] == reverse("tours:guide")

    def it_is_a_404_to_a_member_without_the_grant():
        assert signed_in(MemberFactory()).get(_guide(Cap.CLASS_APPROVER)).status_code == 404

    def it_is_a_404_for_a_permission_with_no_card():
        admin = MemberFactory(fog_role=Member.FogRole.ADMIN)
        assert signed_in(admin).get(_guide(Cap.REFUNDS)).status_code == 404

    def it_sends_a_signed_out_visitor_to_sign_in():
        from django.test import Client

        assert Client().get(_guide(Cap.EQUIPMENT)).status_code == 302
