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

**Guidance:** none

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


_FRAGMENT = "changelog.d/412-review-reason.toml"
_LINKED = (
    'bump = "minor"\ndate = "2026-10-29"\ntitle = "Reasons"\nchanges = ["A reason."]\n'
    'audience = ["instructors", "guild-leads"]\nfeature = "classes.teach"\nguidance = "link"\n'
)


def _fake_git(changed: str = "", added: str = "", numstat: str = "", fragment: str | None = None) -> Callable[..., str]:
    answers = {
        ("diff", "--name-only", _SPAN): changed,
        ("diff", "--name-only", "--diff-filter=A", _SPAN): added,
        ("diff", "--numstat", _SPAN): numstat,
    }
    if fragment is not None:
        answers[("show", f"{'b' * 40}:{_FRAGMENT}")] = fragment

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

    def describe_the_guidance_line():
        def it_requires_it(script):
            body = GOOD.replace("**Guidance:** none\n", "")
            assert script.description_errors(body) == [
                "Missing the `**Guidance:**` line: none, link, shots or tour, how much teaching this ships (docs/GUIDANCE.md)."
            ]

        def it_rejects_a_blank_value(script):
            body = GOOD.replace("**Guidance:** none", "**Guidance:** ")
            assert script.description_errors(body) == [
                "`**Guidance:** ` is not a level; use one of none, link, shots, tour."
            ]

        def it_rejects_a_value_outside_the_four_levels(script):
            errors = script.description_errors(GOOD.replace("**Guidance:** none", "**Guidance:** video"))
            assert errors == ["`**Guidance:** video` is not a level; use one of none, link, shots, tour."]

        @pytest.mark.parametrize("level", ["none", "link", "shots", "tour", "Link"])
        def it_accepts_each_level_in_any_case(script, level: str):
            assert script.description_errors(GOOD.replace("**Guidance:** none", f"**Guidance:** {level}")) == []

        def it_reads_the_level_it_declares(script):
            assert script.declared_guidance(GOOD) == "none"
            assert script.declared_guidance(GOOD.replace("**Guidance:** none", "**Guidance:** Tour")) == "tour"
            assert script.declared_guidance("no line") is None

    def it_requires_every_section(script):
        body = GOOD.replace("### Verification\n`pytest tests/classes/review_spec.py` passes; screenshot below.\n", "")
        assert script.description_errors(body) == ["Missing or empty `### Verification` section."]

    def it_rejects_an_empty_section(script):
        body = GOOD.replace("One additive migration; no breaking changes.", "")
        assert script.description_errors(body) == ["Missing or empty `### Impact / Risks` section."]

    def it_accepts_impact_risks_without_spaces(script):
        assert script.description_errors(GOOD.replace("### Impact / Risks", "## Impact/Risks")) == []

    def describe_split_issue_parts():
        def _problem(line: str) -> str:
            return GOOD.replace("Closes #412. A class sent back", f"{line} A class sent back")

        def it_requires_the_last_part_to_close_its_issue(script):
            errors = script.description_errors(_problem("Part 3 of 3 of #412, the last of it."))
            assert len(errors) == 1
            assert "part 3 of 3" in errors[0]
            assert "Closes #" in errors[0]

        def it_accepts_a_last_part_that_closes(script):
            assert script.description_errors(_problem("Closes #412, part 3 of 3.")) == []

        def it_accepts_any_github_closing_keyword(script):
            assert script.description_errors(_problem("Fixes: #412 (part 2 of 2; part 1 was #410).")) == []

        def it_rejects_an_earlier_part_that_closes(script):
            errors = script.description_errors(_problem("Closes #412, part 1 of 3."))
            assert len(errors) == 1
            assert "part 1 of 3" in errors[0]

        def it_accepts_an_earlier_part_that_only_names_its_issue(script):
            assert script.description_errors(_problem("#412, part 2 of 3.")) == []

        def it_reads_the_part_from_the_problem_section_only(script):
            body = GOOD.replace(
                "- Store the reason on the review.", "- Store the reason, as part 1 of 2 did for notes."
            )
            assert script.description_errors(body) == []

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


def describe_picture_link_errors():
    def it_accepts_a_screenshot_uploaded_to_r2(script):
        body = (
            GOOD
            + "\n![Reason](https://pub-10991d66419645cd89916024ee6bb62c.r2.dev/pr-screenshots/2026-10-20/412-review-01.png)\n"
        )
        assert script.description_errors(body) == []

    def it_accepts_an_upload_github_hosts(script):
        body = GOOD + "\n![Reason](https://github.com/user-attachments/assets/abc123)\n"
        assert script.description_errors(body) == []

    def it_fails_a_relative_link_which_renders_broken_in_a_pr(script):
        body = GOOD + "\n![Reason](mockups/screenshots/412-review-01.png)\n"
        errors = script.description_errors(body)
        assert len(errors) == 1
        assert "relative link" in errors[0] and "scripts/upload_screenshot.py" in errors[0]

    def it_names_each_broken_image(script):
        body = GOOD + "\n![A](mockups/screenshots/a.png)\n![B](./mockups/screenshots/b.png)\n"
        assert [error.split("`")[1] for error in script.description_errors(body)] == [
            "mockups/screenshots/a.png",
            "./mockups/screenshots/b.png",
        ]

    def it_ignores_an_image_inside_a_comment(script):
        body = GOOD + "\n<!-- ![Reason](mockups/screenshots/412-review-01.png) -->\n"
        assert script.description_errors(body) == []


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


def describe_committed_pictures():
    def it_lists_images_under_mockups(script):
        assert script.committed_pictures(["mockups/screenshots/412-review-01.PNG", "mockups/a.png"]) == [
            "mockups/screenshots/412-review-01.PNG",
            "mockups/a.png",
        ]

    def it_ignores_images_elsewhere_and_html_mockups(script):
        assert script.committed_pictures(["static/img/logo.png", "mockups/748-approvals.html"]) == []


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
            assert "shows no screenshot" in str(exit_info.value)
            assert "scripts/upload_screenshot.py" in str(exit_info.value)

        def it_passes_with_a_screenshot_uploaded_to_r2(script, env, capsys):
            env.setenv(
                "PR_BODY",
                GOOD
                + "\n![Reason](https://pub-10991d66419645cd89916024ee6bb62c.r2.dev/pr-screenshots/2026-10-20/412-review-01.png)\n",
            )
            env.setattr(script, "_git", _fake_git(changed="templates/classes/detail.html"))
            script.main()
            assert "PR description OK" in capsys.readouterr().out

        def it_fails_a_committed_image_even_when_shown(script, env):
            shot = "mockups/screenshots/412-review-01.png"
            env.setenv(
                "PR_BODY",
                GOOD
                + "\n![Reason](https://pub-10991d66419645cd89916024ee6bb62c.r2.dev/pr-screenshots/2026-10-20/412-review-01.png)\n",
            )
            env.setattr(script, "_git", _fake_git(changed=f"templates/classes/detail.html\n{shot}", added=shot))
            with pytest.raises(SystemExit) as exit_info:
                script.main()
            assert f"commits `{shot}`" in str(exit_info.value)

        def it_fails_a_committed_image_on_a_backend_pr_too(script, env):
            shot = "mockups/screenshots/412-review-01.png"
            env.setattr(script, "_git", _fake_git(changed=f"classes/models.py\n{shot}", added=shot))
            with pytest.raises(SystemExit) as exit_info:
                script.main()
            assert "Images stay out of the repo" in str(exit_info.value)

        def it_fails_a_picture_shown_by_a_relative_link(script, env):
            env.setenv("PR_BODY", GOOD + "\n![Reason](mockups/screenshots/412-review-01.png)\n")
            env.setattr(script, "_git", _fake_git(changed="templates/classes/detail.html"))
            with pytest.raises(SystemExit) as exit_info:
                script.main()
            assert "relative link" in str(exit_info.value)

        def it_passes_with_the_label(script, env, capsys):
            env.setenv("PR_LABELS", json.dumps(["No-Screenshots"]))
            env.setattr(script, "_git", _fake_git(changed="templates/classes/detail.html"))
            script.main()
            assert "PR description OK" in capsys.readouterr().out

    def describe_the_fragment_the_pr_adds():
        """The body's level must match the ``guidance`` of the one fragment the PR adds (#1007)."""

        def it_fails_when_the_body_and_the_fragment_disagree(script, env):
            env.setattr(script, "_git", _fake_git(changed=_FRAGMENT, added=_FRAGMENT, fragment=_LINKED))
            with pytest.raises(SystemExit) as exit_info:
                script.main()
            message = str(exit_info.value)
            assert "`**Guidance:** none`" in message and '`guidance = "link"`' in message

        def it_passes_when_they_agree(script, env, capsys):
            env.setenv("PR_BODY", GOOD.replace("**Guidance:** none", "**Guidance:** link"))
            env.setattr(script, "_git", _fake_git(changed=_FRAGMENT, added=_FRAGMENT, fragment=_LINKED))
            script.main()
            assert "PR description OK" in capsys.readouterr().out

        def it_treats_an_internal_fragment_as_none(script, env, capsys):
            env.setattr(
                script,
                "_git",
                _fake_git(changed=_FRAGMENT, added=_FRAGMENT, fragment='bump = "patch"\naudience = "internal"\n'),
            )
            script.main()
            assert "PR description OK" in capsys.readouterr().out

        def it_says_an_internal_fragment_has_no_guidance_rather_than_quoting_a_key_it_lacks(script, env):
            env.setenv("PR_BODY", GOOD.replace("**Guidance:** none", "**Guidance:** link"))
            env.setattr(
                script,
                "_git",
                _fake_git(changed=_FRAGMENT, added=_FRAGMENT, fragment='bump = "patch"\naudience = "internal"\n'),
            )
            with pytest.raises(SystemExit) as exit_info:
                script.main()
            message = str(exit_info.value)
            assert "an internal fragment has no guidance, so the line must say `none`" in message
            assert 'guidance = "none"' not in message

        def it_reports_only_the_missing_line_when_the_body_has_none(script, env):
            # No level to compare, so no second error about a mismatch.
            env.setenv("PR_BODY", GOOD.replace("**Guidance:** none\n", ""))
            env.setattr(script, "_git", _fake_git(changed=_FRAGMENT, added=_FRAGMENT, fragment=_LINKED))
            with pytest.raises(SystemExit) as exit_info:
                script.main()
            assert str(exit_info.value).count("Guidance") == 1

        def it_leaves_a_fragment_that_does_not_parse_to_the_fragment_check(script, env, capsys):
            env.setattr(script, "_git", _fake_git(changed=_FRAGMENT, added=_FRAGMENT, fragment='bump = "sideways"\n'))
            script.main()
            assert "PR description OK" in capsys.readouterr().out

        def it_compares_nothing_when_the_pr_adds_no_fragment(script, env, capsys):
            # _fake_git raises on a `git show`, so reaching one here would fail the spec.
            env.setattr(script, "_git", _fake_git(changed="classes/models.py", added="classes/models.py"))
            script.main()
            assert "PR description OK" in capsys.readouterr().out

        def it_compares_nothing_when_the_pr_adds_two(script, env, capsys):
            added = f"{_FRAGMENT}\nchangelog.d/413-other.toml"
            env.setattr(script, "_git", _fake_git(changed=added, added=added))
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
