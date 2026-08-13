"""emit() honors the capability optional tier: holders get defaults, other admins opt in."""

from __future__ import annotations

import pytest

from core.events.emit import emit
from core.events.registry import Channel
from core.models import Notification, NotificationPreference
from membership.models import AdminCapability, Member

pytestmark = pytest.mark.django_db


def _grant(member, capability):
    member.admin_capabilities.create(capability=capability)


# class_validation_requested declares in_app ON + email ON (email_default) + push OFF,
# and routes to CLASS_APPROVERS — the canonical capability-scoped event to probe.
_EVENT = "class_validation_requested"


def describe_capability_fan_out():
    def it_delivers_default_channels_to_a_holder(linked_member):
        holder = linked_member()
        _grant(holder, AdminCapability.Capability.CLASS_APPROVER)
        result = emit(_EVENT, context={}, title="Validate", body="b")
        assert (holder.user_id, Channel.IN_APP) in result.delivered
        assert (holder.user_id, Channel.EMAIL) in result.delivered

    def it_delivers_nothing_to_a_non_holder_admin_without_a_preference(linked_member):
        holder = linked_member()
        _grant(holder, AdminCapability.Capability.CLASS_APPROVER)
        admin = linked_member(fog_role=Member.FogRole.ADMIN)
        result = emit(_EVENT, context={}, title="Validate", body="b")
        # The optional admin gets no channel at all — the bell included.
        assert not any(pk == admin.user_id for pk, _ in result.delivered)
        assert not Notification.objects.filter(user=admin.user, trigger=_EVENT).exists()

    def it_delivers_only_the_opted_in_channel_to_an_optional_admin(linked_member):
        holder = linked_member()
        _grant(holder, AdminCapability.Capability.CLASS_APPROVER)
        admin = linked_member(fog_role=Member.FogRole.ADMIN)
        NotificationPreference.objects.create(user=admin.user, event_key=_EVENT, channel="email", enabled=True)
        result = emit(_EVENT, context={}, title="Validate", body="b")
        assert (admin.user_id, Channel.EMAIL) in result.delivered
        assert (admin.user_id, Channel.IN_APP) not in result.delivered
