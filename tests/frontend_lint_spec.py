"""Repo-wide frontend lint: stylesheets close their blocks, wiki buttons name a variant.

Both guards exist because the failure mode is invisible to every other check we run. The
file parses, the page renders, ``manage.py check`` is clean, and the damage only shows at a
particular viewport width or against a particular theme.

1. An unterminated ``@media`` block. PR #342 opened ``@media (max-width: 768px) {`` and never
   closed it. It was the last block in ``hub.css``, so browsers auto-closed it at EOF and
   nothing looked wrong. PR #343 then appended 160 lines of guild-Wiki-tab CSS *after* it,
   and every one of those rules silently became mobile-only: no grid, no chip styling, no
   panel spacing above 768px. PR #344 appended ``.pl-review-reassure`` and lost it the same
   way. Nobody could see it in a diff, because each diff was individually correct.

2. A ``.pl-btn``/``.hub-btn`` with no colour variant. Neither base rule sets ``background``
   or ``color``, so a class list that names no variant falls through to the browser's own
   button chrome — measured ``rgb(239, 239, 239)`` on black text, i.e. white-on-dark. That
   was 34 controls across the wiki, including "+ Add A Photo", "+ Add A Tip" and the one-tap
   "Verify". Scoped to the wiki surface deliberately: bare ``hub-btn`` usages exist elsewhere
   in the repo and are not this lint's business yet.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CSS_DIR = REPO_ROOT / "static" / "css"
TEMPLATES_DIR = REPO_ROOT / "templates"

_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_CLASS_ATTR_RE = re.compile(r"""class=["']([^"']*)["']""")

# --sm and --icon are size/shape modifiers; neither paints anything.
_SIZE_ONLY_MODIFIERS = {"pl-btn--sm", "pl-btn--icon", "hub-btn--sm", "hub-btn--icon"}

WIKI_TEMPLATE_GLOBS = ("hub/wiki_*.html", "hub/partials/_wiki_*.html", "hub/partials/_guild_wiki_tab.html")


def _brace_depth(css: str) -> int:
    """Net ``{`` minus ``}`` with comments removed. Zero means every block was closed."""
    stripped = _COMMENT_RE.sub("", css)
    return stripped.count("{") - stripped.count("}")


def _bare_button_class_lists(html: str) -> list[str]:
    """Class attribute values naming a button base with no colour variant.

    A Django-templated modifier (``pl-btn--{{ style }}``, or one chosen inside an ``{% if %}``)
    counts as named: the call site made a deliberate choice and the lint cannot evaluate it.
    """
    offenders = []
    for class_list in _CLASS_ATTR_RE.findall(html):
        for base in ("pl-btn", "hub-btn"):
            tokens = class_list.split()
            if base not in tokens:
                continue
            if "{{" in class_list or "{%" in class_list:
                continue
            painted = [token for token in tokens if token.startswith(f"{base}--") and token not in _SIZE_ONLY_MODIFIERS]
            if not painted:
                offenders.append(class_list)
    return offenders


def describe_stylesheets():
    def it_closes_every_block_in_every_stylesheet():
        offenders = [
            f"static/css/{path.name}: depth {_brace_depth(path.read_text(encoding='utf-8')):+d}"
            for path in sorted(CSS_DIR.glob("*.css"))
            if _brace_depth(path.read_text(encoding="utf-8")) != 0
        ]
        assert not offenders, (
            "A stylesheet does not return to brace depth 0. An unclosed @media block "
            "silently swallows every rule appended after it, and the browser auto-closes "
            "it at EOF so nothing looks broken until someone adds the next rule:\n  " + "\n  ".join(offenders)
        )

    def it_actually_detects_an_unclosed_block():
        # Self-test: this is the exact shape PR #342 shipped.
        leaky = "@media (max-width: 768px) {\n    .a { display: none; }\n\n.b { color: red; }\n"
        assert _brace_depth(leaky) == 1

    def it_ignores_braces_inside_comments():
        assert _brace_depth("/* a { b } stray } */\n.c { color: red; }\n") == 0


def describe_wiki_buttons():
    def _wiki_templates() -> list[Path]:
        return sorted({path for glob in WIKI_TEMPLATE_GLOBS for path in TEMPLATES_DIR.glob(glob)})

    def it_finds_the_wiki_templates_it_claims_to_lint():
        # Without this the lint passes by scanning nothing — the "green over 0 files" trap.
        assert len(_wiki_templates()) >= 40

    def it_always_names_a_colour_variant():
        offenders = [
            f'templates/{path.relative_to(TEMPLATES_DIR)}: class="{class_list}"'
            for path in _wiki_templates()
            for class_list in _bare_button_class_lists(path.read_text(encoding="utf-8"))
        ]
        assert not offenders, (
            "A .pl-btn/.hub-btn with no colour variant renders as the browser's own button "
            "chrome — white on black text — which on the dark theme is a raw system button. "
            "Add --primary/--secondary/--danger/--success/--ghost:\n  " + "\n  ".join(offenders)
        )

    def it_actually_detects_a_bare_button():
        # Self-test, in both button families.
        assert _bare_button_class_lists('<button class="pl-btn pl-btn--sm">Go</button>') == ["pl-btn pl-btn--sm"]
        assert _bare_button_class_lists('<button class="hub-btn">Go</button>') == ["hub-btn"]

    def it_accepts_a_named_variant():
        assert _bare_button_class_lists('<a class="pl-btn pl-btn--ghost pl-btn--sm">Go</a>') == []

    def it_accepts_a_variant_chosen_by_the_template():
        markup = "<button class=\"pl-btn pl-btn--{{ confirm_button_style|default:'danger' }}\">Go</button>"
        assert _bare_button_class_lists(markup) == []

    def it_reads_single_quoted_class_attributes():
        # A double-quote-only pattern would score a single-quoted bare button as clean.
        assert _bare_button_class_lists("<button class='pl-btn pl-btn--sm'>Go</button>") == ["pl-btn pl-btn--sm"]
        assert _bare_button_class_lists("<button class='pl-btn pl-btn--ghost'>Go</button>") == []
