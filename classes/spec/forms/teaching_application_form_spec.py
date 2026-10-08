"""BDD specs for TeachingApplicationForm's contact fields (issue #536).

The I'm Interested modal asks how to reach the member and where. The form, not the
view, checks the detail against the method: Email runs Django's email validator;
Text message and Phone call need at least seven digits, however the number is
punctuated. What the member typed is what comes out of ``cleaned_data``.
"""

from __future__ import annotations

import pytest

from classes.forms import TeachingApplicationForm

NOTE = "Two hour intro to wheel throwing."
MISSING_METHOD_ERROR = "Pick how you would like us to reach you."
MISSING_DETAIL_ERROR = "Tell us where to reach you: an email address or a phone number."
BAD_EMAIL_ERROR = "That does not look like an email address. Check it and try again."
BAD_PHONE_ERROR = "That does not look like a phone number. It needs at least seven digits."


def _form(**contact: str) -> TeachingApplicationForm:
    return TeachingApplicationForm({"note": NOTE, "experience": "first_time", **contact})


def describe_TeachingApplicationForm():
    def describe_the_method_select():
        def it_offers_a_blank_pick_ahead_of_the_three_methods():
            choices = TeachingApplicationForm().fields["contact_method"].choices
            assert list(choices) == [
                ("", "Pick one"),
                ("email", "Email"),
                ("text", "Text message"),
                ("phone", "Phone call"),
            ]

        def it_requires_a_pick():
            form = _form(contact_method="", contact_detail="503 555 0100")
            assert not form.is_valid()
            assert form.errors["contact_method"] == [MISSING_METHOD_ERROR]
            assert "contact_detail" not in form.errors

        def it_refuses_a_method_it_does_not_offer():
            form = _form(contact_method="fax", contact_detail="503 555 0100")
            assert not form.is_valid()
            assert form.errors["contact_method"] == [MISSING_METHOD_ERROR]

        def it_carries_the_hooks_the_modals_alpine_component_binds():
            form = TeachingApplicationForm()
            assert form.fields["contact_method"].widget.attrs == {
                "x-ref": "method",
                "@change": "pick($event.target.value)",
            }
            assert form.fields["contact_detail"].widget.attrs == {"x-ref": "detail", "maxlength": "254"}

    def describe_the_detail_field():
        def it_is_required():
            form = _form(contact_method="email", contact_detail="   ")
            assert not form.is_valid()
            assert form.errors["contact_detail"] == [MISSING_DETAIL_ERROR]

        def it_caps_the_length_at_254():
            form = _form(contact_method="email", contact_detail="a" * 250 + "@example.com")
            assert not form.is_valid()
            assert "254 characters or fewer" in form.errors["contact_detail"][0]

        def it_reports_only_the_missing_fields_when_both_are_blank():
            form = _form(contact_method="", contact_detail="")
            assert not form.is_valid()
            assert form.errors["contact_method"] == [MISSING_METHOD_ERROR]
            assert form.errors["contact_detail"] == [MISSING_DETAIL_ERROR]

    def describe_when_the_method_is_email():
        def it_accepts_an_email_address():
            form = _form(contact_method="email", contact_detail="robin@example.com")
            assert form.is_valid(), form.errors
            assert form.cleaned_data["contact_detail"] == "robin@example.com"

        @pytest.mark.parametrize("detail", ["robin at example dot com", "503 555 0100", "robin@"])
        def it_refuses_anything_that_is_not_one(detail):
            form = _form(contact_method="email", contact_detail=detail)
            assert not form.is_valid()
            assert form.errors["contact_detail"] == [BAD_EMAIL_ERROR]

    def describe_when_the_method_is_a_phone_number():
        @pytest.mark.parametrize("method", ["text", "phone"])
        @pytest.mark.parametrize(
            "detail", ["5035550100", "503 555 0100", "(503) 555-0100", "+1 503.555.0100", "5550100"]
        )
        def it_accepts_seven_or_more_digits_however_they_are_punctuated(method, detail):
            form = _form(contact_method=method, contact_detail=detail)
            assert form.is_valid(), form.errors
            assert form.cleaned_data["contact_detail"] == detail  # stored as typed

        @pytest.mark.parametrize("method", ["text", "phone"])
        @pytest.mark.parametrize("detail", ["555010", "(503) 55-0", "call me", "robin@example.com"])
        def it_refuses_fewer_than_seven_digits(method, detail):
            form = _form(contact_method=method, contact_detail=detail)
            assert not form.is_valid()
            assert form.errors["contact_detail"] == [BAD_PHONE_ERROR]

    def it_still_requires_the_note():
        form = TeachingApplicationForm(
            {"note": "  ", "contact_method": "email", "contact_detail": "r@example.com", "experience": "informal"}
        )
        assert not form.is_valid()
        assert list(form.errors) == ["note"]


def describe_the_inquiry_questions():
    """#690: experience is a required fixed list; website and socials are optional."""

    def it_offers_a_blank_pick_ahead_of_the_four_levels():
        choices = TeachingApplicationForm().fields["experience"].choices
        assert list(choices) == [
            ("", "Pick one"),
            ("first_time", "First time teaching"),
            ("informal", "Taught informally"),
            ("a_few", "Taught a few classes"),
            ("experienced", "Experienced instructor"),
        ]

    @pytest.mark.parametrize("level", ["", "guru"])
    def it_requires_a_known_level(level):
        form = _form(contact_method="email", contact_detail="r@example.com", experience=level)
        assert not form.is_valid()
        assert form.errors["experience"] == ["Pick how much teaching you have done."]

    def it_accepts_blank_website_and_socials():
        form = _form(contact_method="email", contact_detail="r@example.com")
        assert form.is_valid(), form.errors
        assert (form.cleaned_data["website"], form.cleaned_data["socials"]) == ("", "")

    def it_assumes_https_for_a_bare_domain():
        form = _form(contact_method="email", contact_detail="r@example.com", website="robin.example")
        assert form.is_valid(), form.errors
        assert form.cleaned_data["website"] == "https://robin.example"

    def it_refuses_a_website_that_is_not_one():
        form = _form(contact_method="email", contact_detail="r@example.com", website="not a site")
        assert not form.is_valid()
        assert form.errors["website"] == ["That does not look like a web address. Check it and try again."]

    def it_refuses_socials_over_500_characters():
        form = _form(contact_method="email", contact_detail="r@example.com", socials="@" * 501)
        assert not form.is_valid()
        assert form.errors["socials"] == ["That is longer than we can store. Keep it to 500 characters or fewer."]
