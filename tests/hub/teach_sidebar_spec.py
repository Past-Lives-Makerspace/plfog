"""BDD specs for the hub sidebar's Teaching entry, its Admin Tools card, and their active states."""

from __future__ import annotations

import pytest
from django.urls import reverse
from django.utils import timezone

from hub.context_processors import hub_sidebar
from membership.models import Member
from tests.membership.factories import MembershipPlanFactory, UserFactory


@pytest.fixture
def plain_user(db):
    MembershipPlanFactory()
    return UserFactory(username="sidebar-member@example.com")


@pytest.fixture
def admin_user(db):
    MembershipPlanFactory()
    user = UserFactory(username="sidebar-admin@example.com")
    member = Member.objects.get(user=user)
    member.fog_role = Member.FogRole.ADMIN
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()
    return user


def _unlock(user) -> Member:
    member = Member.objects.get(user=user)
    member.instructor_oriented_at = timezone.now()
    member.save(update_fields=["instructor_oriented_at"])
    return member


def _sidebar(client) -> str:
    html = client.get(reverse("hub_home")).content.decode()
    start = html.index('aria-label="Hub navigation"')
    return html[start : html.index("</nav>", start)]


def _teach_label(nav: str) -> str:
    """The Teaching entry's rendered anchor, whitespace-squeezed for substring matching."""
    squeezed = nav.replace("\n", "").replace(" ", "")
    return squeezed[squeezed.index('data-nav="teach"') :]


def describe_teach_entry():
    def it_is_present_for_a_plain_active_member_who_cannot_teach_yet(plain_user, client):
        """Teaching is recruited for now, so every active member sees the door.

        It used to be gated on ``can_create_classes``, which meant the only people who
        could find the teaching pages were the people who already had them.
        """
        client.force_login(plain_user)
        nav = _sidebar(client)
        assert 'data-nav="teach"' in nav
        assert ">Teaching" in _teach_label(nav)
        assert reverse("classes:teach_overview") in nav

    def it_is_present_for_an_admin_who_has_not_been_set_up_to_teach(admin_user, client):
        client.force_login(admin_user)
        assert 'data-nav="teach"' in _sidebar(client)

    def it_reads_teaching_and_opens_the_portal_once_unlocked(plain_user, client):
        _unlock(plain_user)
        client.force_login(plain_user)
        nav = _sidebar(client)
        assert ">Teaching" in _teach_label(nav)
        assert reverse("classes:teach_overview") in nav
        assert "Teach a Class" not in nav

    def it_is_absent_for_a_former_member_even_once_unlocked(plain_user, client):
        member = _unlock(plain_user)
        member.status = Member.Status.FORMER
        member.save(update_fields=["status"])
        client.force_login(plain_user)
        assert 'data-nav="teach"' not in _sidebar(client)


def describe_manage_classes():
    """It moved off the sidebar and onto Admin Tools, where the rest of the staff tools live."""

    def it_is_gone_from_every_sidebar(plain_user, admin_user, client):
        client.force_login(plain_user)
        assert 'data-nav="manage-classes"' not in _sidebar(client)
        client.force_login(admin_user)
        assert 'data-nav="manage-classes"' not in _sidebar(client)

    def it_is_an_admin_tools_card(admin_user, client):
        client.force_login(admin_user)
        html = client.get(reverse("hub_admin_tools")).content.decode()
        assert "Manage Classes" in html
        assert f'href="{reverse("classes:admin_overview")}"' in html

    def it_is_absent_from_admin_tools_for_a_non_admin(plain_user, client):
        """An instructor reaches Admin Tools for its own cards and must not see this one.

        Assert we actually LANDED on Admin Tools: _can_use_admin_tools admits on
        is_instructor, not on can_create_classes, so unlocking teaching alone is bounced
        home and the absence assertion would pass against the wrong page.

        Match the card's href, not the bare path: base.html carries "/classes/admin/" as a
        literal in the Class Catalog active-state check.
        """
        member = _unlock(plain_user)
        member.instructor_slug = "sidebar-instructor"
        member.save(update_fields=["instructor_slug"])
        client.force_login(plain_user)
        response = client.get(reverse("hub_admin_tools"))
        assert response.status_code == 200
        html = response.content.decode()
        assert "pl-tool-card" in html  # we are on Admin Tools, and it rendered cards
        assert f'href="{reverse("classes:admin_overview")}"' not in html

    def it_bounces_a_member_with_no_elevated_access_off_admin_tools(plain_user, client):
        client.force_login(plain_user)
        response = client.get(reverse("hub_admin_tools"))
        assert response.status_code == 302
        assert response["Location"] == reverse("hub_home")


def describe_active_states():
    def it_lights_teach_not_class_catalog_on_the_portal(plain_user, client):
        _unlock(plain_user)
        client.force_login(plain_user)
        html = client.get(reverse("classes:teach_overview")).content.decode()
        start = html.index('aria-label="Hub navigation"')
        nav = html[start : html.index("</nav>", start)]
        teach = nav[nav.index('data-nav="teach"') - 200 : nav.index('data-nav="teach"')]
        assert "active" in teach
        catalog = nav[nav.index(reverse("classes:public_list")) : nav.index("Class Catalog")]
        assert "active" not in catalog

    def it_does_not_light_class_catalog_on_the_class_admin(admin_user, client):
        # Manage Classes has no sidebar entry to light any more, but the class admin must
        # still not borrow the Class Catalog's highlight.
        client.force_login(admin_user)
        html = client.get(reverse("classes:admin_overview")).content.decode()
        start = html.index('aria-label="Hub navigation"')
        nav = html[start : html.index("</nav>", start)]
        catalog = nav[nav.index(reverse("classes:public_list")) : nav.index("Class Catalog")]
        assert "active" not in catalog

    def it_lights_teach_not_class_catalog_for_a_locked_member_on_the_marketing_page(plain_user, client):
        # A locked member has a Teaching entry now, so the marketing page lights it and
        # the catalog stays dark, exactly as the portal does for an instructor.
        client.force_login(plain_user)
        html = client.get(reverse("classes:teach_why")).content.decode()
        start = html.index('aria-label="Hub navigation"')
        nav = html[start : html.index("</nav>", start)]
        teach = nav[nav.index('data-nav="teach"') - 200 : nav.index('data-nav="teach"')]
        assert "active" in teach
        catalog = nav[nav.index(reverse("classes:public_list")) : nav.index("Class Catalog")]
        assert "active" not in catalog

    def it_leaves_class_catalog_dark_for_an_instructor_on_the_portal(plain_user, client):
        _unlock(plain_user)
        client.force_login(plain_user)
        html = client.get(reverse("classes:teach_overview")).content.decode()
        start = html.index('aria-label="Hub navigation"')
        nav = html[start : html.index("</nav>", start)]
        catalog = nav[nav.index(reverse("classes:public_list")) : nav.index("Class Catalog")]
        assert "active" not in catalog

    def it_lights_admin_tools_on_the_class_admin(admin_user, client):
        # Manage Classes is one of its cards now, so the class admin belongs to it.
        client.force_login(admin_user)
        html = client.get(reverse("classes:admin_overview")).content.decode()
        start = html.index('aria-label="Hub navigation"')
        nav = html[start : html.index("</nav>", start)]
        tools = nav[nav.index(reverse("hub_admin_tools")) - 120 : nav.index("Admin Tools")]
        assert "active" in tools

    def it_lights_class_catalog_on_the_catalog(plain_user, client):
        client.force_login(plain_user)
        html = client.get(reverse("classes:public_list")).content.decode()
        start = html.index('aria-label="Hub navigation"')
        nav = html[start : html.index("</nav>", start)]
        catalog = nav[nav.index(reverse("classes:public_list")) : nav.index("Class Catalog")]
        assert "active" in catalog


def describe_context_processor():
    def it_exposes_can_create_classes_and_the_teach_entry(plain_user, rf):
        request = rf.get("/classes/teach/")
        request.user = plain_user
        ctx = hub_sidebar(request)
        assert ctx["can_create_classes"] is False
        # The entry is present before the grant; only ``can_create_classes`` flips.
        assert ctx["teach_nav"] == {
            "label": "Teaching",
            "url": reverse("classes:teach_overview"),
            "is_active": True,
        }
        _unlock(plain_user)
        request.user = type(plain_user).objects.get(pk=plain_user.pk)
        ctx = hub_sidebar(request)
        assert ctx["can_create_classes"] is True
        assert ctx["teach_nav"] == {
            "label": "Teaching",
            "url": reverse("classes:teach_overview"),
            "is_active": True,
        }

    def it_gives_anonymous_visitors_no_teach_entry(rf):
        from django.contrib.auth.models import AnonymousUser

        request = rf.get("/")
        request.user = AnonymousUser()
        ctx = hub_sidebar(request)
        assert ctx["teach_nav"] is None
        assert ctx["can_create_classes"] is False

    def it_gives_an_inactive_member_no_teach_entry(plain_user, rf):
        member = Member.objects.get(user=plain_user)
        member.status = Member.Status.FORMER
        member.save(update_fields=["status"])
        request = rf.get("/classes/teach/")
        request.user = type(plain_user).objects.get(pk=plain_user.pk)
        assert hub_sidebar(request)["teach_nav"] is None

    def it_marks_the_entry_inactive_off_the_teaching_paths(plain_user, rf):
        request = rf.get("/classes/")
        request.user = plain_user
        assert hub_sidebar(request)["teach_nav"]["is_active"] is False
