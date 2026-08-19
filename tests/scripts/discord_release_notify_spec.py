"""BDD specs for ``.github/scripts/discord_release_notify.py``.

The script lives under ``.github/scripts`` (not a Python package), so it is loaded
from its file path via ``importlib``. ``CHANGELOG`` and ``VERSION`` are module-level
globals the functions read, so the version-filtering specs monkeypatch them on the
loaded module.
"""

from __future__ import annotations

import importlib.util
import io
import pathlib
import urllib.error
import urllib.request
from email.message import Message
from types import ModuleType

import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parents[2] / ".github" / "scripts" / "discord_release_notify.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("discord_release_notify", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def describe_discord_release_notify():
    def describe_release_entries():
        def it_returns_only_the_entry_for_the_current_version(monkeypatch):
            module = _load_script()
            monkeypatch.setattr(module, "VERSION", "0.20.1")
            monkeypatch.setattr(
                module,
                "CHANGELOG",
                [
                    {"version": "0.20.1", "title": "Shipped now", "changes": ["a"]},
                    {"version": "0.19.17", "title": "Older line", "changes": ["b"]},
                    {"version": "0.19.1", "title": "Oldest", "changes": ["c"]},
                ],
            )
            assert [e["version"] for e in module._release_entries()] == ["0.20.1"]

        def it_excludes_other_patches_in_the_same_minor_line(monkeypatch):
            module = _load_script()
            monkeypatch.setattr(module, "VERSION", "0.20.2")
            monkeypatch.setattr(
                module,
                "CHANGELOG",
                [
                    {"version": "0.20.2", "title": "This patch", "changes": ["a"]},
                    {"version": "0.20.1", "title": "Earlier patch, same line", "changes": ["b"]},
                ],
            )
            assert [e["title"] for e in module._release_entries()] == ["This patch"]

        def it_returns_every_entry_stamped_at_the_same_version(monkeypatch):
            module = _load_script()
            monkeypatch.setattr(module, "VERSION", "0.20.1")
            monkeypatch.setattr(
                module,
                "CHANGELOG",
                [
                    {"version": "0.20.1", "title": "Feature one", "changes": ["a"]},
                    {"version": "0.20.1", "title": "Feature two", "changes": ["b"]},
                    {"version": "0.19.17", "title": "Older", "changes": ["c"]},
                ],
            )
            assert [e["title"] for e in module._release_entries()] == ["Feature one", "Feature two"]

        def it_falls_back_to_the_first_entry_when_no_version_matches(monkeypatch):
            module = _load_script()
            monkeypatch.setattr(module, "VERSION", "9.9.9")
            monkeypatch.setattr(
                module,
                "CHANGELOG",
                [
                    {"version": "0.20.1", "title": "Newest", "changes": ["a"]},
                    {"version": "0.19.17", "title": "Older", "changes": ["b"]},
                ],
            )
            entries = module._release_entries()
            assert entries == [module.CHANGELOG[0]]

    def describe_bullets():
        def it_flattens_every_entrys_changes_into_bullets():
            module = _load_script()
            entries: list[dict[str, object]] = [
                {"version": "0.20.1", "title": "T", "changes": ["one", "two"]},
                {"version": "0.20.1", "title": "U", "changes": ["three"]},
            ]
            assert module._bullets(entries) == ["• one", "• two", "• three"]

    def describe_chunks():
        def it_keeps_short_bullets_in_a_single_chunk():
            module = _load_script()
            assert module._chunks(["• a", "• b", "• c"]) == ["• a\n• b\n• c"]

        def it_splits_bullets_across_chunks_under_the_description_limit():
            module = _load_script()
            # Each bullet fits alone, but two together blow past the limit, forcing a split.
            big = "• " + ("x" * (module._DESC_LIMIT // 2))
            chunks = module._chunks([big, big])
            assert len(chunks) == 2
            assert all(len(chunk) <= module._DESC_LIMIT for chunk in chunks)

        def it_returns_no_chunks_for_no_bullets():
            module = _load_script()
            assert module._chunks([]) == []

    def describe_post():
        class _FakeResponse:
            def __init__(self, status: int) -> None:
                self.status = status

            def __enter__(self) -> _FakeResponse:
                return self

            def __exit__(self, *args: object) -> None:
                return None

        def it_succeeds_on_a_2xx_response(monkeypatch):
            module = _load_script()
            monkeypatch.setattr(urllib.request, "urlopen", lambda request: _FakeResponse(204))
            module._post("https://example.test/webhook", {"embeds": []})

        def it_exits_loudly_on_a_non_2xx_status(monkeypatch):
            module = _load_script()
            monkeypatch.setattr(urllib.request, "urlopen", lambda request: _FakeResponse(500))
            with pytest.raises(SystemExit):
                module._post("https://example.test/webhook", {"embeds": []})

        def it_exits_loudly_when_discord_rejects_the_post(monkeypatch):
            module = _load_script()

            def _raise(request: object) -> None:
                raise urllib.error.HTTPError(
                    url="https://example.test/webhook",
                    code=400,
                    msg="Bad Request",
                    hdrs=Message(),
                    fp=io.BytesIO(b"bad payload"),
                )

            monkeypatch.setattr(urllib.request, "urlopen", _raise)
            with pytest.raises(SystemExit):
                module._post("https://example.test/webhook", {"embeds": []})

        def it_exits_loudly_on_a_network_error(monkeypatch):
            module = _load_script()

            def _raise(request: object) -> None:
                raise urllib.error.URLError("connection refused")

            monkeypatch.setattr(urllib.request, "urlopen", _raise)
            with pytest.raises(SystemExit):
                module._post("https://example.test/webhook", {"embeds": []})

    def describe_main():
        def it_skips_posting_when_the_webhook_is_blank(monkeypatch, capsys):
            module = _load_script()
            monkeypatch.setenv("DISCORD_WEBHOOK_URL", "")
            posts: list[object] = []
            monkeypatch.setattr(module, "_post", lambda webhook, payload: posts.append(payload))
            module.main()
            assert posts == []
            assert "skipping" in capsys.readouterr().out.lower()

        def it_posts_a_single_titled_embed_for_one_chunk(monkeypatch):
            module = _load_script()
            monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://example.test/webhook")
            monkeypatch.setattr(module, "VERSION", "0.20.1")
            monkeypatch.setattr(
                module,
                "CHANGELOG",
                [{"version": "0.20.1", "title": "Solo feature", "changes": ["did a thing"]}],
            )
            posts: list[dict[str, object]] = []
            monkeypatch.setattr(module, "_post", lambda webhook, payload: posts.append(payload))
            module.main()
            assert len(posts) == 1
            embed = posts[0]["embeds"][0]
            assert embed["title"] == "🚀 New update: Solo feature"
            assert embed["footer"] == {"text": "Past Lives Member Portal v0.20.1"}

        def it_titles_the_first_embed_and_continues_the_rest_across_chunks(monkeypatch):
            module = _load_script()
            monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://example.test/webhook")
            monkeypatch.setattr(module, "VERSION", "0.20.1")
            long_change = "x" * (module._DESC_LIMIT - 10)
            monkeypatch.setattr(
                module,
                "CHANGELOG",
                [{"version": "0.20.1", "title": "Big release", "changes": [long_change, long_change]}],
            )
            posts: list[dict[str, object]] = []
            monkeypatch.setattr(module, "_post", lambda webhook, payload: posts.append(payload))
            module.main()
            assert len(posts) == 2
            assert posts[0]["embeds"][0]["title"] == "🚀 New update: Big release"
            assert posts[1]["embeds"][0]["title"] == "…continued (2/2)"
