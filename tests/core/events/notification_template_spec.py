"""BDD specs for NotificationTemplate copy versioning + revert (design §2.3)."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User

from core.models import NotificationTemplate, NotificationTemplateVersion

pytestmark = pytest.mark.django_db


def _template(**kw):
    base = dict(event_key="registration_confirmed", channel="email", subject="v1", body_text="b1", body_html="<p>1</p>")
    base.update(kw)
    return NotificationTemplate.objects.create(**base)


def _user():
    return User.objects.create_user(username="copyeditor", email="copy@example.com")


def describe_apply_edit():
    def it_snapshots_the_prior_copy_before_writing():
        tpl = _template()
        editor = _user()
        tpl.apply_edit(subject="v2", body_text="b2", body_html="<p>2</p>", editor=editor)
        tpl.refresh_from_db()
        assert tpl.subject == "v2"
        assert tpl.is_overridden is True
        assert tpl.updated_by == editor
        versions = list(tpl.versions.all())
        assert len(versions) == 1
        assert versions[0].subject == "v1"  # the PRIOR copy is what's snapshotted

    def it_marks_the_row_overridden():
        tpl = _template(is_overridden=False)
        tpl.apply_edit(subject="x", body_text="y", body_html="", editor=None)
        tpl.refresh_from_db()
        assert tpl.is_overridden is True

    def it_records_a_null_editor_for_a_system_edit():
        tpl = _template()
        tpl.apply_edit(subject="x", body_text="y", body_html="", editor=None)
        tpl.refresh_from_db()
        assert tpl.updated_by is None

    def it_keeps_each_edit_as_a_separate_version():
        tpl = _template()
        tpl.apply_edit(subject="v2", body_text="b2", body_html="", editor=None)
        tpl.apply_edit(subject="v3", body_text="b3", body_html="", editor=None)
        snapshots = [v.subject for v in tpl.versions.order_by("created_at")]
        assert snapshots == ["v1", "v2"]


def describe_revert_to():
    def it_restores_the_selected_versions_copy():
        tpl = _template()
        tpl.apply_edit(subject="v2", body_text="b2", body_html="<p>2</p>", editor=None)
        first_version = tpl.versions.get(subject="v1")
        tpl.revert_to(first_version, editor=None)
        tpl.refresh_from_db()
        assert tpl.subject == "v1"
        assert tpl.body_text == "b1"

    def it_snapshots_the_current_copy_so_the_revert_is_itself_undoable():
        tpl = _template()
        tpl.apply_edit(subject="v2", body_text="b2", body_html="", editor=None)
        v1 = tpl.versions.get(subject="v1")
        tpl.revert_to(v1, editor=None)
        # A revert snapshots "v2" before restoring "v1", so v2 is now in history.
        assert tpl.versions.filter(subject="v2").exists()

    def it_rejects_a_version_from_a_different_template():
        tpl_a = _template()
        tpl_b = _template(event_key="class_reminder")
        other_version = NotificationTemplateVersion.objects.create(template=tpl_b, subject="other")
        with pytest.raises(ValueError):
            tpl_a.revert_to(other_version, editor=None)


def describe_snapshot_version():
    def it_captures_the_current_subject_and_bodies():
        tpl = _template(subject="cur", body_text="curtext", body_html="<i>cur</i>")
        version = tpl.snapshot_version()
        assert version.subject == "cur"
        assert version.body_text == "curtext"
        assert version.body_html == "<i>cur</i>"
        assert version.template == tpl
