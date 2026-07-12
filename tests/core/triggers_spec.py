"""The notification trigger catalogue."""

from core import triggers


def describe_catalogue():
    def it_has_24_configurable_triggers():
        assert len(triggers.TRIGGERS) == 24  # all opt-in; no forced legacy triggers remain

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
