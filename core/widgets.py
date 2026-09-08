"""Reusable form widgets shared across plfog apps."""

from __future__ import annotations

from typing import Any

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
    runs (the include also loads the shared ``rich-editor-init.js`` that initializes every
    mount, including formset rows cloned client-side). Server-side sanitization is the
    form's job — the editor output is never trusted (see
    :func:`core.html_sanitize.sanitize_rich_html`).
    """

    template_name = "widgets/rich_text_editor.html"


class PageContentEditorWidget(RichTextEditorWidget):
    """Quill editor for the dual-mode help/org page fields (/help/edit/).

    Same hidden-textarea + ``.pl-rte`` mount contract as :class:`RichTextEditorWidget`,
    with two differences:

    * **Server-seeded mount** (``data-rte-seed="server"``): the stored value — legacy
      Markdown or already-saved rich HTML — is rendered server-side via
      :func:`membership.markdown.render_page_content` into the mount div, so a Markdown
      body opens as formatted rich text instead of raw source. Saving writes Quill's
      HTML back through the form's sanitizing ``clean_*`` — a one-way, explicit
      Markdown→HTML conversion (Markdown is not what we're moving forward with).
    * **Page toolbar** (``data-rte-toolbar="page"``): adds strike + blockquote over the
      email editor's set. Deliberately NO image button — help screenshots come from the
      committed ``/static/help/`` pipeline, which keeps the sanitizer tight.

    The toolbar is a settable attribute rather than a hardcoded string: the member wiki
    needs a third variant (``"wiki"``, the page set plus an image button whose uploads land
    under the one prefix its sanitizer profile trusts). Both existing callers omit the
    argument and keep ``"page"`` exactly as before.
    """

    markdown_profile = "help"
    toolbar = "page"
    upload_url = ""

    def __init__(
        self,
        attrs: dict[str, Any] | None = None,
        *,
        markdown_profile: str = "help",
        toolbar: str = "page",
        upload_url: str = "",
    ) -> None:
        super().__init__(attrs)
        self.markdown_profile = markdown_profile
        self.toolbar = toolbar
        # Only the wiki toolbar has an image button, and only an existing page has somewhere
        # to put the file — so this is set per form instance, where the slug is known.
        self.upload_url = upload_url

    def get_context(self, name: str, value: Any, attrs: dict[str, Any] | None) -> dict[str, Any]:
        """Add the server-rendered display HTML and toolbar variant to the widget context."""
        # Lazy: core must not import membership at module level (membership imports core).
        from membership.markdown import render_page_content, render_wiki_content

        context = super().get_context(name, value, attrs)
        raw = str(value or "")
        # The wiki body renders through its own profile, or a Quill image the member just
        # inserted would be stripped out of the mount the moment the editor reopened.
        seed = (
            render_wiki_content(raw)
            if self.markdown_profile == "wiki"
            else render_page_content(raw, profile=self.markdown_profile)
        )
        context["widget"]["seed_html"] = seed
        context["widget"]["toolbar"] = self.toolbar
        context["widget"]["upload_url"] = self.upload_url
        return context
