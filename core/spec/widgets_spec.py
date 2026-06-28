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
        assert 'data-rte-for="id_body"' in html
        assert 'id="pl-rte-mount-id_body"' in html
        # The init script binds to this field's own id, not a global selector.
        assert '"id_body"' in html

    def it_seeds_the_textarea_with_the_bound_value():
        form = _RTEForm(initial={"body": "<p>Hello</p>"})
        html = str(form["body"])
        assert "&lt;p&gt;Hello&lt;/p&gt;" in html  # value is HTML-escaped inside the textarea

    def it_renders_two_instances_with_distinct_ids():
        form = _RTEForm()
        body_html = str(form["body"])
        note_html = str(form["note"])
        assert "pl-rte-mount-id_body" in body_html
        assert "pl-rte-mount-id_note" in note_html
        assert "pl-rte-mount-id_note" not in body_html
