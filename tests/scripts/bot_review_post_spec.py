"""BDD specs for ``.github/scripts/bot_review_post.py``.

The script lives under ``.github/scripts`` (not a Python package), so it is loaded from its
file path via ``importlib``, the same way ``discord_release_notify_spec.py`` loads its script.

What these specs are really guarding is the fail-closed property: the only input that may ever
produce an ``APPROVE`` is a well-formed verdict that says ``approve`` and carries a review
body. Everything else — no file, truncated JSON, a JSON array, a missing body, a verdict the
reviewer invented — must degrade to a comment that says no approval was given. That matters
because the verdict is written by a model that has just read a diff any stranger can author.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import urllib.error
import urllib.request
from email.message import Message
from types import ModuleType

import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parents[2] / ".github" / "scripts" / "bot_review_post.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("bot_review_post", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def module() -> ModuleType:
    return _load_script()


class _FakeResponse:
    """Stands in for the object ``urlopen`` yields as a context manager."""

    def __init__(self, status: int) -> None:
        self.status = status

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_: object) -> None:
        return None


def describe_bot_review_post():
    def describe_read_verdict():
        def it_returns_the_file_contents(module, tmp_path):
            path = tmp_path / "verdict.json"
            path.write_text('{"verdict": "approve"}', encoding="utf-8")
            assert module.read_verdict(path) == '{"verdict": "approve"}'

        def describe_when_the_file_is_missing():
            def it_returns_none(module, tmp_path):
                assert module.read_verdict(tmp_path / "nope.json") is None

        def describe_when_the_path_is_a_directory():
            def it_returns_none_rather_than_raising(module, tmp_path):
                assert module.read_verdict(tmp_path) is None

    def describe_decide():
        def describe_with_a_clean_approval():
            def it_approves(module):
                raw = json.dumps({"verdict": "approve", "body": "Looks good."})
                assert module.decide(raw) == ("APPROVE", "Looks good.", True)

        def describe_with_blockers():
            def it_comments_rather_than_requesting_changes(module):
                raw = json.dumps({"verdict": "request_changes", "body": "Missing help_text."})
                event, body, clean = module.decide(raw)
                assert event == "COMMENT"
                assert body == "Missing help_text."
                assert clean is True

        def describe_when_there_is_no_file():
            def it_approves_nothing_and_says_so(module):
                event, body, clean = module.decide(None)
                assert event == "COMMENT"
                assert "No approval has been given" in body
                assert clean is False

        def describe_when_the_file_is_blank():
            def it_approves_nothing(module):
                event, _, clean = module.decide("   \n  ")
                assert event == "COMMENT"
                assert clean is False

        def describe_when_the_json_is_unreadable():
            def it_approves_nothing_and_says_so(module):
                event, body, clean = module.decide("this is not json")
                assert event == "COMMENT"
                assert "could not be read as JSON" in body
                assert clean is False

            def it_survives_a_truncated_file(module):
                event, _, clean = module.decide('{"verdict": "approve", "body": "Looks go')
                assert event == "COMMENT"
                assert clean is False

        def describe_when_the_json_is_not_an_object():
            def it_approves_nothing(module):
                event, body, clean = module.decide('["approve"]')
                assert event == "COMMENT"
                assert "could not be read as JSON" in body
                assert clean is False

        def describe_when_the_body_is_missing():
            def it_approves_nothing_and_says_so(module):
                event, body, clean = module.decide(json.dumps({"verdict": "approve"}))
                assert event == "COMMENT"
                assert "no review text" in body
                assert clean is False

            def it_treats_a_whitespace_body_as_missing(module):
                event, _, clean = module.decide(json.dumps({"verdict": "approve", "body": "  "}))
                assert event == "COMMENT"
                assert clean is False

        def describe_when_the_verdict_is_unrecognised():
            def it_comments_with_the_review_and_approves_nothing(module):
                raw = json.dumps({"verdict": "lgtm ship it", "body": "All fine."})
                event, body, clean = module.decide(raw)
                assert event == "COMMENT"
                assert "unrecognised verdict" in body
                assert "All fine." in body
                assert clean is False

            def it_does_not_approve_on_a_missing_verdict_key(module):
                event, _, clean = module.decide(json.dumps({"body": "All fine."}))
                assert event == "COMMENT"
                assert clean is False

            def it_does_not_approve_on_a_near_miss(module):
                # "Approve" is not "approve". An exact match is the whole gate.
                event, _, clean = module.decide(json.dumps({"verdict": "Approve", "body": "Fine."}))
                assert event == "COMMENT"
                assert clean is False

    def describe_post_review():
        def it_posts_the_event_and_body_to_the_pull_request(module, monkeypatch):
            captured: dict[str, object] = {}

            def fake_urlopen(request, timeout):
                captured["url"] = request.full_url
                captured["payload"] = json.loads(request.data.decode("utf-8"))
                captured["auth"] = request.get_header("Authorization")
                captured["timeout"] = timeout
                return _FakeResponse(200)

            monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
            module.post_review("org/repo", "354", "ghp_token", "APPROVE", "Looks good.")

            assert captured["url"] == "https://api.github.com/repos/org/repo/pulls/354/reviews"
            assert captured["payload"] == {"event": "APPROVE", "body": "Looks good."}
            assert captured["auth"] == "Bearer ghp_token"

        def describe_when_github_rejects_it():
            def it_exits_loudly(module, monkeypatch):
                def fake_urlopen(request, timeout):
                    raise urllib.error.HTTPError(request.full_url, 422, "Unprocessable", Message(), None)

                monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
                with pytest.raises(SystemExit) as excinfo:
                    module.post_review("org/repo", "354", "t", "APPROVE", "body")
                assert "422" in str(excinfo.value)

        def describe_when_github_is_unreachable():
            def it_exits_loudly(module, monkeypatch):
                def fake_urlopen(request, timeout):
                    raise urllib.error.URLError("no route to host")

                monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
                with pytest.raises(SystemExit) as excinfo:
                    module.post_review("org/repo", "354", "t", "APPROVE", "body")
                assert "Could not reach GitHub" in str(excinfo.value)

        def describe_when_the_response_is_an_unexpected_status():
            def it_exits_loudly(module, monkeypatch):
                monkeypatch.setattr(urllib.request, "urlopen", lambda request, timeout: _FakeResponse(302))
                with pytest.raises(SystemExit) as excinfo:
                    module.post_review("org/repo", "354", "t", "APPROVE", "body")
                assert "302" in str(excinfo.value)

    def describe_main():
        @pytest.fixture
        def env(monkeypatch, tmp_path):
            monkeypatch.setenv("GITHUB_REPOSITORY", "org/repo")
            monkeypatch.setenv("PR_NUMBER", "354")
            monkeypatch.setenv("GITHUB_TOKEN", "ghp_token")
            monkeypatch.setenv("VERDICT_FILE", str(tmp_path / "verdict.json"))
            return tmp_path / "verdict.json"

        def it_posts_the_approval_and_exits_zero(module, monkeypatch, env):
            env.write_text(json.dumps({"verdict": "approve", "body": "Good."}), encoding="utf-8")
            posted: list[tuple[str, str]] = []
            monkeypatch.setattr(module, "post_review", lambda *args: posted.append((args[3], args[4])))

            module.main()

            assert posted == [("APPROVE", "Good.")]

        def describe_when_the_reviewer_produced_no_verdict():
            def it_posts_a_comment_and_fails_the_job(module, monkeypatch, env):
                posted: list[tuple[str, str]] = []
                monkeypatch.setattr(module, "post_review", lambda *args: posted.append((args[3], args[4])))

                with pytest.raises(SystemExit) as excinfo:
                    module.main()

                assert posted[0][0] == "COMMENT"
                assert "No approval has been given" in posted[0][1]
                # Non-zero, so the failure is visible in Actions and not only on the PR.
                assert excinfo.value.code != 0

        def describe_when_a_required_env_var_is_missing():
            def it_fails_loudly_rather_than_defaulting(module, monkeypatch, env):
                monkeypatch.delenv("GITHUB_TOKEN")
                with pytest.raises(KeyError):
                    module.main()
