"""BDD specs for the reusable page_header component and its rollout to member-hub pages (UAT #9)."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.template.loader import render_to_string
from django.test import Client
from django.urls import reverse

from membership.models import Member
from tests.membership.factories import MembershipPlanFactory

pytestmark = pytest.mark.django_db


def _render(**context: object) -> str:
    return render_to_string("components/page_header.html", context)


def describe_page_header_component():
    def it_renders_the_title_as_a_hub_page_title(db):
        html = _render(title="My Page")
        assert '<h1 class="hub-page-title"' in html
        assert "My Page" in html

    def it_renders_the_description_as_a_muted_lead(db):
        html = _render(title="My Page", description="What this page is for.")
        assert "hub-text-muted" in html
        assert "What this page is for." in html

    def it_omits_the_muted_lead_when_no_description(db):
        html = _render(title="My Page")
        assert "hub-text-muted" not in html

    def describe_with_an_action():
        def it_renders_a_right_aligned_action_button(db):
            html = _render(title="My Page", action_url="/export/", action_label="Export CSV")
            assert 'href="/export/"' in html
            assert "Export CSV" in html
            assert "hub-btn" in html

        def it_defaults_the_action_button_classes(db):
            html = _render(title="My Page", action_url="/x/", action_label="Go")
            assert "hub-btn hub-btn--sm" in html

        def it_honors_an_action_class_override(db):
            html = _render(title="My Page", action_url="/x/", action_label="Go", action_class="hub-btn hub-btn--ghost")
            assert "hub-btn hub-btn--ghost" in html

    def it_renders_no_action_markup_without_an_action_url(db):
        html = _render(title="My Page", description="Just a header.")
        assert "hub-btn" not in html
        assert "<a " not in html


def describe_page_header_rollout():
    def it_shows_the_purpose_blurb_on_the_orientations_dashboard(client: Client):
        MembershipPlanFactory()
        user = User.objects.create_user(username="ph_orient", password="pw")
        member = user.member
        member.fog_role = Member.FogRole.ADMIN
        member.save(update_fields=["fog_role"])
        member.sync_user_permissions()
        client.login(username="ph_orient", password="pw")
        response = client.get(reverse("hub_orientations_dashboard"))
        assert response.status_code == 200
        assert b"Track and manage orientation requests" in response.content
        assert b"for the guilds you lead" in response.content

    def it_shows_the_purpose_blurb_on_the_member_directory(client: Client):
        MembershipPlanFactory()
        User.objects.create_user(username="ph_dir", password="pw")
        client.login(username="ph_dir", password="pw")
        response = client.get(reverse("hub_member_directory"))
        assert response.status_code == 200
        assert b"you control what of yours appears here" in response.content

    def it_shows_the_purpose_blurb_on_the_community_calendar(client: Client):
        response = client.get("/calendar/")
        assert response.status_code == 200
        assert b"Everything happening at the makerspace in one place" in response.content
