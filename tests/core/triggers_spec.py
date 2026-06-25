"""The notification trigger catalogue."""

from core import triggers


def describe_catalogue():
    def it_has_24_configurable_triggers_plus_forced():
        assert len(triggers.TRIGGERS) == 25  # 24 opt-in + new_login (forced)

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
        assert "new_login" not in keys  # forced triggers never show as a toggle

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
