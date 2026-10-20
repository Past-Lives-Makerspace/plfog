"""BDD specs for ``scripts/upload_screenshot.py``.

The upload itself talks to R2 and is not exercised here; what is pinned is where an object
lands (only under ``pr-screenshots/``) and that a non-image never reaches the bucket.
"""

from __future__ import annotations

import datetime
import importlib.util
import pathlib
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
