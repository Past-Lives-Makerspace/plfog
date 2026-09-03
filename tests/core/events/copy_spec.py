"""BDD specs for the default-copy catalogue + documented merge fields (§2.3)."""

from __future__ import annotations

import pytest

from core.events.copy import (
    COPY_CHANNELS,
    audience_description,
    default_copy_for,
    placeholders_for,
    sample_context_for,
    seedable_rows,
)
from core.events.registry import Channel, all_events, get_event
from core.events.rendering import placeholders_in


def describe_seedable_rows():
    def it_yields_one_row_per_event_and_copy_channel():
        rows = seedable_rows()
        assert len(rows) == len(all_events()) * len(COPY_CHANNELS)

    def it_covers_every_registered_event():
        covered = {event_key for event_key, _channel, _copy in seedable_rows()}
        assert covered == {e.key for e in all_events()}


def describe_event_copy_consistency():
    @pytest.mark.parametrize("event", all_events(), ids=lambda e: e.key)
    def it_only_uses_documented_placeholders_in_its_defaults(event):
        # Every placeholder appearing in ANY default copy must be documented for the
        # event (so seeded copy never renders a [missing: …] marker against its own
        # sample context).
        allowed = set(placeholders_for(event.key))
        for channel in COPY_CHANNELS:
            default = default_copy_for(event.key, channel)
            for fragment in (default.subject, default.body_text, default.body_html):
                for name in placeholders_in(fragment):
                    assert name in allowed, f"{event.key}/{channel.value} uses undocumented {{{{ {name} }}}}"

    @pytest.mark.parametrize("event", all_events(), ids=lambda e: e.key)
    def it_provides_a_sample_value_for_every_documented_placeholder(event):
        sample = sample_context_for(event.key)
        for name in placeholders_for(event.key):
            assert name in sample, f"{event.key} documents {{{{ {name} }}}} but has no sample value"


def describe_default_copy_for():
    def it_falls_back_to_email_copy_for_discord():
        # Discord reuses the email copy (broadcast embed built from title+body).
        email = default_copy_for("registration_confirmed", Channel.EMAIL)
        discord = default_copy_for("registration_confirmed", Channel.DISCORD)
        assert discord.subject == email.subject
        assert discord.body_text == email.body_text


def describe_audience_description():
    def it_describes_a_guild_scoped_audience():
        event = get_event("class_review_requested")
        assert "guild" in audience_description(event).lower()

    def it_describes_the_global_admin_audience():
        event = get_event("class_validation_requested")
        assert "admin" in audience_description(event).lower()

    def it_describes_the_class_roster_audience():
        event = get_event("class_announcement")
        assert "class" in audience_description(event).lower()

    def it_has_a_description_for_every_registered_events_recipient():
        # The settings/catalogue page renders audience_description for every event; a new event
        # whose recipient has no description KeyErrors the whole page (regression guard).
        for event in all_events():
            assert audience_description(event)


def describe_generic_fallback():
    def it_uses_the_event_label_and_description_for_an_uncurated_event():
        # waitlist_confirmed has no curated copy → generic default from label/desc.
        event = get_event("waitlist_confirmed")
        default = default_copy_for("waitlist_confirmed", Channel.IN_APP)
        assert default.subject == event.label
        assert event.description in default.body_text

    def it_exposes_member_name_as_the_only_placeholder():
        assert placeholders_for("waitlist_confirmed") == ("member_name",)

    def it_gives_discord_its_own_greeting_free_copy():
        # The generic email copy greets the recipient; the Discord broadcast has no
        # recipient, so it must get label + description only — never the greeting.
        event = get_event("waitlist_confirmed")
        discord = default_copy_for("waitlist_confirmed", Channel.DISCORD)
        assert discord.subject == event.label
        assert discord.body_text == event.description


def describe_discord_copy_is_broadcast_safe():
    @pytest.mark.parametrize("event", [e for e in all_events() if e.has_channel(Channel.DISCORD)], ids=lambda e: e.key)
    def it_never_greets_a_single_recipient(event):
        # A Discord post is a channel broadcast with no recipient in context; a
        # per-recipient greeting renders as "Hi [missing: member_name]" in a guild's
        # channel (the meeting.minutes_approved incident). Curated or generated,
        # every Discord-enabled event's Discord copy must be greeting-free.
        copy = default_copy_for(event.key, Channel.DISCORD)
        for fragment in (copy.subject, copy.body_text, copy.body_html):
            # "Hi {{ ..." catches a greeting built on ANY per-recipient placeholder,
            # not just member_name.
            assert "Hi {{" not in fragment, f"{event.key} greets a recipient in its Discord copy"
