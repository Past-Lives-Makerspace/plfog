"""BDD specs for the constrained, SAFE merge-field renderer (design §2.3)."""

from __future__ import annotations

from django.utils.safestring import mark_safe

from core.events.rendering import (
    placeholders_in,
    render_copy,
    render_html,
    render_text,
    unknown_placeholders,
    validate_copy,
)


def describe_render_text():
    def it_substitutes_documented_placeholders():
        out = render_text("Hi {{ name }}, welcome to {{ place }}.", {"name": "Jo", "place": "PLM"})
        assert out == "Hi Jo, welcome to PLM."

    def it_tolerates_surrounding_whitespace_in_the_braces():
        assert render_text("{{name}} and {{   name   }}", {"name": "Jo"}) == "Jo and Jo"

    def describe_with_an_unknown_placeholder():
        def it_leaves_a_visible_missing_marker():
            assert render_text("Hi {{ nope }}.", {"name": "Jo"}) == "Hi [missing: nope]."

    def describe_with_a_template_tag_or_dotted_path():
        def it_does_not_execute_django_template_code():
            # {% ... %} tags and dotted access are NOT placeholders — left verbatim.
            tpl = "{% load x %}{{ user.is_superuser }}{% if 1 %}X{% endif %}"
            out = render_text(tpl, {"user": object()})
            assert out == tpl  # nothing interpreted, nothing executed

    def it_never_raises_on_garbage_input():
        assert render_text("{{ }} {{{ }}} {{1bad}}", {}) == "{{ }} {{{ }}} {{1bad}}"


def describe_render_html():
    def it_autoescapes_substituted_values():
        out = str(render_html("<p>{{ body }}</p>", {"body": "<script>alert(1)</script>"}))
        assert "<script>" not in out
        assert "&lt;script&gt;" in out
        assert out.startswith("<p>") and out.endswith("</p>")  # literal markup preserved

    def it_escapes_ampersands_and_quotes_in_values():
        out = str(render_html("{{ v }}", {"v": 'a & "b"'}))
        assert "&amp;" in out
        assert "&quot;" in out or "&#x27;" in out or '"' not in out

    def it_escapes_the_missing_marker_too():
        out = str(render_html("<p>{{ gone }}</p>", {}))
        assert "[missing: gone]" in out

    def describe_with_a_safestring_value():
        def it_passes_app_built_markup_through_unescaped():
            # A SafeString value is trusted app-built markup (e.g. the allocation chart)
            # and is inserted verbatim — the whole point of the opt-in.
            chart = mark_safe('<table><tr><td width="100%">bar</td></tr></table>')
            out = str(render_html("<div>{{ chart }}</div>", {"chart": chart}))
            assert '<table><tr><td width="100%">bar</td></tr></table>' in out

        def it_still_escapes_plain_string_values_alongside_a_safestring():
            # Marking one value safe must not weaken escaping for the others.
            out = str(
                render_html(
                    "{{ chart }}{{ evil }}",
                    {"chart": mark_safe("<b>ok</b>"), "evil": "<script>x</script>"},
                )
            )
            assert "<b>ok</b>" in out
            assert "<script>" not in out
            assert "&lt;script&gt;" in out


def describe_render_copy():
    def it_renders_subject_text_and_html_together():
        rendered = render_copy(
            subject="Hi {{ name }}",
            body_text="Plain {{ name }}",
            body_html="<b>{{ name }}</b>",
            context={"name": "Jo"},
        )
        assert rendered.subject == "Hi Jo"
        assert rendered.body_text == "Plain Jo"
        assert rendered.body_html == "<b>Jo</b>"

    def it_returns_empty_html_when_no_html_template():
        rendered = render_copy(subject="s", body_text="t", body_html="", context={})
        assert rendered.body_html == ""


def describe_placeholders_in():
    def it_reports_only_bare_identifier_placeholders():
        assert placeholders_in("{{ a }} {{ b }} {{ c.d }} {% x %}") == ["a", "b"]


def describe_unknown_placeholders():
    def it_flags_names_outside_the_allowed_set_deduped():
        assert unknown_placeholders("{{ a }} {{ x }} {{ x }} {{ y }}", ("a",)) == ["x", "y"]


def describe_validate_copy():
    def it_returns_empty_when_only_documented_fields_are_used():
        assert validate_copy(subject="{{ a }}", body_text="{{ b }}", body_html="{{ a }}", allowed=("a", "b")) == []

    def it_collects_undocumented_fields_across_all_three_fields():
        bad = validate_copy(
            subject="{{ a }}",
            body_text="{{ bogus }}",
            body_html="{{ alsobad }}",
            allowed=("a",),
        )
        assert bad == ["bogus", "alsobad"]
