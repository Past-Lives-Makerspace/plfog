"""BDD specs for core.widgets.RichTextEditorWidget."""

from __future__ import annotations

from django import forms

from core.widgets import RichTextEditorWidget


class _RTEForm(forms.Form):
    body = forms.CharField(widget=RichTextEditorWidget())
    note = forms.CharField(widget=RichTextEditorWidget())


def describe_RichTextEditorWidget():
    def it_renders_the_named_textarea_and_a_mount_keyed_to_the_field_id():
        html = str(_RTEForm()["body"])
        assert 'name="body"' in html
        assert 'id="id_body"' in html
        assert 'class="pl-rte-source"' in html
        assert 'class="pl-rte"' in html
        # The init script binds to this field's own id, not a global selector.
        assert 'data-rte-for="id_body"' in html

    def it_gives_the_mount_no_id_for_htmx_to_settle():
        # An id on the mount let a boosted Save onto the same page restore its server class
        # over Quill's, unframing the editor (tests/e2e/boosted_navigation_spec.py).
        html = str(_RTEForm()["body"])
        mount = html.split('class="pl-rte"', 1)[1].split(">", 1)[0]
        assert "id=" not in mount

    def it_seeds_the_textarea_with_the_bound_value():
        form = _RTEForm(initial={"body": "<p>Hello</p>"})
        html = str(form["body"])
        assert "&lt;p&gt;Hello&lt;/p&gt;" in html  # value is HTML-escaped inside the textarea

    def it_renders_two_instances_with_distinct_ids():
        form = _RTEForm()
        body_html = str(form["body"])
        note_html = str(form["note"])
        assert 'data-rte-for="id_body"' in body_html
        assert 'data-rte-for="id_note"' in note_html
        assert 'data-rte-for="id_note"' not in body_html
