"""The notification trigger catalogue."""

from core import triggers


def describe_catalogue():
    def it_has_25_triggers():
        assert len(triggers.TRIGGERS) == 25  # incl. the forced refund_failed admin alert

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
    """Every stored key must fit the columns that store it.

    Postgres rejects an over-long value and aborts the surrounding transaction; SQLite
    silently truncates nothing and stores it whole. The suite runs on SQLite, so without
    an explicit width check this class of bug reaches production invisibly — which is
    exactly what happened to ``equipment.reservation_cancelled_by_manager`` (42 characters
    into a 40-character column), taking the manager-cancel path down with it.
    """

    def _trigger_column_width() -> int:
        from core.models import Notification

        return Notification._meta.get_field("trigger").max_length

    def it_fits_every_catalogue_key_in_the_notification_column():
        width = _trigger_column_width()
        too_long = {t.key: len(t.key) for t in triggers.TRIGGERS if len(t.key) > width}
        assert too_long == {}, f"trigger keys longer than the {width}-char column: {too_long}"

    def it_fits_every_registered_event_key_in_the_notification_column():
        # The event registry writes the same column through emit(); its keys are longer
        # than the catalogue's and are what actually overflowed.
        from core.events.registry import all_events

        width = _trigger_column_width()
        too_long = {e.key: len(e.key) for e in all_events() if len(e.key) > width}
        assert too_long == {}, f"event keys longer than the {width}-char column: {too_long}"

    def it_fits_every_registered_event_key_in_the_email_log_column():
        from core.models import TransactionalEmailLog
        from core.events.registry import all_events

        width = TransactionalEmailLog._meta.get_field("trigger_kind").max_length
        too_long = {e.key: len(e.key) for e in all_events() if len(e.key) > width}
        assert too_long == {}, f"event keys longer than the {width}-char column: {too_long}"
