"""Repo-wide template lint: Django ``{# #}`` comments are SINGLE-LINE only.

A ``{#`` whose ``#}`` is on a later line is not a comment — Django renders the
whole thing verbatim as visible page text. This has shipped to real pages more
than once (FRONTEND.md Rule 17), most recently inside the toggle component
itself, where the axe gate couldn't catch it because the leaked text landed
inside a <label> and became the control's accessible name. Multi-line notes
belong in ``{% comment %}…{% endcomment %}``.
"""

from __future__ import annotations

from pathlib import Path

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"


def _unclosed_comment_lines(text: str) -> list[int]:
    """Line numbers (1-based) where a ``{#`` opens with no ``#}`` on the same line."""
    offenders = []
    for lineno, line in enumerate(text.splitlines(), 1):
        rest = line
        while "{#" in rest:
            after = rest[rest.index("{#") + 2 :]
            if "#}" not in after:
                offenders.append(lineno)
                break
            rest = after[after.index("#}") + 2 :]
    return offenders


def describe_template_comments():
    def it_never_leaves_a_hash_comment_open_across_lines():
        offenders = [
            f"templates/{path.relative_to(TEMPLATES_DIR)}:{lineno}"
            for path in sorted(TEMPLATES_DIR.rglob("*.html"))
            for lineno in _unclosed_comment_lines(path.read_text(encoding="utf-8"))
        ]
        assert not offenders, (
            "Multi-line {# #} comments render as VISIBLE page text. "
            "Use {% comment %}…{% endcomment %} instead:\n  " + "\n  ".join(offenders)
        )

    def it_actually_detects_an_unclosed_comment():
        # Self-test so a refactor can't quietly neuter the lint.
        leaky = "<p>hi</p>\n{# spans\n   two lines #}\n{# fine #}"
        assert _unclosed_comment_lines(leaky) == [2]
