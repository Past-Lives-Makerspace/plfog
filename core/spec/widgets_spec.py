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


class _BodyForm(forms.Form):
    from core.widgets import RichBodyEditorWidget

    body = forms.CharField(required=False, widget=RichBodyEditorWidget())


def describe_RichBodyEditorWidget():
    def it_hands_the_textarea_legacy_plain_text_as_the_paragraphs_the_page_renders():
        # Quill seeds from the textarea, so a pre-editor description opens as paragraphs, not one line.
        html = str(_BodyForm(initial={"body": "Wear <closed toe shoes>.\n\nTake it home."})["body"])
        textarea = html.split("</textarea>", 1)[0]
        assert "&lt;p&gt;Wear &amp;lt;closed toe shoes&amp;gt;.&lt;/p&gt;&lt;p&gt;Take it home.&lt;/p&gt;" in textarea

    def it_passes_editor_html_through_untouched():
        # Sanitizing is the form's job; the draft baseline reads format_value and must see the same HTML.
        html = str(_BodyForm(initial={"body": '<p>Hi <a href="http://x.test" rel="noopener">x</a></p>'})["body"])
        textarea = html.split("</textarea>", 1)[0]
        assert (
            "&lt;p&gt;Hi &lt;a href=&quot;http://x.test&quot; rel=&quot;noopener&quot;&gt;x&lt;/a&gt;&lt;/p&gt;"
            in textarea
        )

    def it_formats_a_blank_value_as_none_like_a_textarea():
        from core.widgets import RichBodyEditorWidget

        assert RichBodyEditorWidget().format_value("") is None
        assert RichBodyEditorWidget().format_value(None) is None

    def it_is_the_same_mount_contract_as_the_email_editor():
        html = str(_BodyForm()["body"])
        assert 'class="pl-rte-source"' in html
        assert 'data-rte-for="id_body"' in html
