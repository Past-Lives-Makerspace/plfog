"""Specs for the TEMPORARY copy-review comment form (remove on/after 2026-08-10)."""

from __future__ import annotations

from core.forms import CopyReviewCommentForm


def _data(**overrides: str) -> dict[str, str]:
    base = {"section": "public--overview", "author_name": "Robin", "body": "Looks good."}
    base.update(overrides)
    return base


def describe_CopyReviewCommentForm():
    def it_is_valid_with_all_fields_and_strips_whitespace():
        form = CopyReviewCommentForm(data=_data(author_name="  Robin  ", body="  Hi there  "))
        assert form.is_valid()
        assert form.cleaned_data["author_name"] == "Robin"
        assert form.cleaned_data["body"] == "Hi there"
        assert form.cleaned_data["section"] == "public--overview"

    def describe_blank_section():
        def it_is_invalid():
            form = CopyReviewCommentForm(data=_data(section="   "))
            assert not form.is_valid()
            assert "section" in form.errors

    def describe_blank_author_name():
        def it_is_invalid():
            form = CopyReviewCommentForm(data=_data(author_name="   "))
            assert not form.is_valid()
            assert "author_name" in form.errors

    def describe_blank_body():
        def it_is_invalid():
            form = CopyReviewCommentForm(data=_data(body="   "))
            assert not form.is_valid()
            assert "body" in form.errors

    def describe_body_over_2000_chars():
        def it_is_invalid():
            form = CopyReviewCommentForm(data=_data(body="x" * 2001))
            assert not form.is_valid()
            assert "body" in form.errors

    def describe_author_name_over_80_chars():
        def it_is_invalid():
            form = CopyReviewCommentForm(data=_data(author_name="x" * 81))
            assert not form.is_valid()
            assert "author_name" in form.errors

    def describe_section_over_200_chars():
        def it_is_invalid():
            form = CopyReviewCommentForm(data=_data(section="x" * 201))
            assert not form.is_valid()
            assert "section" in form.errors
