"""BDD specs for ``scripts/upload_screenshot.py``.

The upload itself talks to R2 and is not exercised here; what is pinned is where an object
lands (only under ``pr-screenshots/``, or ``guidance/<slug>/`` for a walkthrough's shots, #1050),
that a non-image never reaches the bucket, and what the guidance mode refuses and prints.
"""

from __future__ import annotations

import datetime
import importlib.util
import pathlib
import runpy
import subprocess
import sys
from types import ModuleType

import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "upload_screenshot.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("upload_screenshot", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


upload = _load()


def describe_object_key():
    def it_files_the_image_under_the_pr_screenshots_prefix_by_day():
        key = upload.object_key(pathlib.Path("mockups/screenshots/412-review-01.png"), datetime.date(2026, 10, 20))
        assert key == "pr-screenshots/2026-10-20/412-review-01.png"


def describe_markdown():
    def it_embeds_the_url_with_the_file_stem_as_alt_text():
        assert (
            upload.markdown(pathlib.Path("a/412-review-01.png"), "https://x/y.png")
            == "![412-review-01](https://x/y.png)"
        )


def describe_read_env_file():
    def it_reads_keys_strips_quotes_and_skips_comments():
        text = '# comment\nR2_BUCKET_NAME="plfog"\nexport R2_PUBLIC_URL=https://pub.example\n\nNOISE\n'
        assert upload.read_env_file(text) == {"R2_BUCKET_NAME": "plfog", "R2_PUBLIC_URL": "https://pub.example"}


def describe_main():
    def it_refuses_a_file_that_is_not_an_image_before_touching_r2(tmp_path):
        notes = tmp_path / "notes.txt"
        notes.write_text("hi")
        with pytest.raises(SystemExit) as exit_info:
            upload.main([str(notes)])
        assert "not an image file" in str(exit_info.value)

    def it_refuses_a_missing_file(tmp_path):
        with pytest.raises(SystemExit) as exit_info:
            upload.main([str(tmp_path / "gone.png")])
        assert "not an image file" in str(exit_info.value)


class _FakeS3:
    """Stands in for the boto3 S3 client: R2 is an external service."""

    def __init__(self, existing: set[str] = frozenset(), head_error: str | None = None) -> None:
        self.existing = set(existing)
        self.head_error = head_error
        self.uploads: list[tuple[str, str, str]] = []

    def head_object(self, Bucket: str, Key: str) -> dict:
        from botocore.exceptions import ClientError

        if self.head_error:
            raise ClientError({"Error": {"Code": self.head_error}}, "HeadObject")
        if Key not in self.existing:
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
        return {}

    def upload_file(self, filename: str, bucket: str, key: str, ExtraArgs: dict) -> None:
        self.uploads.append((bucket, key, ExtraArgs["ContentType"]))


_CONFIG = {
    "R2_ACCOUNT_ID": "acct",
    "R2_BUCKET_NAME": "plfog",
    "R2_ACCESS_KEY_ID": "id",
    "R2_SECRET_ACCESS_KEY": "secret",
    "R2_PUBLIC_URL": "https://pub.example/",
}


@pytest.fixture
def fake_r2(monkeypatch):
    import boto3

    def install(client: _FakeS3) -> _FakeS3:
        monkeypatch.setattr(upload, "settings", lambda: dict(_CONFIG))
        monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: client)
        return client

    return install


def describe_exists():
    def it_is_false_when_r2_answers_404():
        assert upload._exists(_FakeS3(), "plfog", "pr-screenshots/x.png") is False

    def it_is_true_when_the_object_is_there():
        assert upload._exists(_FakeS3(existing={"pr-screenshots/x.png"}), "plfog", "pr-screenshots/x.png") is True

    def it_re_raises_any_other_error_rather_than_guess_absent():
        from botocore.exceptions import ClientError

        with pytest.raises(ClientError):
            upload._exists(_FakeS3(head_error="403"), "plfog", "pr-screenshots/x.png")


def describe_uploading():
    def it_uploads_under_the_prefix_and_prints_the_embed(tmp_path, fake_r2, capsys):
        shot = tmp_path / "412-review-01.png"
        shot.write_bytes(b"png")
        client = fake_r2(_FakeS3())
        upload.main([str(shot)])
        [(bucket, key, content_type)] = client.uploads
        assert bucket == "plfog" and key.startswith("pr-screenshots/") and key.endswith("/412-review-01.png")
        assert content_type == "image/png"
        assert capsys.readouterr().out.strip() == f"![412-review-01](https://pub.example/{key})"

    def it_refuses_to_overwrite_and_uploads_nothing(tmp_path, fake_r2):
        shot = tmp_path / "412-review-01.png"
        shot.write_bytes(b"png")
        key = upload.object_key(shot, datetime.datetime.now(datetime.UTC).date())
        client = fake_r2(_FakeS3(existing={key}))
        with pytest.raises(SystemExit) as exit_info:
            upload.main([str(shot)])
        assert "never overwrites" in str(exit_info.value)
        assert client.uploads == []


def _git_common_dir(monkeypatch, common: str) -> None:
    """Answer the script's one git call, ``rev-parse --git-common-dir``, with ``common``."""

    def fake_run(args, **kwargs):
        assert args == ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"]
        return subprocess.CompletedProcess(args, 0, stdout=f"{common}\n", stderr="")

    monkeypatch.setattr(upload.subprocess, "run", fake_run)


@pytest.fixture
def clean_env(monkeypatch):
    for key in upload._KEYS:
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


def describe_env_files():
    def it_reads_this_checkout_then_the_main_checkout_of_a_worktree(tmp_path, monkeypatch):
        worktree, main = tmp_path / "wt", tmp_path / "main"
        worktree.mkdir()
        (main / ".git").mkdir(parents=True)
        (worktree / ".env").write_text("")
        (main / ".env").write_text("")
        monkeypatch.setattr(upload, "_REPO_ROOT", worktree)
        _git_common_dir(monkeypatch, str(main / ".git"))
        assert upload._env_files() == [worktree / ".env", main / ".env"]

    def it_skips_files_that_do_not_exist_and_an_empty_git_answer(tmp_path, monkeypatch):
        monkeypatch.setattr(upload, "_REPO_ROOT", tmp_path)
        _git_common_dir(monkeypatch, "")
        assert upload._env_files() == []


def describe_settings():
    def it_lets_this_checkout_beat_the_main_one_and_the_environment_beat_both(tmp_path, clean_env):
        worktree, main = tmp_path / "wt", tmp_path / "main"
        worktree.mkdir()
        main.mkdir()
        (main / ".env").write_text("\n".join(f"{key}=main" for key in upload._KEYS))
        (worktree / ".env").write_text("R2_BUCKET_NAME=worktree\n")
        clean_env.setattr(upload, "_env_files", lambda: [worktree / ".env", main / ".env"])
        clean_env.setenv("R2_PUBLIC_URL", "https://from-env")
        found = upload.settings()
        assert found["R2_ACCOUNT_ID"] == "main"
        assert found["R2_BUCKET_NAME"] == "worktree"
        assert found["R2_PUBLIC_URL"] == "https://from-env"

    def it_exits_naming_every_missing_key(clean_env):
        clean_env.setattr(upload, "_env_files", lambda: [])
        clean_env.setenv("R2_BUCKET_NAME", "plfog")
        with pytest.raises(SystemExit) as exit_info:
            upload.settings()
        message = str(exit_info.value)
        assert "R2_ACCOUNT_ID" in message and "R2_PUBLIC_URL" in message
        assert "R2_BUCKET_NAME" not in message
        assert "drag the image" in message


def describe_running_the_file():
    def it_runs_main_from_the_command_line(tmp_path, monkeypatch):
        notes = tmp_path / "notes.txt"
        notes.write_text("hi")
        monkeypatch.setattr(sys, "argv", ["upload_screenshot.py", str(notes)])
        with pytest.raises(SystemExit) as exit_info:
            runpy.run_path(str(_SCRIPT), run_name="__main__")
        assert "not an image file" in str(exit_info.value)


#: The eight byte PNG signature and a little body: what the sniff reads before the size is weighed.
_PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 64
_JPEG = b"\xff\xd8\xff" + b"\0" * 64


def _steps(tmp_path, count: int = 2) -> list[pathlib.Path]:
    paths = []
    for number in range(1, count + 1):
        path = tmp_path / f"step-{number}.png"
        path.write_bytes(_PNG)
        paths.append(path)
    return paths


def describe_guidance_shots():
    """``--guidance <slug>`` (#1050): a walkthrough's pictures under guidance/<slug>/step-N.png, outside the 30-day rule."""

    def it_uploads_each_step_under_the_fragments_prefix_and_prints_the_markdown_and_the_toml(tmp_path, fake_r2, capsys):
        client = fake_r2(_FakeS3())
        upload.main(["--guidance", "1050-guidance-shots", *map(str, _steps(tmp_path))])
        assert client.uploads == [
            ("plfog", "guidance/1050-guidance-shots/step-1.png", "image/png"),
            ("plfog", "guidance/1050-guidance-shots/step-2.png", "image/png"),
        ]
        out = capsys.readouterr().out
        assert out.startswith(
            "### Step 1\n\n![step-1](https://pub.example/guidance/1050-guidance-shots/step-1.png)\n\n"
            "### Step 2\n\n![step-2](https://pub.example/guidance/1050-guidance-shots/step-2.png)\n"
        )
        assert out.rstrip().endswith('guidance = "shots"\n\n[[shots]]\ncaption = ""\n\n[[shots]]\ncaption = ""')

    def it_takes_the_steps_in_any_order_and_uploads_them_by_number(tmp_path, fake_r2):
        one, two = _steps(tmp_path)
        client = fake_r2(_FakeS3())
        upload.main(["--guidance", "slug-x", str(two), str(one)])
        assert [key for _, key, _ in client.uploads] == ["guidance/slug-x/step-1.png", "guidance/slug-x/step-2.png"]

    def it_refuses_a_second_run_without_replace_and_uploads_nothing(tmp_path, fake_r2):
        client = fake_r2(_FakeS3(existing={"guidance/1050-guidance-shots/step-2.png"}))
        with pytest.raises(SystemExit) as exit_info:
            upload.main(["--guidance", "1050-guidance-shots", *map(str, _steps(tmp_path))])
        message = str(exit_info.value)
        assert "guidance/1050-guidance-shots/step-2.png already exists" in message and "--replace" in message
        assert client.uploads == []

    def it_replaces_a_remake_when_told(tmp_path, fake_r2):
        client = fake_r2(_FakeS3(existing={"guidance/1050-guidance-shots/step-1.png"}))
        upload.main(["--guidance", "1050-guidance-shots", "--replace", *map(str, _steps(tmp_path))])
        assert len(client.uploads) == 2

    def it_refuses_a_file_over_500_kb_with_its_size(tmp_path):
        one, two = _steps(tmp_path)
        two.write_bytes(_PNG + b"\0" * (600 * 1024))
        with pytest.raises(SystemExit) as exit_info:
            upload.main(["--guidance", "slug-x", str(one), str(two)])
        message = str(exit_info.value)
        assert "step-2.png is 600 KB" in message and "at most 500 KB" in message

    def it_refuses_a_jpg_with_its_type(tmp_path):
        jpg = tmp_path / "step-1.jpg"
        jpg.write_bytes(_JPEG)
        with pytest.raises(SystemExit) as exit_info:
            upload.main(["--guidance", "slug-x", str(jpg)])
        assert "step-1.jpg is image/jpeg, not a PNG" in str(exit_info.value)

    def it_refuses_a_png_whose_bytes_are_not_a_png(tmp_path):
        fake = tmp_path / "step-1.png"
        fake.write_bytes(_JPEG)
        with pytest.raises(SystemExit) as exit_info:
            upload.main(["--guidance", "slug-x", str(fake)])
        assert "step-1.png is image/jpeg, not a PNG" in str(exit_info.value)

    def it_refuses_names_that_are_not_steps_running_from_1(tmp_path):
        one, _two = _steps(tmp_path)
        three = tmp_path / "step-3.png"
        three.write_bytes(_PNG)
        with pytest.raises(SystemExit) as exit_info:
            upload.main(["--guidance", "slug-x", str(one), str(three)])
        assert "none missing or repeated (got step-1.png, step-3.png)" in str(exit_info.value)
        home = tmp_path / "home.png"
        home.write_bytes(_PNG)
        with pytest.raises(SystemExit) as exit_info:
            upload.main(["--guidance", "slug-x", str(home)])
        assert "home.png is not a step name" in str(exit_info.value)

    def it_refuses_a_slug_outside_the_fragment_grammar(tmp_path):
        with pytest.raises(SystemExit) as exit_info:
            upload.main(["--guidance", "Bad_Slug", *map(str, _steps(tmp_path))])
        assert "'Bad_Slug' is not a fragment slug" in str(exit_info.value)

    def it_refuses_replace_without_guidance(tmp_path, capsys):
        (one,) = _steps(tmp_path, 1)
        with pytest.raises(SystemExit) as exit_info:
            upload.main(["--replace", str(one)])
        assert exit_info.value.code == 2
        assert "--replace only applies with --guidance" in capsys.readouterr().err

    def it_sniffs_webp_and_gif_and_falls_back_to_the_name(tmp_path):
        webp = tmp_path / "a.webp"
        webp.write_bytes(b"RIFF" + b"\0" * 4 + b"WEBP" + b"\0" * 8)
        gif = tmp_path / "b.gif"
        gif.write_bytes(b"GIF89a" + b"\0" * 8)
        unknown = tmp_path / "c.svg"
        unknown.write_bytes(b"<svg/>")
        assert upload.sniff_type(webp) == "image/webp"
        assert upload.sniff_type(gif) == "image/gif"
        assert upload.sniff_type(unknown) == "image/svg+xml"
