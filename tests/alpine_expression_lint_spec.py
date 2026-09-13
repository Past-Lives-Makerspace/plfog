"""Repo-wide template lint: an Alpine handler attribute holds an expression, not a statement.

Alpine compiles ``@click="…"`` (and ``x-on:``, ``x-init``, ``x-effect``) as the right hand
side of an assignment, so a value that starts with a ``try`` statement is a SyntaxError
(``Alpine Expression Error: Unexpected token 'try'``) on every render of the page, and
the handler never runs. FRONTEND.md Rule 14 shipped exactly that for the session
scheduler's date field (issue #378). The working form wraps the statement in an arrow
IIFE, ``(() => { try { $el.showPicker() } catch (e) {} })()``, which this lint allows: it
only flags a handler whose expression *begins* with ``try``.
"""

from __future__ import annotations

import re
from pathlib import Path

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

# `@click`, `@click.prevent`, `x-on:input.debounce`, `x-init`, `x-effect`, with a double or
# single quoted value. Values may span lines (multi-line x-init blocks), hence DOTALL.
_HANDLER_RE = re.compile(
    r"""(?<![\w-])(?:@[\w.:-]+|x-on:[\w.:-]+|x-init|x-effect)\s*=\s*(?:"(?P<dq>[^"]*)"|'(?P<sq>[^']*)')""",
    re.DOTALL,
)
# Statement keywords that cannot open an expression: `__self.result = try {…}` is a
# SyntaxError, and so is each of these. `if`, `let` and `const` are absent on purpose,
# Alpine wraps those in an async IIFE itself; `function` and `class` are absent because a
# function or class expression IS a valid expression (Alpine calls a handler that
# evaluates to a function).
_STATEMENT_START_RE = re.compile(r"^\s*(?:try|for|while|switch|throw|do|var)\b")


def _statement_handlers(text: str) -> list[int]:
    """Line numbers (1-based) of Alpine handler attributes whose value opens with a statement."""
    offenders = []
    for match in _HANDLER_RE.finditer(text):
        value = match.group("dq") if match.group("dq") is not None else match.group("sq")
        if _STATEMENT_START_RE.match(value):
            offenders.append(text.count("\n", 0, match.start()) + 1)
    return offenders


def describe_alpine_handler_attributes():
    def it_never_starts_a_handler_with_a_try_statement():
        offenders = [
            f"templates/{path.relative_to(TEMPLATES_DIR)}:{lineno}"
            for path in sorted(TEMPLATES_DIR.rglob("*.html"))
            for lineno in _statement_handlers(path.read_text(encoding="utf-8"))
        ]
        assert not offenders, (
            "An Alpine handler is an expression; a statement (try, for, while, switch, throw, do, var) "
            "is a SyntaxError on every render. Wrap it: "
            '@click="(() => { try { $el.showPicker() } catch (e) {} })()"\n  ' + "\n  ".join(offenders)
        )

    def it_actually_detects_a_statement_handler():
        # Self-test so a refactor can't quietly neuter the lint.
        leaky = (
            '<input @click="try { $el.showPicker() } catch (e) {}">\n'
            "<input x-on:click='try { $el.showPicker() } catch (e) {}'>\n"
            '<input @click="(() => { try { $el.showPicker() } catch (e) {} })()">\n'
            '<input @click="tryAgain()">\n'
            '<div x-init="\n  try { boot() } catch (e) {}\n">\n'
            '<button @click="for (const s of steps) go(s)">\n'
            "<button @click=\"throw new Error('no')\">\n"
            "<button @click=\"doIt(); document.title = 'x'\">\n"
            '<button @click="function (e) { go(e) }">'
        )
        assert _statement_handlers(leaky) == [1, 2, 5, 8, 9]
