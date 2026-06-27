"""Reusable form widgets shared across plfog apps."""

from __future__ import annotations

from django import forms


class RichTextEditorWidget(forms.Textarea):
    """A Quill-backed rich-text editor rendered over a hidden textarea.

    The real (named) textarea keeps the field's ``name``/``id`` — so normal form
    submission carries the HTML — but is visually hidden; a sibling ``.pl-rte`` div is
    the Quill mount. The widget's inline init script seeds Quill from the textarea and
    syncs Quill's HTML back to it on every edit, keyed to the field's auto id so several
    editors can coexist on one page.

    Asset loading is explicit: include ``_components/rich_editor_assets.html`` once near
    the top of the using-template so ``window.Quill`` is present before the init script
    runs. Server-side sanitization is the form's job — the editor output is never trusted
    (see :func:`core.html_sanitize.sanitize_rich_html`).
    """

    template_name = "widgets/rich_text_editor.html"
