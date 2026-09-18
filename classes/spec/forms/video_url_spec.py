"""BDD specs for the video link on every class form (#368 item 8, #391).

All three forms share one validator, so all three take the same providers and give
the same refusal. The refusal has to name every provider, because a member who pasted
an unsupported link has no other way to learn what would work.

They also share one rendered control. ``forms.URLField`` carries ``assume_scheme="https"``,
so every one of them accepts ``youtube.com/watch?v=…`` typed bare and saves it as https.
``<input type="url">`` refuses that same string in the browser, so none of the three may
render one: the control would gate input the very next save accepts.
"""

from __future__ import annotations

import re

import pytest
from django.urls import reverse

from classes.factories import ClassOfferingFactory, InstructorFactory, UserFactory
from classes.forms import ClassOfferingForm, TeachClassOfferingForm, TeachPublishedClassForm
from classes.models import ClassOffering
from classes.video_providers import unsupported_video_message

FORMS = [ClassOfferingForm, TeachClassOfferingForm, TeachPublishedClassForm]

SCHEMELESS = "youtube.com/watch?v=dQw4w9WgXcQ"


def _video_input(html: str) -> str:
    """The one unprefixed ``video_url`` control in a rendered page.

    Exactly one: the FAQ formset on the same page carries no URL field, and any formset
    control would be prefixed anyway. Asserting the count keeps this from passing because
    the selector found nothing.
    """
    tags = [tag for tag in re.findall(r"<input[^>]*>", html) if 'name="video_url"' in tag]
    assert len(tags) == 1, f"expected one video_url control, found {len(tags)}"
    return tags[0]


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
    def it_refuses_a_backslash_authority_that_browsers_read_as_another_host(form_class, db):
        # URLField passes this through untouched and Python's urlsplit calls the host
        # instagram.com, while a browser navigates to evil.test. The card would carry
        # Instagram's name over somebody else's page, so clean_video_url is the gate.
        assert _errors(form_class, r"https://evil.test\@www.instagram.com/reel/CxYzAbCdEfG/") == [
            unsupported_video_message()
        ]

    @pytest.mark.parametrize("form_class", FORMS, ids=lambda cls: cls.__name__)
    def it_lets_a_blank_link_through(form_class, db):
        assert _errors(form_class, "") == []

    def it_names_all_three_providers_in_the_refusal(db):
        message = unsupported_video_message()
        assert "YouTube" in message and "Instagram" in message and "Facebook" in message

    def it_says_which_providers_the_field_takes_in_the_help_text(db):
        help_text = ClassOffering._meta.get_field("video_url").help_text
        assert "YouTube" in help_text and "Instagram" in help_text and "Facebook" in help_text


def describe_class_form_video_url_control():
    @pytest.mark.parametrize("form_class", FORMS, ids=lambda cls: cls.__name__)
    def it_never_renders_a_url_input(form_class, db):
        # The browser's own check on type="url" is inverted against this field on both ends:
        # it refuses youtube.com/watch?v=… that the server normalises and saves, and it waves
        # through ftp://… that validate_video_url refuses. Dropping the type is what stops it
        # gating; inputmode keeps the URL keyboard on a phone.
        widget = form_class().fields["video_url"].widget
        assert widget.input_type == "text", widget.input_type
        assert widget.attrs.get("inputmode") == "url"

    @pytest.mark.parametrize("form_class", FORMS, ids=lambda cls: cls.__name__)
    def it_takes_the_link_the_control_no_longer_refuses(form_class, db):
        # The other side of the same rule: the string the browser used to block is the one
        # the server accepts, and it is saved with the scheme filled in.
        assert _cleaned(form_class, SCHEMELESS) == f"https://{SCHEMELESS}"

    def it_never_renders_a_url_input_on_the_published_class_page(db, client):
        # The published page is the one surface the composer's fix (#390) did not reach: it
        # renders TeachPublishedClassForm on its own template, outside the composer's panes.
        instructor = InstructorFactory(user=UserFactory(username="pub-video@example.com"))
        offering = ClassOfferingFactory(instructor=instructor, status=ClassOffering.Status.PUBLISHED)
        client.force_login(instructor.user)
        html = client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})).content.decode()
        control = _video_input(html)
        assert 'type="url"' not in control, control
        assert 'inputmode="url"' in control, control
