"""emit() routes a capability event to holders only — a non-holder admin gets nothing,
even with a saved preference (the capability is the master switch)."""

from __future__ import annotations

import pytest

from core.events.emit import emit
from core.events.registry import Channel
from core.models import Notification, NotificationPreference
from membership.models import AdminCapability, Member

pytestmark = pytest.mark.django_db


def _grant(member, capability):
    member.admin_capabilities.create(capability=capability)


# space.lease_requested declares in_app ON + email ON + push OFF, and routes to
# SPACE_APPROVERS — a capability-scoped event to probe (instructor_application_received was,
# until the application was retired, #1295).
_EVENT = "space.lease_requested"


def describe_capability_fan_out():
    def it_delivers_default_channels_to_a_holder(linked_member):
        holder = linked_member()
        _grant(holder, AdminCapability.Capability.SPACE_APPROVER)
        result = emit(_EVENT, context={}, title="Validate", body="b")
        assert (holder.user_id, Channel.IN_APP) in result.delivered
        assert (holder.user_id, Channel.EMAIL) in result.delivered

    def it_delivers_nothing_to_a_non_holder_admin_even_with_a_preference(linked_member):
        holder = linked_member()
        _grant(holder, AdminCapability.Capability.SPACE_APPROVER)
        admin = linked_member(fog_role=Member.FogRole.ADMIN)
        # An explicit opt-in used to reach an "optional" admin; holders-only routing means
        # a non-holder admin is not a recipient at all, so even a saved preference is inert.
        NotificationPreference.objects.create(user=admin.user, event_key=_EVENT, channel="email", enabled=True)
        result = emit(_EVENT, context={}, title="Validate", body="b")
        assert not any(pk == admin.user_id for pk, _ in result.delivered)
        assert not Notification.objects.filter(user=admin.user, trigger=_EVENT).exists()
