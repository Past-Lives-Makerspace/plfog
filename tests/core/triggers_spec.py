"""The notification trigger catalogue."""

from core import triggers


def describe_catalogue():
    def it_has_27_triggers():
        # 25, plus spec D's wiki.page_reported and wiki.page_verified.
        assert len(triggers.TRIGGERS) == 27  # incl. the forced refund_failed admin alert

    def it_looks_up_by_key():
        t = triggers.get("class_published")
        assert t.label == "New class published"
        assert t.audience == triggers.Audience.ALL_MEMBERS

    def it_raises_on_unknown_key():
        import pytest

        with pytest.raises(KeyError):
            triggers.get("nope")

    def it_filters_by_audience_for_a_plain_member():
        keys = {t.key for t in triggers.for_member(is_instructor=False, is_staff=False)}
        assert "class_published" in keys
        assert "instructor_class_approved" not in keys
        assert "new_member_joined" not in keys

    def it_excludes_forced_triggers_from_the_toggle_list(monkeypatch):
        # force_email triggers never render a member-facing toggle. No shipping trigger
        # is forced now, so drive the filter with a synthetic forced trigger.
        forced = triggers.Trigger(
            key="forced_probe", label="Probe", description="d", category="Security", force_email=True
        )
        visible = triggers.Trigger(key="visible_probe", label="Visible", description="d", category="Classes")
        monkeypatch.setattr(triggers, "TRIGGERS", [forced, visible])
        keys = {t.key for t in triggers.for_member(is_instructor=False, is_staff=False)}
        assert "forced_probe" not in keys
        assert "visible_probe" in keys

    def it_includes_instructor_triggers_for_instructors():
        keys = {t.key for t in triggers.for_member(is_instructor=True, is_staff=False)}
        assert "instructor_class_approved" in keys

    def it_includes_staff_triggers_for_staff():
        keys = {t.key for t in triggers.for_member(is_instructor=False, is_staff=True)}
        assert "new_member_joined" in keys

    def it_groups_by_category():
        grouped = triggers.by_category(is_instructor=True, is_staff=True)
        assert "Classes" in grouped
        assert any(t.key == "tab_charged" for t in grouped["Billing"])


def describe_column_widths():
    """Every stored key must fit every column that stores it.

    Postgres rejects an over-long value and aborts the surrounding transaction; SQLite
    enforces no declared width at all. The suite runs on SQLite, so without an explicit
    width check this class of bug reaches production invisibly — which is exactly what
    happened to ``equipment.reservation_cancelled_by_manager`` (42 characters into a
    40-character ``Notification.trigger``), taking the manager-cancel path down with it.

    Parametrized over every column rather than the one that broke: four more columns take
    the same registry keys at ``max_length=60``, which is 18 characters from the identical
    failure.
    """

    def _key_columns() -> list[tuple[str, int]]:
        from core.models import (
            DiscordWebhookRoute,
            EventDelivery,
            Notification,
            NotificationPreference,
            NotificationTemplate,
            TransactionalEmailLog,
        )

        pairs = [
            (Notification, "trigger"),
            (TransactionalEmailLog, "trigger_kind"),
            (NotificationPreference, "event_key"),
            (EventDelivery, "event_key"),
            (NotificationTemplate, "event_key"),
            (DiscordWebhookRoute, "event_key"),
        ]
        return [(f"{model.__name__}.{field}", model._meta.get_field(field).max_length) for model, field in pairs]

    def it_covers_every_column_that_stores_a_key():
        # Guard the guard: a new key-storing column must be added to _key_columns above.
        assert len(_key_columns()) == 6

    def it_fits_every_catalogue_trigger_key():
        longest = max(triggers.TRIGGERS, key=lambda t: len(t.key))
        for label, width in _key_columns():
            assert len(longest.key) <= width, f"{longest.key} ({len(longest.key)}) overflows {label} ({width})"

    def it_fits_every_registered_event_key():
        # The registry's keys are the long ones, and emit() writes them to these same
        # columns. This is the assertion that fails on unfixed main, on SQLite.
        from core.events.registry import all_events

        longest = max(all_events(), key=lambda e: len(e.key))
        for label, width in _key_columns():
            assert len(longest.key) <= width, f"{longest.key} ({len(longest.key)}) overflows {label} ({width})"
