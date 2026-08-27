"""The event registry — legacy-trigger seed PLUS the Phase-6 net-new events."""

from __future__ import annotations

import pytest

from core import triggers
from core.events import registry
from core.events.registry import Channel, ChannelDefault, Recipients, get_event

# The Phase-6 net-new event keys, appended to (or replacing) the trigger seed.
_NEW_KEYS = {e.key for e in registry._NEW_EVENTS}
# The two announcement events re-use a seeded key (they REPLACE the Phase-1 entry);
# the rest are brand-new keys not present in the legacy catalogue.
_BRAND_NEW_KEYS = {
    "member.invited",
    "member.login_invite",
    "voting.closing_soon",
    "voting.vote_soon",
    "voting.officers_closing_soon",
    "voting.results_published",
    "voting.results_ready",
    "voting.discord_reminder",
    "voting.results_discord",
    "release.published",
    "event.guild_published",
    "event.community_published",
    "event.lead_meeting_published",
    "guild_announcement.submitted",
    "guild_announcement.approved",
    "guild_announcement.changes_requested",
    "guild_announcement.declined",
    "event.submitted",
    "event.approved",
    "event.changes_requested",
    "event.declined",
    "event.reminder",
    "event.happening_now",
    "discord_guilds_imported",
    "orientation.completed",
    "space.lease_requested",
    "space.cubby_requested",
    "space.request_approved",
    "space.request_declined",
    "meeting.item_proposed",
    "meeting.item_decided",
    "meeting.minutes_approved",
    "meeting.council_minutes_approved",
    "discount_code.requested",
    "billing.charge_failed_admin",
    "class_announcement",
    "waitlist_promoted",
    "waitlist_promoted_pay",
    "registration_removed",
}


def describe_event_registry():
    def describe_seeding():
        def it_registers_every_legacy_trigger_plus_the_new_events():
            # Each trigger key still maps to an event; the brand-new keys are extra.
            assert len(registry.EVENTS) == len(triggers.TRIGGERS) + len(_BRAND_NEW_KEYS)

        def it_keeps_the_legacy_keys_as_a_subset():
            event_keys = {e.key for e in registry.EVENTS}
            trigger_keys = {t.key for t in triggers.TRIGGERS}
            assert trigger_keys <= event_keys
            assert _BRAND_NEW_KEYS <= event_keys

        def it_copies_label_description_and_category_for_unreplaced_triggers():
            # The announcement events are deliberately replaced (their resolver +
            # channels change); every OTHER trigger still mirrors the legacy copy.
            for trigger in triggers.TRIGGERS:
                if trigger.key in _NEW_KEYS:
                    continue
                event = get_event(trigger.key)
                assert event.label == trigger.label
                assert event.description == trigger.description
                assert event.category == trigger.category

    def describe_channels():
        def it_includes_in_app_on_for_every_per_recipient_event():
            # These member-email events are email-only (the invitee has no account; the
            # login-invite reaches someone who hasn't signed in; the Discord-guilds import
            # is a transactional email_to confirmation with no bell row), so the in-app
            # invariant holds for every OTHER event.
            # Discord-broadcast events have no in-app bell (they're @everyone channel posts)
            email_only = {
                "member.invited",
                "member.login_invite",
                "discord_guilds_imported",
                "voting.discord_reminder",
                "voting.results_discord",
            }
            for event in registry.EVENTS:
                if event.key in email_only:
                    assert event.channel(Channel.IN_APP) is None
                    continue
                spec = event.channel(Channel.IN_APP)
                assert spec is not None
                assert spec.default is ChannelDefault.ON

        def it_forces_email_when_a_trigger_declares_force_email():
            # No shipping trigger sets force_email anymore, but the seed translation still
            # honors the flag: a forced trigger seeds an EMAIL channel with the FORCED default.
            forced = triggers.Trigger(
                key="forced_probe", label="Probe", description="d", category="Security", force_email=True
            )
            email = next(spec for spec in registry._channels_from_trigger(forced) if spec.channel is Channel.EMAIL)
            assert email.default is ChannelDefault.FORCED
            assert email.is_forced

        def it_forces_email_for_the_member_invite():
            # member.invited is a forced email (the invitee must receive it).
            spec = get_event("member.invited").channel(Channel.EMAIL)
            assert spec is not None
            assert spec.is_forced

        def it_defaults_email_off_for_non_default_triggers():
            # class_published has email_default=False in the legacy catalogue.
            spec = get_event("class_published").channel(Channel.EMAIL)
            assert spec is not None
            assert spec.default is ChannelDefault.OFF
            assert not spec.is_forced

        def it_offers_push_on_every_in_app_event():
            # Every event that writes an in-app bell row also OFFERS Push (a toggle);
            # events with no bell (forced-email / broadcast-only) declare no Push channel.
            for event in registry.EVENTS:
                push = event.channel(Channel.PUSH)
                if event.channel(Channel.IN_APP) is None:
                    assert push is None
                else:
                    assert push is not None

        def it_defaults_push_on_only_for_the_important_set():
            # Push defaults ON exactly for the curated "relatively important" events; every
            # other event that offers Push defaults OFF (available but quiet).
            on = {
                event.key
                for event in registry.EVENTS
                if (spec := event.channel(Channel.PUSH)) is not None and spec.default is ChannelDefault.ON
            }
            assert on == registry._PUSH_ON_BY_DEFAULT
            assert registry.get_event("class_cancelled").channel(Channel.PUSH).default is ChannelDefault.ON
            # routine / FYI notices stay OFF by default (offered, not forced on)
            for quiet in ("meeting.minutes_approved", "meeting.council_minutes_approved", "orientation.completed"):
                spec = registry.get_event(quiet).channel(Channel.PUSH)
                assert spec is not None
                assert spec.default is ChannelDefault.OFF

        def it_broadcasts_announcements_and_releases_on_discord():
            for key in ("class_published", "guild_announcement", "site_announcement", "release.published"):
                assert get_event(key).has_channel(Channel.DISCORD)

    def describe_resolvers():
        def it_assigns_a_resolver_reference_to_every_event():
            for event in registry.EVENTS:
                assert isinstance(event.recipient, Recipients)

        def it_routes_class_review_to_guild_leadership_or_class_approvers():
            assert get_event("class_review_requested").recipient is Recipients.GUILD_LEADERSHIP_OR_CLASS_APPROVERS

        def it_routes_class_validation_to_class_approvers():
            assert get_event("class_validation_requested").recipient is Recipients.CLASS_APPROVERS

        def it_routes_orientation_requested_to_orienters():
            assert get_event("orientation_requested").recipient is Recipients.GUILD_ORIENTERS

        def it_routes_login_invite_to_single_user():
            assert get_event("member.login_invite").recipient is Recipients.SINGLE_USER

        def it_routes_new_member_joined_to_fog_admins():
            assert get_event("new_member_joined").recipient is Recipients.FOG_ADMINS

    def describe_activity_kind():
        def it_maps_known_triggers_to_their_activity_kind():
            assert get_event("new_member_joined").activity_kind == "member_signup"
            assert get_event("tab_charged").activity_kind == "tab_charged"

        def it_leaves_activity_kind_none_when_no_log_exists():
            assert get_event("class_reminder").activity_kind is None

        def it_leaves_activity_kind_none_when_classes_cmsactivity_mirror_owns_the_site_row():
            # These classes events write their SiteActivity via the CmsActivity mirror
            # (classes.activity._SITE_KIND_MAP), so emit must NOT log a duplicate.
            assert get_event("class_published").activity_kind is None
            assert get_event("registration_confirmed").activity_kind is None
            assert get_event("waitlist_confirmed").activity_kind is None
            assert get_event("class_review_requested").activity_kind is None
            assert get_event("instructor_class_approved").activity_kind is None

    def describe_get_event():
        def it_returns_the_event_for_a_known_key():
            assert get_event("class_published").key == "class_published"

        def it_raises_keyerror_for_an_unknown_key():
            with pytest.raises(KeyError):
                get_event("does_not_exist")

    def describe_eventtype_helpers():
        def it_reports_declared_channels():
            assert get_event("class_reminder").has_channel(Channel.EMAIL)

        def it_reports_undeclared_channels_as_absent():
            assert not get_event("class_reminder").has_channel(Channel.DISCORD)

        def it_lists_channels_in_declared_order():
            # class_published now REPLACES the seed to add the Discord broadcast channel,
            # appended after the preserved in-app/email/push channels.
            assert get_event("class_published").channel_list == [
                Channel.IN_APP,
                Channel.EMAIL,
                Channel.PUSH,
                Channel.DISCORD,
            ]
