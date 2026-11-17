"""BDD specs for the instructor-raised staff events and the refund_authority resolver."""

from __future__ import annotations

import pytest
from django.core import mail

from core.events.copy import audience_description, default_copy_for, sample_context_for, seedable_rows
from core.events.emit import emit
from core.events.registry import Channel, Recipients, get_event
from core.events.rendering import render_text
from core.events.resolvers import guild_lead_or_class_approvers, refund_authority
from core.events.settings_matrix import STAFF_RECIPIENTS
from core.models import Notification, TransactionalEmailLog
from membership.models import AdminCapability, Member
from tests.membership.factories import GuildFactory

pytestmark = pytest.mark.django_db


def _grant(member: Member, capability: str) -> None:
    member.admin_capabilities.create(capability=capability)


def describe_refund_authority_resolver():
    def it_unions_fog_admins_and_refunds_holders_deduped(linked_member):
        admin = linked_member(fog_role=Member.FogRole.ADMIN)
        holder = linked_member()
        _grant(holder, AdminCapability.Capability.REFUNDS)
        both = linked_member(fog_role=Member.FogRole.ADMIN)
        _grant(both, AdminCapability.Capability.REFUNDS)
        linked_member()  # a plain member
        billing = linked_member()
        _grant(billing, AdminCapability.Capability.BILLING_APPROVER)
        recipients = refund_authority({})
        assert {user.pk for user, _reason in recipients} == {admin.user.pk, holder.user.pk, both.user.pk}
        assert len(recipients) == 3

    def it_is_registered_grouped_under_staff_and_described():
        assert Recipients.REFUND_AUTHORITY in STAFF_RECIPIENTS
        assert "refund" in audience_description(get_event("class_cancelled_admin_notice")).lower()


def describe_class_cancelled_admin_notice():
    def it_declares_in_app_and_email_with_no_broadcast():
        event = get_event("class_cancelled_admin_notice")
        assert event.recipient is Recipients.REFUND_AUTHORITY
        assert Channel.IN_APP in event.channel_list and Channel.EMAIL in event.channel_list
        assert Channel.DISCORD not in event.channel_list
        assert event.activity_kind is None

    def it_renders_every_placeholder_in_both_channels():
        sample = sample_context_for("class_cancelled_admin_notice")
        for channel in (Channel.IN_APP, Channel.EMAIL):
            copy = default_copy_for("class_cancelled_admin_notice", channel)
            for template in (copy.subject, copy.body_text, copy.body_html):
                assert "[missing:" not in render_text(template, sample)
        email = render_text(default_copy_for("class_cancelled_admin_notice", Channel.EMAIL).body_text, sample)
        assert "Robin Vale cancelled Intro to Lost-Wax Casting. 3 paid registrations need refunds." in email
        assert "https://pastlives.example/classes/admin/42/registrations/" in email

    def it_reaches_an_admin_and_a_refunds_holder_by_bell_and_email(linked_member):
        admin = linked_member(email="refund-admin@example.com", fog_role=Member.FogRole.ADMIN)
        holder = linked_member(email="refund-holder@example.com")
        _grant(holder, AdminCapability.Capability.REFUNDS)
        mail.outbox = []
        emit(
            "class_cancelled_admin_notice",
            context={
                "instructor_name": "Robin",
                "class_title": "Casting",
                "paid_count": "2",
                "registrations_url": "https://example.test/regs/",
            },
            url="/classes/admin/1/registrations/",
            period="spec:notice",
        )
        for user in (admin.user, holder.user):
            row = Notification.objects.get(trigger="class_cancelled_admin_notice", user=user)
            assert "Robin cancelled Casting" in row.title
            assert "2 paid registrations need refunds." in row.body
        assert {m.to[0] for m in mail.outbox} == {"refund-admin@example.com", "refund-holder@example.com"}
        assert all("[missing:" not in m.body for m in mail.outbox)
        assert TransactionalEmailLog.objects.filter(trigger_kind="class_cancelled_admin_notice").count() == 2


def describe_class_rescheduled_lead_notice():
    """#1186: an instructor moved a live class; its guild lead, else the CMS Administrators, hear once."""

    def it_routes_composed_with_in_app_and_email():
        event = get_event("class_rescheduled_lead_notice")
        assert event.recipient is Recipients.GUILD_LEAD_OR_CLASS_APPROVERS
        assert Channel.IN_APP in event.channel_list and Channel.EMAIL in event.channel_list
        assert Channel.DISCORD not in event.channel_list
        assert Recipients.GUILD_LEAD_OR_CLASS_APPROVERS in STAFF_RECIPIENTS
        assert "CMS Administrators" in audience_description(event)

    def it_renders_every_placeholder_in_both_channels():
        sample = sample_context_for("class_rescheduled_lead_notice")
        for channel in (Channel.IN_APP, Channel.EMAIL):
            copy = default_copy_for("class_rescheduled_lead_notice", channel)
            for template in (copy.subject, copy.body_text, copy.body_html):
                rendered = render_text(template, sample)
                assert "[missing:" not in rendered
                assert " - " not in rendered and "\u2014" not in rendered
        email = render_text(default_copy_for("class_rescheduled_lead_notice", Channel.EMAIL).body_text, sample)
        assert "Was: Sat, Nov 22, 2026 at 10:00 AM to 12:00 PM" in email
        assert "Now: Sun, Nov 23, 2026 at 1:00 PM to 3:00 PM" in email

    def it_reaches_the_guild_lead_alone_when_the_guild_has_one(linked_member):
        lead = linked_member(email="lead@example.com")
        holder = linked_member(email="cms@example.com")
        _grant(holder, AdminCapability.Capability.CLASS_APPROVER)
        guild = GuildFactory(name="Lead Guild", guild_lead=lead)
        assert [user.pk for user, _ in guild_lead_or_class_approvers({"guild": guild})] == [lead.user.pk]

    def it_falls_back_to_the_cms_administrators_with_no_guild_or_no_lead(linked_member):
        holder = linked_member(email="cms2@example.com")
        _grant(holder, AdminCapability.Capability.CLASS_APPROVER)
        leadless = GuildFactory(name="Leadless Guild", guild_lead=None)
        for guild in (None, leadless):
            assert [user.pk for user, _ in guild_lead_or_class_approvers({"guild": guild})] == [holder.user.pk]

    def it_fails_loudly_without_a_guild_key():
        with pytest.raises(KeyError):
            guild_lead_or_class_approvers({})

    def it_emails_at_once_not_in_the_morning_digest(linked_member):
        holder = linked_member(email="cms3@example.com")
        _grant(holder, AdminCapability.Capability.CLASS_APPROVER)
        mail.outbox = []
        emit(
            "class_rescheduled_lead_notice",
            context={**sample_context_for("class_rescheduled_lead_notice"), "guild": None},
            url="/classes/teach/classes/1/",
            period="spec:new-dates",
        )
        row = Notification.objects.get(trigger="class_rescheduled_lead_notice", user=holder.user)
        assert row.title == "Intro to Lost-Wax Casting has new dates"
        (email,) = [m for m in mail.outbox if m.to == ["cms3@example.com"]]
        assert "Now: Sun, Nov 23, 2026" in email.body

    def it_seeds_rows_for_both_new_events():
        seeded = {(key, channel) for key, channel, _default in seedable_rows()}
        for key in ("class_cancelled_admin_notice", "class_rescheduled_lead_notice"):
            assert (key, Channel.IN_APP) in seeded
            assert (key, Channel.EMAIL) in seeded
