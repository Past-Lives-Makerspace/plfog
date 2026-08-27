"""Completeness guards for the copy-review Email Copy Gallery.

Runs in the NORMAL suite (not gated, no DB) so adding a new email — a template
pair under the email dirs, or an inline ``core.email.send`` call site — without
registering it in ``tests/e2e/email_gallery/registry.py`` fails ordinary CI.
That enforcement is the point: "all email copy is reviewable" stays true going
forward, not just on the day the gallery shipped.
"""

from __future__ import annotations

from tests.e2e.email_gallery.registry import (
    _STRUCTURAL_EVENT_KEYS,
    SECTIONS,
    Renderer,
    discover_inline_sends,
    discover_template_pairs,
    gallery_emails,
    no_email_events,
    registered_template_pairs,
    unregistered_inline_sends,
)

EXPECTED_PAIRS = {
    # classes
    "admin_validation_request",
    "confirmation",
    "instructor_new_registration",
    "reminder",
    "review_decision",
    "review_request",
    "review_submitted_instructor",
    "waitlist_joined",
    "waitlist_spot_opened",
    "welcome",
    "promoted",
    "promoted_pay",
    "removed",
    # membership
    "discord_guilds_imported",
    "orientation_request",
    "orientation_confirmed",
    "orientation_declined",
    "orientation_cancelled",
    "orientation_lead_request",
    "orientation_thankyou",
    # billing
    "charge_failed_admin",
    "receipt",
    # account (allauth overrides)
    "login_code",
    "unknown_account",
    "account_already_exists",
}


def describe_email_gallery_completeness():
    def it_registers_every_discovered_template_pair():
        """Guard 1: a new email template pair with no gallery registration fails CI."""
        missing = discover_template_pairs() - registered_template_pairs()
        assert missing == set(), (
            f"Email template pairs with no gallery registration: {sorted(missing)} — "
            "register them in tests/e2e/email_gallery/registry.py so their copy is reviewable "
            "on copy-review.pastlives.space."
        )

    def it_discovers_the_expected_25_pairs():
        """Pins the discovery rule so it never silently sweeps in (or drops) templates."""
        assert discover_template_pairs() == EXPECTED_PAIRS
        assert len(EXPECTED_PAIRS) == 25

    def it_excludes_shells_and_partials():
        discovered = discover_template_pairs()
        for shell in ("_base", "_footer", "_release_shell", "notification_shell", "base_message"):
            assert shell not in discovered

    def it_registers_every_inline_string_send():
        """Guard 2 (B3): every direct ``core.email.send`` call site is registered or allowlisted."""
        missing_kinds, missing_dynamic = unregistered_inline_sends()
        assert missing_kinds == {}, (
            f"Inline email sends with no gallery card or allowlist entry: {missing_kinds} — "
            "card them or allowlist them (with a reason) in tests/e2e/email_gallery/registry.py."
        )
        assert missing_dynamic == [], (
            f"Send sites with a computed trigger_kind not in the dynamic allowlist: {missing_dynamic}"
        )

    def it_pins_the_current_inline_send_sites():
        """The known literal trigger_kinds — a new one appearing here means new email copy."""
        literal_kinds, dynamic_sites = discover_inline_sends()
        assert set(literal_kinds) == {
            "core.find_account",
            "classes.welcome_email",
            "classes.duplicate_payment_alert",
            "classes.welcome_email_test",
            "classes.instructor_message",
            "classes.admin_message",
            "membership.orientation_orphan_payment",
            "release_email.test",
            "announcement.test",
            "hub.beta_feedback",
        }
        assert set(dynamic_sites) == {"plfog/adapters.py"}

    def it_covers_every_email_section():
        """Every card sits in a real section; every spine event is carded or listed."""
        emails = gallery_emails()
        for email in emails:
            assert email.section in SECTIONS, f"{email.key} has unknown section {email.section!r}"

        from core.events.registry import all_events

        carded_keys = frozenset().union(*(e.event_keys for e in emails)) | {
            e.key for e in emails if e.renderer is Renderer.SPINE_COPY
        }
        listed_keys = {event.key for event, _reason in no_email_events()}
        for event in all_events():
            assert event.key in carded_keys or event.key in listed_keys, (
                f"spine event {event.key} is neither carded nor listed under 'No email is sent'"
            )
            assert not (event.key in carded_keys and event.key in listed_keys), (
                f"spine event {event.key} is both carded and listed as no-email"
            )

    def it_lists_guild_joined_as_a_no_email_event():
        """#4: guild_joined declares no_email (its welcome email was removed), so it is LISTED
        under 'No email is sent' with a plain reason — never re-carded as an opt-in email."""
        listed = {event.key: reason for event, reason in no_email_events()}
        assert "guild_joined" in listed
        assert listed["guild_joined"].endswith("no email channel")
        emails = gallery_emails()
        carded_keys = frozenset().union(*(e.event_keys for e in emails)) | {
            e.key for e in emails if e.renderer is Renderer.SPINE_COPY
        }
        assert "guild_joined" not in carded_keys

    def it_dedups_review_decision_two_keys():
        """M1: both decision events resolve to the single review_decision entry."""
        entry = next(e for e in gallery_emails() if e.key == "review_decision")
        assert entry.event_keys == frozenset({"instructor_class_approved", "instructor_changes_requested"})

    def it_renders_charge_failed_admin_despite_member_off():
        """M2: member EMAIL default OFF, but the admin email ships — carded, never 'no email'."""
        assert "tab_charge_failed" in _STRUCTURAL_EVENT_KEYS
        listed_keys = {event.key for event, _reason in no_email_events()}
        assert "tab_charge_failed" not in listed_keys
        entry = next(e for e in gallery_emails() if e.key == "charge_failed_admin")
        assert "tab_charge_failed" in entry.event_keys
