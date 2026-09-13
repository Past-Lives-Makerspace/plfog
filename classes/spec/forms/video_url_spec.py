"""BDD specs for the video link on every class form (#368 item 8).

All three forms share one validator, so all three take the same providers and give
the same refusal. The refusal has to name every provider, because a member who pasted
an unsupported link has no other way to learn what would work.
"""

from __future__ import annotations

import pytest

from classes.forms import ClassOfferingForm, TeachClassOfferingForm, TeachPublishedClassForm
from classes.video_providers import unsupported_video_message

FORMS = [ClassOfferingForm, TeachClassOfferingForm, TeachPublishedClassForm]


def _errors(form_class, url: str) -> list[str]:
    form = form_class(data={"video_url": url})
    form.is_valid()  # the rest of the form is empty on purpose; this field is the subject
    return form.errors.get("video_url", [])


def _cleaned(form_class, url: str) -> str:
    form = form_class(data={"video_url": url})
    form.is_valid()
    assert "video_url" not in form.errors, form.errors["video_url"]
    return form.cleaned_data["video_url"]


def describe_class_form_video_url():
    @pytest.mark.parametrize("form_class", FORMS, ids=lambda cls: cls.__name__)
    @pytest.mark.parametrize(
        "url",
        [
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://www.instagram.com/reel/CxYzAbCdEfG/",
            "https://www.facebook.com/watch/?v=1234567890",
            "https://fb.watch/aBcD1234ef/",
        ],
        ids=lambda url: url,
    )
    def it_accepts_a_link_from_every_provider(form_class, url, db):
        assert _cleaned(form_class, url) == url

    @pytest.mark.parametrize("form_class", FORMS, ids=lambda cls: cls.__name__)
    def it_refuses_a_provider_nobody_supports(form_class, db):
        assert _errors(form_class, "https://vimeo.com/12345") == [unsupported_video_message()]

    @pytest.mark.parametrize("form_class", FORMS, ids=lambda cls: cls.__name__)
    def it_refuses_a_lookalike_host(form_class, db):
        assert _errors(form_class, "https://instagram.com.evil.test/reel/CxYzAbCdEfG/") == [unsupported_video_message()]

    @pytest.mark.parametrize("form_class", FORMS, ids=lambda cls: cls.__name__)
    def it_lets_a_blank_link_through(form_class, db):
        assert _errors(form_class, "") == []

    def it_names_all_three_providers_in_the_refusal(db):
        message = unsupported_video_message()
        assert "YouTube" in message and "Instagram" in message and "Facebook" in message

    def it_says_which_providers_the_field_takes_in_the_help_text(db):
        from classes.models import ClassOffering

        help_text = ClassOffering._meta.get_field("video_url").help_text
        assert "YouTube" in help_text and "Instagram" in help_text and "Facebook" in help_text
