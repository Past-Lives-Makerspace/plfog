"""Static guard: the public top bar keeps its safe-area inset at every width.

``env(safe-area-inset-top)`` resolves to 0 in desktop Chrome and in Playwright, and
there is no iOS Simulator in CI, so the notch inset cannot be asserted from a
browser at all — it is asserted by reading the stylesheet.

Two shapes are guarded, and both are one "tidy-up" away from being lost:

1. **The fallback pair.** ``height: calc(var(--topbar-height) + env(…))`` on its own
   is not a safe declaration — a browser that cannot parse ``env()`` drops the
   *whole* declaration rather than the term, leaving the bar with no height at all.
   The plain declaration therefore comes first and the ``env()`` one immediately
   after, and this spec fails if either half goes missing or they swap order.

2. **The phone-width override.** ``@media (max-width: 880px)`` re-declares the bar's
   ``padding`` shorthand. It has the same specificity as the base rule and sits
   later in the file, so a natural-looking ``padding: 0 0.75rem`` silently wins at
   every phone width and deletes the inset on exactly the devices the breakpoint
   exists for. Every ``padding``/``height`` declaration on the bar is therefore
   checked, not just the base rule's — a reader scoped to the base rule would pass
   green over precisely that bug.

The whole-file substring check the naive version of this spec would use is vacuous:
``env(safe-area-inset-top, 0px)`` already appears a dozen times in ``hub.css`` for
other components, so it passes before any of this exists.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HUB_CSS = REPO_ROOT / "static" / "css" / "hub.css"

INSET = "env(safe-area-inset-top, 0px)"

_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_RULE_RE = re.compile(r"(?P<selectors>[^{}]+)\{(?P<body>[^{}]*)\}")
# The bar element itself and its modifiers — never its children, which do not
# carry the bar's own box.
_BAR_SELECTOR_RE = re.compile(r"^\.pl-public-topbar(--[A-Za-z0-9-]+)?$")
# `(?<![\w-])` keeps `line-height`, `min-height` and `max-height` out of it.
_BOX_DECL_RE = re.compile(r"(?<![\w-])(height|padding|padding-top)\s*:\s*([^;{}]+);")
_TOPBAR_HEIGHT_RE = re.compile(r"--topbar-height\s*:\s*([^;]+);")


def _bar_rules(css: str) -> list[tuple[str, str]]:
    """``(selector, body)`` for every rule whose subject is the bar element itself."""
    rules = []
    for match in _RULE_RE.finditer(_COMMENT_RE.sub("", css)):
        for selector in (part.strip() for part in match.group("selectors").split(",")):
            if _BAR_SELECTOR_RE.match(selector):
                rules.append((selector, match.group("body")))
                break
    return rules


def _box_declarations(body: str) -> list[tuple[str, str]]:
    """The ``height``/``padding``/``padding-top`` declarations of one rule, in source order."""
    return [(match.group(1), match.group(2).strip()) for match in _BOX_DECL_RE.finditer(body)]


def _media_block(css: str, prelude: str) -> str:
    """The inner text of ``prelude { … }``, brace-matched so nested rules come with it."""
    stripped = _COMMENT_RE.sub("", css)
    open_brace = stripped.index("{", stripped.index(prelude))
    depth = 0
    for index in range(open_brace, len(stripped)):
        if stripped[index] == "{":
            depth += 1
        elif stripped[index] == "}":
            depth -= 1
            if depth == 0:
                return stripped[open_brace + 1 : index]
    raise AssertionError(f"`{prelude}` is never closed")


def _inset_offenders(css: str) -> list[str]:
    """Every bar rule whose box declarations are not a plain-then-``env()`` pair."""
    offenders = []
    for selector, body in _bar_rules(css):
        by_property: dict[str, list[str]] = {}
        for prop, value in _box_declarations(body):
            by_property.setdefault(prop, []).append(value)
        for prop, values in by_property.items():
            first_inset = next((index for index, value in enumerate(values) if INSET in value), None)
            first_plain = next((index for index, value in enumerate(values) if "env(" not in value), None)
            where = f"{selector} {{ {prop} }}"
            if first_inset is None:
                offenders.append(f"{where}: no `{INSET}` term — the notch inset is gone at this width")
            elif first_plain is None:
                offenders.append(f"{where}: an env() declaration with no plain fallback before it")
            elif first_plain > first_inset:
                offenders.append(f"{where}: the plain fallback must be written before the env() declaration")
    return offenders


def describe_public_topbar_safe_area():
    def it_keeps_the_inset_on_every_declaration_of_the_bars_box():
        offenders = _inset_offenders(HUB_CSS.read_text(encoding="utf-8"))
        assert not offenders, (
            "A `.pl-public-topbar` box declaration lost its safe-area fallback pair. Write it as "
            "the plain declaration first and the env() one immediately after — an env() a browser "
            "cannot parse invalidates the whole declaration, not just the term:\n  " + "\n  ".join(offenders)
        )

    def it_finds_the_rules_it_claims_to_check():
        # "Green over nothing": if the regex stops matching, every assertion above
        # passes over an empty list and the guard is silently dead.
        selectors = [selector for selector, _ in _bar_rules(HUB_CSS.read_text(encoding="utf-8"))]
        assert selectors.count(".pl-public-topbar") >= 2, (
            f"expected the base rule and at least one breakpoint override, found {selectors}"
        )
        assert ".pl-public-topbar--minimal" in selectors, selectors

    def it_checks_the_phone_width_override_specifically():
        # The base rule can be perfect and the bar still lose its inset on every
        # phone, because this block re-declares the padding shorthand later in the
        # file at equal specificity. This is the failure the ticket was about.
        inner = _media_block(HUB_CSS.read_text(encoding="utf-8"), "@media (max-width: 880px)")
        declarations = [decl for _, body in _bar_rules(inner) for decl in _box_declarations(body)]
        assert declarations, "the 880px block no longer re-declares the bar's box — retarget this assertion"
        assert any(prop == "padding" and INSET in value for prop, value in declarations), declarations
        assert any(prop == "padding" and "env(" not in value for prop, value in declarations), declarations

    def describe_the_checker_itself():
        good = (
            ".pl-public-topbar {\n"
            "    height: var(--topbar-height);\n"
            "    height: calc(var(--topbar-height) + env(safe-area-inset-top, 0px));\n"
            "    padding: 0 1.5rem;\n"
            "    padding: env(safe-area-inset-top, 0px) 1.5rem 0;\n"
            "}\n"
        )

        def it_accepts_the_fallback_pair():
            assert _inset_offenders(good) == []

        def it_actually_detects_a_breakpoint_that_dropped_the_inset():
            # The exact shape the 880px block takes when someone "tidies" it.
            leaky = good + "@media (max-width: 880px) {\n    .pl-public-topbar { padding: 0 0.75rem; }\n}\n"
            (offender,) = _inset_offenders(leaky)
            assert "padding" in offender

        def it_actually_detects_an_env_declaration_with_no_fallback():
            lonely = ".pl-public-topbar {\n    height: calc(var(--topbar-height) + env(safe-area-inset-top, 0px));\n}\n"
            (offender,) = _inset_offenders(lonely)
            assert "fallback" in offender

        def it_actually_detects_a_fallback_written_after_the_env_declaration():
            swapped = (
                ".pl-public-topbar {\n    padding: env(safe-area-inset-top, 0px) 1.5rem 0;\n    padding: 0 1.5rem;\n}\n"
            )
            (offender,) = _inset_offenders(swapped)
            assert "before" in offender

        def it_ignores_the_bars_child_elements():
            assert _inset_offenders(".pl-public-topbar__brand { padding: 0 8px; }") == []

        def it_ignores_properties_that_merely_end_in_height():
            assert _inset_offenders(good + ".pl-public-topbar { line-height: 1.6; max-height: 90px; }") == []

        def it_reads_only_the_rules_nested_inside_the_media_block():
            # A block reader that ran to the first `}` would stop mid-rule; one that
            # ran to the last would swallow `.b` and score the file on the wrong rules.
            css = "@media (max-width: 880px) {\n    .a { color: red; }\n}\n.b { color: blue; }\n"
            inner = _media_block(css, "@media (max-width: 880px)")
            assert ".a { color: red; }" in inner
            assert ".b" not in inner


def describe_topbar_height_token():
    def it_is_defined_once_and_still_56px():
        # The bar's height is `calc(var(--topbar-height) + env(…))`; every width the
        # e2e spec measures the brand against assumes this value.
        values = _TOPBAR_HEIGHT_RE.findall(_COMMENT_RE.sub("", HUB_CSS.read_text(encoding="utf-8")))
        assert values == ["56px"], f"--topbar-height changed or is defined more than once: {values}"
