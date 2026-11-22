"""BDD specs for the Payments and Reports tiles in the Tools page's Your Guild and Classes sections (#1294)."""

from __future__ import annotations

import re

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from classes.factories import ClassOfferingFactory
from membership.models import AdminCapability, Member
from tests.membership.factories import GuildFactory

pytestmark = pytest.mark.django_db

_SCOPED_PAYMENTS = f'href="{reverse("billing_admin_dashboard")}?tab=payments" data-tool-scoped-payments'
_SCOPED_REPORTS = f'href="{reverse("billing_admin_reports")}" data-tool-scoped-reports'


def _login(client: Client, username: str) -> Member:
    user = User.objects.create_user(username=username, password="pass", email=f"{username}@example.com")
    client.login(username=username, password="pass")
    return user.member  # type: ignore[attr-defined]


def _teach(member: Member) -> None:
    Member.objects.filter(pk=member.pk).update(
        instructor_slug=f"slug{member.pk}", instructor_oriented_at=timezone.now(), status=Member.Status.ACTIVE
    )
    ClassOfferingFactory(instructor=member)


def _section(client: Client, key: str) -> str:
    html = client.get(reverse("hub_admin_tools")).content.decode()
    match = re.search(rf'data-tools-section="{key}".*?</section>', html, re.S)
    return match.group(0) if match else ""


def describe_the_guild_section():
    def it_gives_a_lead_payments_and_reports_that_open_the_scoped_pages(client: Client):
        member = _login(client, "tile_lead")
        GuildFactory(guild_lead=member)
        section = _section(client, "guild")
        assert _SCOPED_PAYMENTS in section
        assert _SCOPED_REPORTS in section
        assert client.get(reverse("billing_admin_dashboard") + "?tab=payments").status_code == 200
        assert client.get(reverse("billing_admin_reports"), follow=True).status_code == 200


def describe_the_classes_section():
    def it_gives_an_instructor_payments_and_reports(client: Client):
        _teach(_login(client, "tile_teacher"))
        section = _section(client, "classes")
        assert _SCOPED_PAYMENTS in section
        assert _SCOPED_REPORTS in section
        titles = re.findall(r'pl-tool-card__title">([^<{]+)', section)
        assert titles == ["Email Members", "My Classes", "Payments", "Registrations", "Reports"]
        assert client.get(reverse("billing_admin_dashboard")).status_code == 200

    def it_gives_a_lead_who_teaches_both_sections_tiles(client: Client):
        member = _login(client, "tile_lead_teacher")
        GuildFactory(guild_lead=member)
        _teach(member)
        assert _SCOPED_PAYMENTS in _section(client, "guild")
        assert _SCOPED_PAYMENTS in _section(client, "classes")

    def it_gives_an_instructor_with_no_class_no_tiles(client: Client):
        member = _login(client, "tile_no_class")
        Member.objects.filter(pk=member.pk).update(instructor_slug="noclass", instructor_oriented_at=timezone.now())
        assert "data-tool-scoped-payments" not in _section(client, "classes")
        assert client.get(reverse("billing_admin_dashboard")).status_code == 403

    def it_gives_an_admin_who_teaches_no_classes_tiles(client: Client):
        User.objects.create_superuser(username="tile_admin", password="pass", email="tile_admin@example.com")
        client.login(username="tile_admin", password="pass")
        _teach(User.objects.get(username="tile_admin").member)  # type: ignore[attr-defined]
        assert "data-tool-scoped-payments" not in client.get(reverse("hub_admin_tools")).content.decode()

    def it_gives_a_billing_administrator_who_teaches_both_kinds_of_card(client: Client):
        member = _login(client, "tile_biller_teacher")
        member.admin_capabilities.create(capability=AdminCapability.Capability.BILLING_APPROVER)
        _teach(member)
        html = client.get(reverse("hub_admin_tools")).content.decode()
        assert f'href="{reverse("billing_admin_dashboard")}"' in html
        assert _SCOPED_REPORTS in _section(client, "classes")


def describe_the_teaching_overview():
    def it_points_an_instructor_at_payments_and_reports(client: Client):
        _teach(_login(client, "tile_overview"))
        html = client.get(reverse("classes:teach_overview")).content.decode()
        line = re.search(r"data-teach-money>(.*?)</p>", html, re.S)
        assert line is not None
        assert f'href="{reverse("billing_admin_dashboard")}?tab=payments"' in line.group(1)
        assert f'href="{reverse("billing_admin_reports")}"' in line.group(1)
