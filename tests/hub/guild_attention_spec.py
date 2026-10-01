"""BDD specs for the guild edit page's Needs Attention section (ticket #399, ruling 10).

The guild's class-review queue used to live only on the teaching overview, which forced
guild business through the instructor portal and hid it from anyone without teaching
access. It is now the first section of the guild's own settings page, above the tab strip.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.db import connection
from django.test import Client, RequestFactory
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from classes.factories import CategoryFactory, ClassOfferingFactory
from classes.models import ClassApproval, ClassOffering
from hub.view_as import ViewAs
from hub.views import _guild_attention_context
from membership.models import GuildStaffMembership, Member
from tests.membership.factories import GuildFactory, GuildStaffMembershipFactory, MembershipPlanFactory

pytestmark = pytest.mark.django_db

TABS_MARKER = 'data-help-key="guild.edit-tabs"'
SECTION_MARKER = 'data-help-key="guild.approve-classes"'
CLEAR_MARKER = "Needs Attention · all clear"


def _user(username: str, *, fog_role: str = Member.FogRole.MEMBER) -> User:
    """A logged-in-able member. ``can_create_classes`` stays False — no teaching access."""
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="pass")
    member = user.member
    member.fog_role = fog_role
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()
    return user


def _pending_class(guild, title: str) -> ClassOffering:
    """A class submitted under ``guild`` with its guild-lead gate still undecided."""
    offering = ClassOfferingFactory(
        title=title,
        category=CategoryFactory(guild=guild),
        status=ClassOffering.Status.PENDING,
    )
    ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.GUILD_LEAD)
    ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.ADMIN)
    return offering


def _guild_approved_class(guild, title: str) -> ClassOffering:
    """A class this guild already approved, still waiting on the admin gate."""
    offering = ClassOfferingFactory(
        title=title,
        category=CategoryFactory(guild=guild),
        status=ClassOffering.Status.PENDING,
    )
    ClassApproval.objects.create(
        class_offering=offering,
        role=ClassApproval.Role.GUILD_LEAD,
        decision=ClassApproval.Decision.APPROVED,
        decided_at=timezone.now(),
    )
    ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.ADMIN)
    return offering


def _get_page(client: Client, username: str, guild, query: str = "") -> str:
    client.login(username=username, password="pass")
    resp = client.get(f"{reverse('hub_guild_edit', args=[guild.pk])}{query}")
    assert resp.status_code == 200
    return resp.content.decode()


def describe_guild_needs_attention_section():
    def describe_placement():
        def it_renders_above_the_tab_strip_without_adding_a_tab(client: Client):
            user = _user("ga_place")
            guild = GuildFactory(name="Metalsmithing", guild_lead=user.member)
            _pending_class(guild, "Ring Sizing and Soldering Basics")
            html = _get_page(client, "ga_place", guild)
            assert html.index(SECTION_MARKER) < html.index(TABS_MARKER)
            # Criterion 28 as ruling 21 supersedes it: no thirteenth tab, and the page still
            # opens on Basic Information because nothing sets active_tab.
            assert "section === 'overview'" not in html
            assert "get('tab') || 'basic'" in html

        def it_keeps_every_existing_tab_deep_link_and_the_tour(client: Client):
            user = _user("ga_deep")
            guild = GuildFactory(name="Deep Link Guild", guild_lead=user.member)
            for query in ("?tab=basic", "?tab=links", "?tab=staff", "?tour=guild-lead"):
                html = _get_page(client, "ga_deep", guild, query)
                assert TABS_MARKER in html
                assert html.index(SECTION_MARKER) < html.index(TABS_MARKER)

    def describe_what_it_lists():
        def it_lists_the_review_queue_and_the_admin_wait_for_this_guild(client: Client):
            user = _user("ga_lead")
            guild = GuildFactory(name="Metalsmithing", guild_lead=user.member)
            pending = _pending_class(guild, "Ring Sizing and Soldering Basics")
            approved = _guild_approved_class(guild, "Chasing and Repousse Weekend")
            token = pending.approvals.get(role=ClassApproval.Role.GUILD_LEAD).token
            html = _get_page(client, "ga_lead", guild)
            assert "Waiting on Your Review" in html
            assert "Ring Sizing and Soldering Basics" in html
            assert reverse("classes:class_review", kwargs={"token": token}) in html
            assert "Awaiting Admin Validation" in html
            assert "Chasing and Repousse Weekend" in html
            assert reverse("classes:teach_class_edit", kwargs={"pk": approved.pk}) in html
            assert CLEAR_MARKER not in html

        def it_leaves_out_a_class_from_another_guild(client: Client):
            user = _user("ga_scope")
            guild = GuildFactory(name="Mine", guild_lead=user.member)
            _pending_class(guild, "My Guild Class")
            _pending_class(GuildFactory(name="Theirs"), "Someone Elses Class")
            html = _get_page(client, "ga_scope", guild)
            assert "My Guild Class" in html
            assert "Someone Elses Class" not in html

        def describe_when_both_queues_are_empty():
            def it_shows_the_all_clear_strip(client: Client):
                user = _user("ga_clear")
                guild = GuildFactory(name="Quiet Guild", guild_lead=user.member)
                html = _get_page(client, "ga_clear", guild)
                assert SECTION_MARKER in html
                assert CLEAR_MARKER in html
                assert "Waiting on Your Review" not in html

    def describe_who_sees_it():
        def it_shows_the_queue_to_a_staffer_with_no_teaching_access(client: Client):
            # Criterion 30: a secretary is neither the guild_lead FK holder nor a teacher.
            user = _user("ga_staff")
            guild = GuildFactory(name="Staffed Guild")
            GuildStaffMembershipFactory(guild=guild, member=user.member, role=GuildStaffMembership.Role.SECRETARY)
            assert user.member.can_create_classes is False
            _pending_class(guild, "Forging a Chefs Knife")
            html = _get_page(client, "ga_staff", guild)
            assert "Waiting on Your Review" in html
            assert "Forging a Chefs Knife" in html

        def it_shows_an_admin_nothing_on_a_guild_they_neither_lead_nor_staff(client: Client):
            # Criterion 47: the queues are viewer-scoped, so an admin's queue on a guild they
            # do not staff is empty — telling them "all clear" would be a lie about that guild.
            _user("ga_admin", fog_role=Member.FogRole.ADMIN)
            guild = GuildFactory(name="Someone Elses Guild")
            _pending_class(guild, "Invisible To The Admin")
            html = _get_page(client, "ga_admin", guild)
            assert SECTION_MARKER not in html
            assert CLEAR_MARKER not in html
            assert "Invisible To The Admin" not in html

        def it_shows_an_admin_the_queue_on_a_guild_they_do_staff(client: Client):
            user = _user("ga_admin_lead", fog_role=Member.FogRole.ADMIN)
            guild = GuildFactory(name="Admin Led Guild", guild_lead=user.member)
            _pending_class(guild, "Visible To The Admin")
            html = _get_page(client, "ga_admin_lead", guild)
            assert SECTION_MARKER in html
            assert "Visible To The Admin" in html

    def describe_row_links():
        def it_links_the_title_and_offers_edit_when_the_viewer_can_open_the_class(client: Client):
            # The D3 population that exists: guild staff reach the class screen through
            # membership.permissions.can_edit_class, with no can_create_classes of their own.
            user = _user("ga_rowlink")
            guild = GuildFactory(name="Row Link Guild")
            GuildStaffMembershipFactory(guild=guild, member=user.member, role=GuildStaffMembership.Role.SECRETARY)
            offering = _pending_class(guild, "Row Link Class")
            html = _get_page(client, "ga_rowlink", guild)
            edit_url = reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})
            assert f'<a class="pl-attention-row__main" href="{edit_url}">Row Link Class</a>' in html
            assert f'href="{edit_url}">Edit</a>' in html

        def it_takes_the_flag_from_can_edit_class_not_from_queue_membership(rf: RequestFactory):
            # The title, Edit and View all point at the class screen, so a viewer who fails
            # its gate gets three dead links unless the row drops them. The flag therefore
            # tracks can_edit_class — the leg classes.access.class_access composes for guild
            # lead-or-staff — and never can_create_classes. A request with no view_as fails
            # _editing_member, which is the cheapest way to model a viewer the gate refuses.
            user = _user("ga_flag")
            guild = GuildFactory(name="Flag Guild", guild_lead=user.member)
            _pending_class(guild, "Flag Class")
            request = rf.get(reverse("hub_guild_edit", args=[guild.pk]))
            request.user = user
            ctx = _guild_attention_context(request, guild)
            assert ctx["guild_attention_visible"] is True
            assert [row["can_open"] for row in ctx["guild_attention_review"]] == [False]

    def describe_query_budget():
        def it_costs_the_same_whatever_the_queue_holds(rf: RequestFactory):
            # The guild edit page is already heavy, so the section must not add a query per
            # row. The gate behind can_open is the trap: resolved per row it re-runs
            # Guild.is_staffed_by once per row for a staffer, which is exactly a staffer's
            # own queue. Measured on the staffed path for that reason.
            def cost(username: str, rows: int) -> int:
                user = _user(username)
                guild = GuildFactory(name=f"Budget {username}")
                GuildStaffMembershipFactory(guild=guild, member=user.member, role=GuildStaffMembership.Role.SECRETARY)
                for index in range(rows):
                    _pending_class(guild, f"{username} class {index}")
                request = rf.get(reverse("hub_guild_edit", args=[guild.pk]))
                request.user = user
                request.view_as = ViewAs.for_request(request)
                with CaptureQueriesContext(connection) as captured:
                    context = _guild_attention_context(request, guild)
                assert len(context["guild_attention_review"]) == rows
                return len(captured)

            assert cost("ga_budget_one", 1) == cost("ga_budget_many", 4)

    def describe_it_grants_nothing():
        def it_adds_no_control_that_submits_the_guild_settings_form(client: Client):
            # Criterion 46. The section sits above the page's single <form>, and every
            # control in it is an anchor, so no arrangement of the page can post it.
            user = _user("ga_nosubmit")
            guild = GuildFactory(name="No Submit Guild", guild_lead=user.member)
            _pending_class(guild, "No Submit Class")
            _guild_approved_class(guild, "No Submit Approved")
            html = _get_page(client, "ga_nosubmit", guild)
            section = html[html.index(SECTION_MARKER) : html.index(TABS_MARKER)]
            assert "<button" not in section
            assert "<form" not in section
            assert 'type="submit"' not in section

        def it_decides_no_approval_on_a_page_view(client: Client):
            # Criterion 32: rendering the queue is a read. Approving still happens only on
            # the tokenized review page, which this ticket does not touch.
            user = _user("ga_readonly")
            guild = GuildFactory(name="Read Only Guild", guild_lead=user.member)
            offering = _pending_class(guild, "Read Only Class")
            _get_page(client, "ga_readonly", guild)
            assert offering.approvals.filter(decision="").count() == 2
            offering.refresh_from_db()
            assert offering.status == ClassOffering.Status.PENDING
