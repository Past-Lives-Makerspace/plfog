"""Static guard: the public top bar keeps its safe-area inset at every width.

``env(safe-area-inset-top)`` resolves to 0 in desktop Chrome and in Playwright, and
there is no iOS Simulator in CI, so the notch inset cannot be asserted from a
browser at all — it is asserted by reading the stylesheet.

An engine that does not understand ``env()`` degrades in one of two ways, and which
one you get depends on whether the declaration's value also contains ``var()``:

* **No ``var()``.** The value is grammar-checked at parse time, ``env()`` is an
  unknown function, and the whole declaration is dropped. An earlier plain
  declaration of the same property then wins. That makes *plain first, ``env()``
  immediately after* a genuine fallback pair, and it is how the bar's ``padding``
  carries the inset.
* **Contains ``var()``.** Per CSS Custom Properties Level 1 the value is stored as
  an unvalidated token stream and checked only *after* substitution. The
  declaration therefore wins the cascade and only then turns out to be invalid at
  computed-value time, which computes the property to its inherited value if it is
  inherited and otherwise to its **initial** value. For ``height`` and ``top`` that
  is ``auto``. The earlier declaration is *not* resurrected. A plain/``env()`` pair
  is useless for such a value, and writing one is actively worse than shipping no
  inset at all — which is why the bar's ``height`` inset lives in an ``@supports``
  block, whose condition *is* parse-tested, and why the toast expresses its inset
  as a separate var-free ``margin-top`` rather than folding ``env()`` into its
  ``top: calc(var(…) …)``.

Four shapes are guarded here, and each is one "tidy-up" away from being lost:

1. **The fallback pair** on every var-free box declaration of the bar.
2. **The ``@supports`` block** that carries the ``height`` inset, including the
   fact that nothing later in the file re-declares ``height`` at equal specificity
   and quietly wins over it.
3. **The phone-width override.** ``@media (max-width: 880px)`` re-declares the
   bar's ``padding`` shorthand. It has the same specificity as the base rule and
   sits later in the file, so a natural-looking ``padding: 0 0.75rem`` silently
   wins at every phone width and deletes the inset on exactly the devices the
   breakpoint exists for. Every ``padding``/``height`` declaration on the bar is
   therefore checked, not just the base rule's.
4. **No ``var()`` and ``env()`` in one value outside ``@supports``**, on the bar or
   on the toast container. That is the defect shape itself, stated directly: the
   toast once shipped ``top: calc(var(--topbar-height, 56px) + env(…) + 0.75rem)``
   with a plain ``top`` above it as a "fallback", which on an affected engine put
   a ``position: fixed`` confirmation below the fold and showed the member nothing.

The whole-file substring check the naive version of this spec would use is vacuous:
``env(safe-area-inset-top, 0px)`` already appears a dozen times in ``hub.css`` for
other components, so it passes before any of this exists.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HUB_CSS = REPO_ROOT / "static" / "css" / "hub.css"
TOAST_HTML = REPO_ROOT / "templates" / "components" / "toast.html"

INSET = "env(safe-area-inset-top, 0px)"

_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_RULE_RE = re.compile(r"(?P<selectors>[^{}]+)\{(?P<body>[^{}]*)\}")
# The bar element itself and its modifiers — never its children, which do not
# carry the bar's own box.
_BAR_SELECTOR_RE = re.compile(r"^\.pl-public-topbar(--[A-Za-z0-9-]+)?$")
# Everything positioned against the bar's inset: the bar, and the toast container
# that offsets itself by --topbar-height. `.pl-topbar` (the authenticated bar) is
# deliberately absent — it has the same var()+env() shape and is its own ticket.
_INSET_SELECTOR_RE = re.compile(r"^\.(pl-public-topbar|plt-container)(--[A-Za-z0-9-]+)?$")
# `(?<![\w-])` keeps `line-height`, `min-height` and `max-height` out of it.
# The logical-property spellings are in here on purpose. `padding-block-start: 0`
# is exactly the tidy-up a modern-CSS reviewer would reach for, and it would
# otherwise slip past this guard and delete the inset silently.
_BOX_DECL_RE = re.compile(
    r"(?<![\w-])(height|block-size|padding|padding-top|padding-inline|padding-block|padding-block-start)"
    r"\s*:\s*([^;{}]+);"
)
_ANY_DECL_RE = re.compile(r"(?<![\w-])([-a-zA-Z]+)\s*:\s*([^;{}]+);")
_TOPBAR_HEIGHT_RE = re.compile(r"--topbar-height\s*:\s*([^;]+);")
_STYLE_RE = re.compile(r"<style>(.*?)</style>", re.DOTALL)
# An `@supports` condition that actually feature-tests env(), which is the only
# kind that licenses a var()+env() value inside it.
_SUPPORTS_ENV_RE = re.compile(r"^@supports\s*\(\s*[-a-zA-Z]+\s*:\s*env\(safe-area-inset-top\)\s*\)$")

_MIXED_MESSAGE = (
    "A value mixing var() and env() outside @supports is not a fallback, it is a trap: a "
    "declaration containing var() is validated only after substitution, so on an engine "
    "without env() it wins the cascade and then computes to its initial value (`auto` for "
    "height and top). Split the inset onto its own var-free property, or move the whole "
    "declaration into `@supports (height: env(safe-area-inset-top))`:\n  "
)


def _rules(css: str, selector_re: re.Pattern[str]) -> list[tuple[str, str]]:
    """``(selector, body)`` for every rule whose subject matches ``selector_re``."""
    matched = []
    for match in _RULE_RE.finditer(_COMMENT_RE.sub("", css)):
        for selector in (part.strip() for part in match.group("selectors").split(",")):
            if selector_re.match(selector):
                matched.append((selector, match.group("body")))
                break
    return matched


def _bar_rules(css: str) -> list[tuple[str, str]]:
    """``(selector, body)`` for every rule whose subject is the bar element itself."""
    return _rules(css, _BAR_SELECTOR_RE)


def _box_declarations(body: str) -> list[tuple[str, str]]:
    """The ``height``/``padding``/``padding-top`` declarations of one rule, in source order."""
    return [(match.group(1), match.group(2).strip()) for match in _BOX_DECL_RE.finditer(body)]


def _style_css(html: str) -> str:
    """The CSS of every ``<style>`` element in a template, concatenated."""
    blocks = _STYLE_RE.findall(html)
    if not blocks:
        raise AssertionError("no <style> block found — retarget this reader")
    return "\n".join(blocks)


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


def _supports_spans(css: str) -> list[tuple[str, str, int, int]]:
    """``(condition, inner css, start, end)`` for every ``@supports`` block.

    Offsets are into ``css`` with comments already stripped, which is what every
    caller here reads, so a block's position can be compared against the rules
    that come after it.
    """
    spans = []
    cursor = 0
    while True:
        start = css.find("@supports", cursor)
        if start == -1:
            return spans
        open_brace = css.index("{", start)
        depth = 0
        for index in range(open_brace, len(css)):
            if css[index] == "{":
                depth += 1
            elif css[index] == "}":
                depth -= 1
                if depth == 0:
                    break
        else:
            raise AssertionError("`@supports` is never closed")
        spans.append((css[start:open_brace].strip(), css[open_brace + 1 : index], start, index + 1))
        cursor = index + 1


def _outside_supports(css: str) -> str:
    """``css`` with every ``@supports`` block cut out, comments already stripped."""
    stripped = _COMMENT_RE.sub("", css)
    kept, cursor = [], 0
    for _, _, start, end in _supports_spans(stripped):
        kept.append(stripped[cursor:start])
        cursor = end
    kept.append(stripped[cursor:])
    return "".join(kept)


def _supports_guarded(css: str) -> set[str]:
    """Box properties whose inset is supplied from inside an ``env()`` feature test."""
    guarded = set()
    for condition, inner, _, _ in _supports_spans(_COMMENT_RE.sub("", css)):
        if not _SUPPORTS_ENV_RE.match(condition):
            continue
        for _, body in _bar_rules(inner):
            for prop, value in _box_declarations(body):
                if INSET in value:
                    guarded.add(prop)
    return guarded


def _inset_offenders(css: str) -> list[str]:
    """Every bar box declaration that would lose the inset, or lose its fallback."""
    guarded = _supports_guarded(css)
    offenders = []
    for selector, body in _bar_rules(_outside_supports(css)):
        by_property: dict[str, list[str]] = {}
        for prop, value in _box_declarations(body):
            by_property.setdefault(prop, []).append(value)
        for prop, values in by_property.items():
            first_inset = next((index for index, value in enumerate(values) if INSET in value), None)
            first_plain = next((index for index, value in enumerate(values) if "env(" not in value), None)
            where = f"{selector} {{ {prop} }}"
            if first_inset is None and first_plain is not None and prop in guarded:
                # The plain value stands here and an @supports block adds the inset
                # on top of it. That is the only safe shape for a value holding var().
                continue
            if first_inset is None:
                offenders.append(f"{where}: no `{INSET}` term — the notch inset is gone at this width")
            elif first_plain is None:
                offenders.append(f"{where}: an env() declaration with no plain fallback before it")
            elif first_plain > first_inset:
                offenders.append(f"{where}: the plain fallback must be written before the env() declaration")
    return offenders


def _var_and_env_offenders(css: str, source: str) -> list[str]:
    """Every declaration mixing ``var()`` and ``env()`` in one value outside ``@supports``."""
    offenders = []
    for selector, body in _rules(_outside_supports(css), _INSET_SELECTOR_RE):
        for match in _ANY_DECL_RE.finditer(body):
            value = match.group(2).strip()
            if "var(" in value and "env(" in value:
                offenders.append(f"{source}: {selector} {{ {match.group(1)}: {value} }}")
    return offenders


def describe_public_topbar_safe_area():
    def it_keeps_the_inset_on_every_declaration_of_the_bars_box():
        offenders = _inset_offenders(HUB_CSS.read_text(encoding="utf-8"))
        assert not offenders, (
            "A `.pl-public-topbar` box declaration lost its safe-area inset. A var-free value "
            "carries it as the plain declaration first and the env() one immediately after; a "
            "value containing var() carries it from an @supports block instead:\n  " + "\n  ".join(offenders)
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
            "    padding: 0 1.5rem;\n"
            "    padding: env(safe-area-inset-top, 0px) 1.5rem 0;\n"
            "}\n"
            "@supports (height: env(safe-area-inset-top)) {\n"
            "    .pl-public-topbar {\n"
            "        height: calc(var(--topbar-height) + env(safe-area-inset-top, 0px));\n"
            "    }\n"
            "}\n"
        )

        def it_accepts_a_var_free_fallback_pair_and_a_supports_guarded_height():
            assert _inset_offenders(good) == []

        def it_actually_detects_a_breakpoint_that_dropped_the_inset():
            # The exact shape the 880px block takes when someone "tidies" it.
            leaky = good + "@media (max-width: 880px) {\n    .pl-public-topbar { padding: 0 0.75rem; }\n}\n"
            (offender,) = _inset_offenders(leaky)
            assert "padding" in offender

        def it_actually_detects_an_env_declaration_with_no_fallback():
            lonely = ".pl-public-topbar {\n    padding: env(safe-area-inset-top, 0px) 1.5rem 0;\n}\n"
            (offender,) = _inset_offenders(lonely)
            assert "fallback" in offender

        def it_actually_detects_a_fallback_written_after_the_env_declaration():
            swapped = (
                ".pl-public-topbar {\n    padding: env(safe-area-inset-top, 0px) 1.5rem 0;\n    padding: 0 1.5rem;\n}\n"
            )
            (offender,) = _inset_offenders(swapped)
            assert "before" in offender

        def it_still_requires_an_inset_for_a_height_no_supports_block_guards():
            # Deleting the @supports block must not silently become "height needs no inset".
            unguarded = good[: good.index("@supports")]
            (offender,) = _inset_offenders(unguarded)
            assert "height" in offender and "notch inset is gone" in offender

        def it_ignores_a_supports_block_that_is_not_an_env_feature_test():
            # `@supports (display: grid)` is parse-tested too, but it is true on the
            # affected engines, so a var()+env() value inside it still breaks them.
            mislabelled = good.replace("(height: env(safe-area-inset-top))", "(display: grid)")
            (offender,) = _inset_offenders(mislabelled)
            assert "height" in offender

        def it_catches_the_logical_property_spelling_of_the_same_tidy_up():
            # `padding-block-start` is the modern spelling a reviewer might "improve"
            # the shorthand into. It sets the same edge, so it must not slip past.
            logical = ".pl-public-topbar { padding-block-start: 0; }"
            (offender,) = _inset_offenders(logical)
            assert "notch inset is gone" in offender

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

        def it_reads_only_the_rules_nested_inside_the_supports_block():
            css = good + ".after { color: blue; }\n"
            (condition, inner, _, end) = _supports_spans(_COMMENT_RE.sub("", css))[0]
            assert condition == "@supports (height: env(safe-area-inset-top))"
            assert ".pl-public-topbar" in inner and ".after" not in inner
            assert _COMMENT_RE.sub("", css)[end:].strip() == ".after { color: blue; }"


def describe_the_height_inset_lives_in_a_supports_block():
    def it_declares_the_plain_height_outside_and_the_inset_inside():
        css = HUB_CSS.read_text(encoding="utf-8")
        outside = [
            value
            for _, body in _bar_rules(_outside_supports(css))
            for prop, value in _box_declarations(body)
            if prop == "height"
        ]
        assert outside == ["var(--topbar-height)"], (
            f"the bar's plain height must be exactly `var(--topbar-height)` and declared once: {outside}"
        )
        guarded = [
            value
            for condition, inner, _, _ in _supports_spans(_COMMENT_RE.sub("", css))
            if _SUPPORTS_ENV_RE.match(condition)
            for _, body in _bar_rules(inner)
            for prop, value in _box_declarations(body)
            if prop == "height"
        ]
        assert guarded == [f"calc(var(--topbar-height) + {INSET})"], guarded

    def it_puts_the_supports_block_after_everything_that_could_override_it():
        # The @supports rule has the same specificity as the base rule and as the
        # breakpoint overrides, so a later `height` on the bar wins over it and the
        # inset is gone again with nothing turning red.
        stripped = _COMMENT_RE.sub("", HUB_CSS.read_text(encoding="utf-8"))
        (span,) = [entry for entry in _supports_spans(stripped) if _SUPPORTS_ENV_RE.match(entry[0])]
        later = [
            f"{selector} {{ height: {value} }}"
            for selector, body in _bar_rules(stripped[span[3] :])
            for prop, value in _box_declarations(body)
            if prop == "height"
        ]
        assert later == [], f"these re-declare the bar's height after the @supports block and win over it: {later}"


def describe_no_var_and_env_in_one_value():
    def it_holds_for_the_stylesheet():
        offenders = _var_and_env_offenders(HUB_CSS.read_text(encoding="utf-8"), "hub.css")
        assert not offenders, _MIXED_MESSAGE + "\n  ".join(offenders)

    def it_holds_for_the_toast_component():
        css = _style_css(TOAST_HTML.read_text(encoding="utf-8"))
        offenders = _var_and_env_offenders(css, "toast.html")
        assert not offenders, _MIXED_MESSAGE + "\n  ".join(offenders)

    def it_finds_the_rules_it_claims_to_check():
        # "Green over nothing" again — both readers must actually reach a rule.
        hub = [selector for selector, _ in _rules(HUB_CSS.read_text(encoding="utf-8"), _INSET_SELECTOR_RE)]
        assert ".pl-public-topbar" in hub, hub
        toast_css = _style_css(TOAST_HTML.read_text(encoding="utf-8"))
        toast = [selector for selector, _ in _rules(toast_css, _INSET_SELECTOR_RE)]
        assert ".plt-container" in toast and ".plt-container--center" in toast, toast

    def it_keeps_the_toasts_inset_on_its_own_var_free_property():
        # The regression this spec was written for: the inset must reach the toast,
        # and it must not reach it by being folded into the `top` calc().
        css = _style_css(TOAST_HTML.read_text(encoding="utf-8"))
        body = dict(_rules(css, _INSET_SELECTOR_RE))[".plt-container"]
        declarations = {match.group(1): match.group(2).strip() for match in _ANY_DECL_RE.finditer(body)}
        assert declarations["top"] == "calc(var(--topbar-height, 56px) + 0.75rem)", declarations["top"]
        assert declarations["margin-top"] == INSET, declarations

    def describe_the_checker_itself():
        def it_detects_a_var_and_env_value_outside_supports():
            (offender,) = _var_and_env_offenders(
                ".plt-container { top: calc(var(--topbar-height, 56px) + env(safe-area-inset-top, 0px) + 0.75rem); }",
                "synthetic",
            )
            assert "top" in offender and "synthetic" in offender

        def it_accepts_the_same_value_inside_a_supports_block():
            guarded = (
                "@supports (height: env(safe-area-inset-top)) {\n"
                "    .pl-public-topbar { height: calc(var(--topbar-height) + env(safe-area-inset-top, 0px)); }\n"
                "}\n"
            )
            assert _var_and_env_offenders(guarded, "synthetic") == []

        def it_accepts_var_and_env_in_separate_declarations():
            split = (
                ".plt-container {\n"
                "    top: calc(var(--topbar-height, 56px) + 0.75rem);\n"
                "    margin-top: env(safe-area-inset-top, 0px);\n"
                "}\n"
            )
            assert _var_and_env_offenders(split, "synthetic") == []

        def it_ignores_elements_that_are_not_positioned_against_the_bar():
            # `.pl-topbar` is the authenticated bar: same shape, its own ticket.
            authenticated = ".pl-topbar { height: calc(var(--topbar-height) + env(safe-area-inset-top, 0px)); }"
            assert _var_and_env_offenders(authenticated, "synthetic") == []

        def it_reads_the_style_element_and_not_the_script():
            html = "<style>\n.plt-container { top: 1px; }\n</style>\n<script>var x = {a: 1};</script>\n"
            assert _style_css(html).strip() == ".plt-container { top: 1px; }"


def describe_topbar_height_token():
    def it_is_defined_once_and_still_56px():
        # The bar's height is `calc(var(--topbar-height) + env(…))`; every width the
        # e2e spec measures the brand against assumes this value.
        values = _TOPBAR_HEIGHT_RE.findall(_COMMENT_RE.sub("", HUB_CSS.read_text(encoding="utf-8")))
        assert values == ["56px"], f"--topbar-height changed or is defined more than once: {values}"
