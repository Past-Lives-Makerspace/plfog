"""The event registry — seeded from the legacy trigger catalogue, faithfully."""

from __future__ import annotations

import pytest

from core import triggers
from core.events import registry
from core.events.registry import Channel, ChannelDefault, Recipients, get_event


def describe_event_registry():
    def describe_seeding():
        def it_registers_one_event_per_legacy_trigger():
            assert len(registry.EVENTS) == len(triggers.TRIGGERS)

        def it_shares_keys_with_the_legacy_catalogue():
            event_keys = {e.key for e in registry.EVENTS}
            trigger_keys = {t.key for t in triggers.TRIGGERS}
            assert event_keys == trigger_keys

        def it_copies_label_description_and_category():
            for trigger in triggers.TRIGGERS:
                event = get_event(trigger.key)
                assert event.label == trigger.label
                assert event.description == trigger.description
                assert event.category == trigger.category

    def describe_channels():
        def it_always_includes_in_app_on():
            for event in registry.EVENTS:
                spec = event.channel(Channel.IN_APP)
                assert spec is not None
                assert spec.default is ChannelDefault.ON

        def it_forces_email_for_force_email_triggers():
            event = get_event("new_login")
            spec = event.channel(Channel.EMAIL)
            assert spec is not None
            assert spec.default is ChannelDefault.FORCED
            assert spec.is_forced

        def it_defaults_email_off_for_non_default_triggers():
            # class_published has email_default=False in the legacy catalogue.
            spec = get_event("class_published").channel(Channel.EMAIL)
            assert spec is not None
            assert spec.default is ChannelDefault.OFF
            assert not spec.is_forced

        def it_maps_push_default_flag_to_push_channel():
            # No legacy trigger sets push_default=True, so all map to OFF.
            for event in registry.EVENTS:
                spec = event.channel(Channel.PUSH)
                assert spec is not None
                assert spec.default is ChannelDefault.OFF

    def describe_resolvers():
        def it_assigns_a_resolver_reference_to_every_event():
            for event in registry.EVENTS:
                assert isinstance(event.recipient, Recipients)

        def it_routes_class_review_to_guild_leadership():
            assert get_event("class_review_requested").recipient is Recipients.GUILD_LEADERSHIP

        def it_routes_orientation_requested_to_orienters():
            assert get_event("orientation_requested").recipient is Recipients.GUILD_ORIENTERS

        def it_routes_new_login_to_single_user():
            assert get_event("new_login").recipient is Recipients.SINGLE_USER

        def it_routes_new_member_joined_to_fog_admins():
            assert get_event("new_member_joined").recipient is Recipients.FOG_ADMINS

    def describe_activity_kind():
        def it_maps_known_triggers_to_their_activity_kind():
            assert get_event("registration_confirmed").activity_kind == "class_registered"
            assert get_event("tab_charged").activity_kind == "tab_charged"

        def it_leaves_activity_kind_none_when_no_log_exists():
            assert get_event("class_reminder").activity_kind is None

    def describe_get_event():
        def it_returns_the_event_for_a_known_key():
            assert get_event("class_published").key == "class_published"

        def it_raises_keyerror_for_an_unknown_key():
            with pytest.raises(KeyError):
                get_event("does_not_exist")

    def describe_eventtype_helpers():
        def it_reports_declared_channels():
            assert get_event("new_login").has_channel(Channel.EMAIL)

        def it_reports_undeclared_channels_as_absent():
            assert not get_event("new_login").has_channel(Channel.DISCORD)

        def it_lists_channels_in_declared_order():
            assert get_event("class_published").channel_list == [Channel.IN_APP, Channel.EMAIL, Channel.PUSH]
