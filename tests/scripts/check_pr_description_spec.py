"""BDD specs for ``.github/scripts/check_pr_description.py``.

The script lives under ``.github/scripts`` (not a Python package), so it is loaded from its
file path via ``importlib``, matching the other script specs. ``_fake_git`` answers only the
three exact diff calls the check makes and raises on anything else, so a change to the span or
the filters fails here rather than silently checking the wrong files.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
from collections.abc import Callable
from types import ModuleType

import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parents[2] / ".github" / "scripts" / "check_pr_description.py"
_SPAN = f"{'a' * 40}...{'b' * 40}"

GOOD = """<!-- As a guild lead, when a class is sent back, I would like the reason. -->
**Summary:** Guild leads now see why a class was sent back, right on the class page.

**Area:** class review: the review model, the class page and its email.

### Problem
Closes #412. A class sent back shows no reason, so the instructor has to ask.

### Solution
- Store the reason on the review.
- Show it on the class page and in the email.

### Impact / Risks
One additive migration; no breaking changes.

### Verification
`pytest tests/classes/review_spec.py` passes; screenshot below.
"""


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_pr_description", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def script() -> ModuleType:
    return _load_script()


def _fake_git(changed: str = "", added: str = "", numstat: str = "") -> Callable[..., str]:
    answers = {
        ("diff", "--name-only", _SPAN): changed,
        ("diff", "--name-only", "--diff-filter=A", _SPAN): added,
        ("diff", "--numstat", _SPAN): numstat,
    }

    def fake(*args: str) -> str:
        if args not in answers:
            raise AssertionError(f"unexpected git call: {args}")
        return answers[args]

    return fake


def describe_description_errors():
    def it_accepts_a_filled_in_template(script):
        assert script.description_errors(GOOD) == []

    def it_requires_a_summary(script):
        body = GOOD.replace(
            "**Summary:** Guild leads now see why a class was sent back, right on the class page.\n", ""
        )
        assert any("Summary" in error for error in script.description_errors(body))

    def it_rejects_an_empty_summary(script):
        body = GOOD.replace("Guild leads now see why a class was sent back, right on the class page.", "")
        assert any("Summary" in error for error in script.description_errors(body))

    def it_does_not_count_a_summary_left_inside_a_comment(script):
        body = GOOD.replace("**Summary:** Guild", "<!-- **Summary:** Guild").replace(
            "class page.\n\n**Area", "class page. -->\n\n**Area"
        )
        assert any("Summary" in error for error in script.description_errors(body))

    def it_caps_the_summary_at_160_characters(script):
        body = GOOD.replace("right on the class page.", "x" * 160)
        assert any("keep it to 160" in error for error in script.description_errors(body))

    def it_allows_exactly_160_characters(script):
        summary = "y" * 160
        body = GOOD.replace("Guild leads now see why a class was sent back, right on the class page.", summary)
        assert script.description_errors(body) == []

    def it_requires_an_area(script):
        body = GOOD.replace("**Area:** class review: the review model, the class page and its email.\n", "")
        assert any("Area" in error for error in script.description_errors(body))

    def it_requires_every_section(script):
        body = GOOD.replace("### Verification\n`pytest tests/classes/review_spec.py` passes; screenshot below.\n", "")
        assert script.description_errors(body) == ["Missing or empty `### Verification` section."]

    def it_rejects_an_empty_section(script):
        body = GOOD.replace("One additive migration; no breaking changes.", "")
        assert script.description_errors(body) == ["Missing or empty `### Impact / Risks` section."]

    def it_accepts_impact_risks_without_spaces(script):
        assert script.description_errors(GOOD.replace("### Impact / Risks", "## Impact/Risks")) == []

    def describe_solution_bullets():
        def it_rejects_one(script):
            body = GOOD.replace("- Show it on the class page and in the email.\n", "")
            assert script.description_errors(body) == ["`### Solution` has 1 bullet(s); use 2 to 4."]

        def it_rejects_five(script):
            extra = "- Show it on the class page and in the email.\n" + "- More.\n" * 3
            body = GOOD.replace("- Show it on the class page and in the email.\n", extra)
            assert script.description_errors(body) == ["`### Solution` has 5 bullet(s); use 2 to 4."]

        def it_accepts_four_numbered(script):
            numbered = "1. Store it.\n2. Show it.\n3. Email it.\n4. Log it.\n"
            body = GOOD.replace(
                "- Store the reason on the review.\n- Show it on the class page and in the email.\n", numbered
            )
            assert script.description_errors(body) == []

    def it_caps_the_description_at_300_words(script):
        body = GOOD + "\n" + "word " * 300
        errors = script.description_errors(body)
        assert len(errors) == 1
        assert "keep it to 300" in errors[0]


def describe_word_count():
    def it_skips_comments_and_link_targets(script):
        body = "<!-- hidden words here -->Two words ![shot](mockups/screenshots/412-review-01.png)"
        assert script.word_count(body) == 3

    def it_counts_markdown_as_words_only_where_text_is(script):
        assert script.word_count("### Solution\n- Store it.") == 3


def describe_needs_pictures():
    def it_is_true_for_templates_css_and_js(script):
        assert script.needs_pictures(["templates/hub/home.html"])
        assert script.needs_pictures(["static/css/hub.css"])
        assert script.needs_pictures(["static/js/native-push.js"])

    def it_is_false_for_backend_and_help_images(script):
        assert not script.needs_pictures(["classes/models.py", "static/help/guide/01-home.png"])


def describe_adds_pictures():
    def it_accepts_an_image_under_mockups(script):
        assert script.adds_pictures(["mockups/screenshots/412-review-01.PNG"])

    def it_rejects_images_elsewhere_and_non_images(script):
        assert not script.adds_pictures(["static/img/logo.png", "mockups/screenshots/README.md"])


def describe_counted_lines():
    def it_counts_code_and_skips_generated_files(script):
        numstat = "\n".join(
            [
                "10\t5\tclasses/models.py",
                "200\t0\tclasses/migrations/0090_reason.py",
                "-\t-\tmockups/screenshots/412-review-01.png",
                "40\t40\tuv.lock",
                "3\t0\tchangelog.d/412-review-reason.toml",
                "90\t12\tCONTRIBUTING.md",
                "7\t1\ttemplates/classes/detail.html",
            ]
        )
        assert script.counted_lines(numstat) == 23

    def it_is_zero_for_no_changes(script):
        assert script.counted_lines("") == 0


def describe_main():
    @pytest.fixture
    def env(monkeypatch):
        monkeypatch.setenv("BASE_SHA", "a" * 40)
        monkeypatch.setenv("HEAD_SHA", "b" * 40)
        monkeypatch.setenv("PR_BODY", GOOD)
        monkeypatch.setenv("PR_LABELS", "[]")
        return monkeypatch

    def it_passes_a_good_backend_pr(script, env, capsys):
        env.setattr(script, "_git", _fake_git(changed="classes/models.py", numstat="10\t5\tclasses/models.py"))
        script.main()
        assert "PR description OK" in capsys.readouterr().out

    def it_fails_a_bad_description(script, env):
        env.setenv("PR_BODY", "Fixed it.")
        env.setattr(script, "_git", _fake_git())
        with pytest.raises(SystemExit) as exit_info:
            script.main()
        assert "does not follow CONTRIBUTING.md" in str(exit_info.value)

    def it_treats_a_missing_body_as_empty(script, env):
        env.delenv("PR_BODY")
        env.setattr(script, "_git", _fake_git())
        with pytest.raises(SystemExit) as exit_info:
            script.main()
        assert "Summary" in str(exit_info.value)

    def describe_ui_changes():
        def it_fails_without_a_picture(script, env):
            env.setattr(script, "_git", _fake_git(changed="templates/classes/detail.html"))
            with pytest.raises(SystemExit) as exit_info:
                script.main()
            assert "mockups/screenshots/" in str(exit_info.value)

        def it_passes_with_a_picture(script, env, capsys):
            shot = "mockups/screenshots/412-review-01.png"
            env.setattr(script, "_git", _fake_git(changed=f"templates/classes/detail.html\n{shot}", added=shot))
            script.main()
            assert "PR description OK" in capsys.readouterr().out

        def it_passes_with_the_label(script, env, capsys):
            env.setenv("PR_LABELS", json.dumps(["No-Screenshots"]))
            env.setattr(script, "_git", _fake_git(changed="templates/classes/detail.html"))
            script.main()
            assert "PR description OK" in capsys.readouterr().out

    def it_skips_every_rule_with_the_exemption_label(script, env, capsys):
        env.setenv("PR_BODY", "Fixed it.")
        env.setenv("PR_LABELS", json.dumps(["No-Description-Check"]))
        env.setattr(script, "_git", _fake_git(changed="templates/classes/detail.html"))
        script.main()
        assert "not checking the description" in capsys.readouterr().out

    def it_warns_above_400_lines_without_failing(script, env, capsys):
        env.setattr(script, "_git", _fake_git(changed="classes/models.py", numstat="300\t101\tclasses/models.py"))
        script.main()
        out = capsys.readouterr().out
        assert "::warning::401 changed lines" in out
        assert "PR description OK" in out

    def it_stops_when_git_fails(script, env, monkeypatch):
        class _Failed:
            returncode = 128
            stdout = ""
            stderr = "bad revision"

        monkeypatch.setattr(script.subprocess, "run", lambda *args, **kwargs: _Failed())
        with pytest.raises(SystemExit) as exit_info:
            script.main()
        assert "bad revision" in str(exit_info.value)

    def it_runs_real_git(script, env, monkeypatch):
        calls: list[list[str]] = []

        class _Ok:
            returncode = 0
            stdout = ""
            stderr = ""

        def fake_run(argv, **kwargs):
            calls.append(argv)
            return _Ok()

        monkeypatch.setattr(script.subprocess, "run", fake_run)
        script.main()
        assert calls[0] == ["git", "diff", "--name-only", _SPAN]
