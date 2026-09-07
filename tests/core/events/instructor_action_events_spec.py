"""BDD specs for the two instructor-raised staff events and the refund_authority resolver."""

from __future__ import annotations

import pytest
from django.core import mail

from core.events.copy import audience_description, default_copy_for, sample_context_for, seedable_rows
from core.events.emit import emit
from core.events.registry import Channel, Recipients, get_event
from core.events.rendering import render_text
from core.events.resolvers import refund_authority
from core.events.settings_matrix import STAFF_RECIPIENTS
from core.models import Notification, TransactionalEmailLog
from membership.models import AdminCapability, Member

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


def describe_class_change_requested():
    def it_routes_to_the_cms_administrators_with_in_app_and_email():
        event = get_event("class_change_requested")
        assert event.recipient is Recipients.CLASS_APPROVERS
        assert Channel.IN_APP in event.channel_list and Channel.EMAIL in event.channel_list
        assert Channel.DISCORD not in event.channel_list

    def it_renders_every_placeholder_in_both_channels():
        sample = sample_context_for("class_change_requested")
        for channel in (Channel.IN_APP, Channel.EMAIL):
            copy = default_copy_for("class_change_requested", channel)
            for template in (copy.subject, copy.body_text, copy.body_html):
                assert "[missing:" not in render_text(template, sample)
        email = render_text(default_copy_for("class_change_requested", Channel.EMAIL).body_text, sample)
        assert "Please move the price to $85 and add one more seat." in email
        assert "https://pastlives.example/classes/admin/42/edit/" in email

    def it_reaches_a_holder_with_the_note(linked_member):
        holder = linked_member(email="cms-holder@example.com")
        _grant(holder, AdminCapability.Capability.CLASS_APPROVER)
        linked_member(email="plain@example.com")
        mail.outbox = []
        emit(
            "class_change_requested",
            context={
                "instructor_name": "Robin",
                "class_title": "Casting",
                "note": "Move it to Friday.",
                "edit_url": "https://example.test/edit/",
            },
            url="/classes/admin/1/edit/",
            period="spec:change",
        )
        row = Notification.objects.get(trigger="class_change_requested", user=holder.user)
        assert row.title == "Robin asked for a change to Casting"
        assert row.body == "Move it to Friday."
        assert [m.to for m in mail.outbox] == [["cms-holder@example.com"]]
        assert "Move it to Friday." in mail.outbox[0].body

    def it_seeds_rows_for_both_new_events():
        seeded = {(key, channel) for key, channel, _default in seedable_rows()}
        for key in ("class_cancelled_admin_notice", "class_change_requested"):
            assert (key, Channel.IN_APP) in seeded
            assert (key, Channel.EMAIL) in seeded
