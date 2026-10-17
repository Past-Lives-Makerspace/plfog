"""Overdue class reviews (#817): the list, the daily Discord post, the admin queue marker and the Home card."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.core.management import call_command
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from classes.factories import CategoryFactory, ClassOfferingFactory, ClassSessionFactory, InstructorFactory, UserFactory
from classes.models import ClassApproval, ClassOffering
from classes.overdue import overdue_reviews
from core.models import DiscordWebhookRoute, EventDelivery
from membership.models import AdminCapability, Member
from tests.membership.factories import GuildFactory

pytestmark = pytest.mark.django_db

Status = ClassOffering.Status
Role = ClassApproval.Role


def _gate(offering: ClassOffering, role: str, age_days: float, **fields: object) -> ClassApproval:
    gate = ClassApproval.objects.create(class_offering=offering, role=role, **fields)
    ClassApproval.objects.filter(pk=gate.pk).update(created_at=timezone.now() - timedelta(days=age_days))
    gate.refresh_from_db()
    return gate


def _lead_named(name: str) -> Member:
    member = Member.objects.get(user=UserFactory(username=f"{name}@example.com", email=f"{name}@example.com"))
    member.full_legal_name = name
    member.save(update_fields=["full_legal_name"])
    return member


@pytest.fixture
def mugs() -> ClassOffering:
    guild = GuildFactory(name="Ceramics Guild", guild_lead=_lead_named("Jay Holtman"))
    offering = ClassOfferingFactory(
        title="Make Two Mugs",
        status=Status.PENDING,
        instructor=InstructorFactory(full_legal_name="Erin Corrales"),
        category=CategoryFactory(guild=guild),
    )
    ClassSessionFactory(class_offering=offering, starts_at=timezone.now() + timedelta(days=23, hours=1))
    return offering


def describe_overdue_reviews():
    def it_lists_a_review_open_three_days_or_more(mugs: ClassOffering):
        _gate(mugs, Role.GUILD_LEAD, 3.01)
        [row] = overdue_reviews()
        assert row.offering == mugs
        assert row.days_waiting == 3
        assert row.waiting_on == "Jay Holtman"
        assert row.marker == "Waiting 3 days · starts in 23 days"

    def it_leaves_out_a_review_younger_than_three_days(mugs: ClassOffering):
        _gate(mugs, Role.GUILD_LEAD, 2.9)
        assert overdue_reviews() == []

    def it_counts_an_admin_gate_too():
        offering = ClassOfferingFactory(status=Status.PENDING)
        _gate(offering, Role.ADMIN, 4)
        [row] = overdue_reviews()
        assert row.waiting_on == "admins"
        assert row.marker == "Waiting 4 days · no dates yet"

    def it_ages_a_class_on_its_guild_lead_gate_when_an_admin_row_opened_early(mugs: ClassOffering):
        lead_gate = _gate(mugs, Role.GUILD_LEAD, 15)
        _gate(mugs, Role.ADMIN, 0.1)
        [row] = overdue_reviews()
        assert row.gate == lead_gate
        assert row.days_waiting == 15

    def it_ages_an_early_admin_row_from_the_guild_leads_approval(mugs: ClassOffering):
        # An admin peeked at the review page on day 0; the lead approved one day ago.
        _gate(mugs, Role.GUILD_LEAD, 20, decision=ClassApproval.Decision.APPROVED)
        ClassApproval.objects.filter(class_offering=mugs, role=Role.GUILD_LEAD).update(
            decided_at=timezone.now() - timedelta(days=1)
        )
        _gate(mugs, Role.ADMIN, 20)
        assert overdue_reviews() == []
        ClassApproval.objects.filter(class_offering=mugs, role=Role.GUILD_LEAD).update(
            decided_at=timezone.now() - timedelta(days=4, hours=1)
        )
        [row] = overdue_reviews()
        assert row.days_waiting == 4
        assert row.waiting_on == "admins"

    def it_says_when_a_pending_classs_dates_have_passed():
        offering = ClassOfferingFactory(status=Status.PENDING)
        ClassSessionFactory(class_offering=offering, starts_at=timezone.now() - timedelta(days=2))
        _gate(offering, Role.ADMIN, 5)
        [row] = overdue_reviews()
        assert row.marker == "Waiting 5 days · its dates have passed"

    @pytest.mark.parametrize("status", [Status.DRAFT, Status.PUBLISHED, Status.CANCELLED])
    def it_skips_a_class_no_longer_pending(mugs: ClassOffering, status: str):
        _gate(mugs, Role.GUILD_LEAD, 10)
        ClassOffering.objects.filter(pk=mugs.pk).update(status=status)
        assert overdue_reviews() == []

    def it_skips_a_decided_gate(mugs: ClassOffering):
        _gate(mugs, Role.GUILD_LEAD, 10, decision=ClassApproval.Decision.APPROVED)
        assert overdue_reviews() == []

    def it_puts_the_soonest_class_first():
        later = ClassOfferingFactory(title="Later", status=Status.PENDING)
        ClassSessionFactory(class_offering=later, starts_at=timezone.now() + timedelta(days=40))
        sooner = ClassOfferingFactory(title="Sooner", status=Status.PENDING)
        ClassSessionFactory(class_offering=sooner, starts_at=timezone.now() + timedelta(days=5))
        _gate(later, Role.ADMIN, 9)
        _gate(sooner, Role.ADMIN, 4)
        assert [row.offering for row in overdue_reviews()] == [sooner, later]


def describe_send_overdue_review_alerts():
    @pytest.fixture
    def posts(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, object]]:
        sent: list[tuple[str, object]] = []
        monkeypatch.setattr("core.events.discord.post_embed", lambda url, message: sent.append((url, message)) or True)
        DiscordWebhookRoute.objects.create(
            event_key="class_review_overdue", webhook_url="https://discord.com/api/webhooks/1/alerts"
        )
        return sent

    def it_posts_a_plain_message_to_the_routed_channel(mugs: ClassOffering, posts: list):
        _gate(mugs, Role.GUILD_LEAD, 15)
        call_command("send_overdue_review_alerts")
        [(url, message)] = posts
        assert url == "https://discord.com/api/webhooks/1/alerts"
        text = f"{message.title}\n{message.body}"
        assert "Make Two Mugs" in text
        assert "Erin Corrales" in text
        assert "Ceramics Guild" in text
        assert "15 days for Jay Holtman" in text
        assert "First session in 23 days" in text
        assert reverse("classes:admin_class_review", kwargs={"pk": mugs.pk}) in text
        assert "@" not in text

    def it_fails_loudly_and_spends_nothing_without_an_alerts_route(
        mugs: ClassOffering, monkeypatch: pytest.MonkeyPatch
    ):
        from django.core.management.base import CommandError

        sent: list[object] = []
        monkeypatch.setattr("core.events.discord.post_embed", lambda url, message: sent.append(message) or True)
        _gate(mugs, Role.GUILD_LEAD, 15)
        with pytest.raises(CommandError, match="no enabled Discord route"):
            call_command("send_overdue_review_alerts")
        assert sent == []
        assert not EventDelivery.objects.filter(event_key="class_review_overdue").exists()

    def it_posts_nothing_for_a_young_review(mugs: ClassOffering, posts: list):
        _gate(mugs, Role.GUILD_LEAD, 2)
        call_command("send_overdue_review_alerts")
        assert posts == []

    def it_posts_once_per_three_days_of_waiting(mugs: ClassOffering, posts: list):
        gate = _gate(mugs, Role.GUILD_LEAD, 3.1)
        call_command("send_overdue_review_alerts")
        call_command("send_overdue_review_alerts")
        assert len(posts) == 1
        for age in (4.1, 5.1):
            ClassApproval.objects.filter(pk=gate.pk).update(created_at=timezone.now() - timedelta(days=age))
            call_command("send_overdue_review_alerts")
        assert len(posts) == 1
        ClassApproval.objects.filter(pk=gate.pk).update(created_at=timezone.now() - timedelta(days=6.1))
        call_command("send_overdue_review_alerts")
        assert len(posts) == 2
        assert EventDelivery.objects.filter(event_key="class_review_overdue").count() == 2

    def it_sends_no_email_or_bell(mugs: ClassOffering, posts: list):
        from django.core import mail

        from core.models import Notification

        mail.outbox = []
        _gate(mugs, Role.GUILD_LEAD, 15)
        call_command("send_overdue_review_alerts")
        assert mail.outbox == []
        assert not Notification.objects.exists()


def _admin_client(client: Client) -> Client:
    member = _lead_named("Ada Admin")
    member.fog_role = Member.FogRole.ADMIN
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()
    client.force_login(member.user)
    return client


def describe_the_admin_queue_marker():
    def it_marks_and_lifts_an_overdue_guild_lead_row(client: Client, mugs: ClassOffering):
        fresh = ClassOfferingFactory(
            title="Fresh Class", status=Status.PENDING, category=CategoryFactory(guild=mugs.category.guild)
        )
        _gate(fresh, Role.GUILD_LEAD, 0.5)
        _gate(mugs, Role.GUILD_LEAD, 15)
        html = _admin_client(client).get(reverse("classes:admin_overview")).content.decode()
        assert "Waiting 15 days · starts in 23 days" in html
        assert html.index("Make Two Mugs") < html.index("Fresh Class")

    def it_marks_an_overdue_waiting_on_you_row_aged_from_its_admin_gate(client: Client):
        offering = ClassOfferingFactory(title="Old Draft Now Pending", status=Status.PENDING)
        ClassOffering.objects.filter(pk=offering.pk).update(created_at=timezone.now() - timedelta(days=60))
        _gate(offering, Role.ADMIN, 4)
        html = _admin_client(client).get(reverse("classes:admin_overview")).content.decode()
        assert "Waiting 4 days · no dates yet" in html
        assert "waiting 4\xa0days" in html


def describe_the_home_card():
    def it_shows_admins_the_overdue_reviews(client: Client, mugs: ClassOffering):
        _gate(mugs, Role.GUILD_LEAD, 15)
        html = _admin_client(client).get(reverse("hub_home")).content.decode()
        assert 'data-block="overdue-reviews"' in html
        assert "Waiting on Jay Holtman" in html

    def it_shows_a_cms_administrator_the_card(client: Client, mugs: ClassOffering):
        _gate(mugs, Role.GUILD_LEAD, 15)
        member = _lead_named("Cam Approver")
        AdminCapability.objects.create(member=member, capability=AdminCapability.Capability.CLASS_APPROVER)
        client.force_login(member.user)
        assert 'data-block="overdue-reviews"' in client.get(reverse("hub_home")).content.decode()

    def it_never_shows_a_plain_member_or_the_guild_lead(client: Client, mugs: ClassOffering):
        _gate(mugs, Role.GUILD_LEAD, 15)
        client.force_login(mugs.category.guild.guild_lead.user)
        assert 'data-block="overdue-reviews"' not in client.get(reverse("hub_home")).content.decode()
