"""BDD specs for TeachingApplicationForm, the application page behind I'm Interested.

Contact fields (#536): the form, not the view, checks the detail against the method:
Email runs Django's email validator; Text message and Phone call need at least seven
digits, however the number is punctuated. What the member typed is what comes out of
``cleaned_data``. The application page's questions (#793): which are required, the
rules between them (where they taught past a first timer, an address only on Yes),
``answers()`` for ``Member.apply_to_teach`` and ``initial_for`` for asking again.
"""

from __future__ import annotations

import pytest

from classes.forms import TeachingApplicationForm
from membership.models import Member
from tests.membership.factories import MemberFactory

NOTE = "Two hour intro to wheel throwing."
MISSING_METHOD_ERROR = "Pick how you would like us to reach you."
MISSING_DETAIL_ERROR = "Tell us where to reach you: an email address or a phone number."
BAD_EMAIL_ERROR = "That does not look like an email address. Check it and try again."
BAD_PHONE_ERROR = "That does not look like a phone number. It needs at least seven digits."


# A complete application with every optional answer left out.
VALID: dict[str, object] = {
    "class_title": "Wheel Throwing 101",
    "note": NOTE,
    "class_format": "one_time",
    "practice_years": "3_to_10",
    "experience": "first_time",
    "class_size": "5_to_8",
    "availability": ["weekends"],
    "has_website": "no",
    "has_socials": "no",
    "contact_method": "email",
    "contact_detail": "r@example.com",
}


def _form(**overrides: object) -> TeachingApplicationForm:
    return TeachingApplicationForm({**VALID, **overrides})


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
        form = _form(note="  ")
        assert not form.is_valid()
        assert list(form.errors) == ["note"]


def describe_the_inquiry_questions():
    """#690: experience is a required fixed list; website and socials are optional."""

    def it_offers_the_four_levels_as_chips_with_no_blank_pick():
        choices = TeachingApplicationForm().fields["experience"].choices
        assert list(choices) == [
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
        form = _form(has_website="yes", website="robin.example")
        assert form.is_valid(), form.errors
        assert form.cleaned_data["website"] == "https://robin.example"

    def it_refuses_a_website_that_is_not_one():
        form = _form(has_website="yes", website="not a site")
        assert not form.is_valid()
        assert form.errors["website"] == ["That does not look like a web address. Check it and try again."]

    @pytest.mark.parametrize("address", ["ftp://files.example.com", "ftps://x.example.com"])
    def it_refuses_a_website_the_application_cannot_store(address: str):
        form = _form(has_website="yes", website=address)
        assert not form.is_valid()
        assert form.errors["website"] == ["That does not look like a web address. Check it and try again."]

    def it_refuses_socials_over_500_characters():
        form = _form(has_socials="yes", socials="@" * 501)
        assert not form.is_valid()
        assert form.errors["socials"] == ["That is longer than we can store. Keep it to 500 characters or fewer."]


def describe_the_application_page_questions():
    """#793: the class idea, experience, logistics and past work."""

    def it_accepts_a_complete_application_with_every_optional_answer_blank():
        form = _form()
        assert form.is_valid(), form.errors
        for optional in ("shop_area", "taught_where", "certifications", "space_needs", "anything_else"):
            assert form.cleaned_data[optional] == ""

    @pytest.mark.parametrize(
        ("field", "error"),
        [
            ("class_title", "Give the class a working title."),
            ("class_format", "Pick the format you have in mind."),
            ("practice_years", "Pick how long you have practiced this craft."),
            ("class_size", "Pick a class size you would be comfortable with."),
            ("availability", "Pick at least one time that usually works."),
            ("has_website", "Tell us whether you have a website."),
            ("has_socials", "Tell us whether you share your work on social media."),
        ],
    )
    def it_requires_each_question_that_is_not_optional(field, error):
        data = {**VALID}
        del data[field]
        form = TeachingApplicationForm(data)
        assert not form.is_valid()
        assert form.errors[field] == [error]

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("class_format", "weekly"),
            ("practice_years", "forever"),
            ("class_size", "50"),
            ("has_website", "maybe"),
            ("has_socials", "maybe"),
        ],
    )
    def it_refuses_a_choice_it_does_not_offer(field, value):
        form = _form(**{field: value})
        assert not form.is_valid()
        assert field in form.errors

    def it_refuses_an_unknown_time_slot():
        form = _form(availability=["weekends", "midnight"])
        assert not form.is_valid()
        assert form.errors["availability"] == ["Pick from the times listed."]

    def it_takes_any_number_of_time_slots():
        form = _form(availability=["weekday_daytime", "weekday_evenings", "weekends"])
        assert form.is_valid(), form.errors
        assert form.cleaned_data["availability"] == ["weekday_daytime", "weekday_evenings", "weekends"]

    def it_caps_the_title_at_120_characters():
        form = _form(class_title="x" * 121)
        assert not form.is_valid()
        assert form.errors["class_title"] == ["Keep the title to 120 characters or fewer."]

    def it_offers_the_choices_in_the_order_the_issue_lists_them():
        fields = TeachingApplicationForm().fields
        assert [label for _, label in fields["class_format"].choices] == [
            "One time workshop",
            "A series of sessions",
            "Not sure yet",
        ]
        assert [label for _, label in fields["practice_years"].choices] == [
            "Under a year",
            "1 to 3 years",
            "3 to 10 years",
            "Over 10 years",
        ]
        assert [label for _, label in fields["class_size"].choices] == ["1 to 4", "5 to 8", "9 to 12", "More than 12"]
        assert [label for _, label in fields["availability"].choices] == [
            "Weekday daytime",
            "Weekday evenings",
            "Weekends",
        ]

    def it_ties_each_chip_group_to_the_alpine_property_the_page_reads():
        fields = TeachingApplicationForm().fields
        models = {name: fields[name].widget.attrs.get("x-model") for name in fields}
        assert models["experience"] == "experience"
        assert models["has_website"] == "hasWebsite"
        assert models["has_socials"] == "hasSocials"


def describe_where_they_have_taught():
    @pytest.mark.parametrize("level", ["informal", "a_few", "experienced"])
    def it_is_required_past_a_first_time_teacher(level):
        form = _form(experience=level)
        assert not form.is_valid()
        assert form.errors["taught_where"] == ["Tell us where you have taught before."]

    @pytest.mark.parametrize("level", ["informal", "a_few", "experienced"])
    def it_keeps_the_answer_past_a_first_time_teacher(level):
        form = _form(experience=level, taught_where="The community college, two terms.")
        assert form.is_valid(), form.errors
        assert form.cleaned_data["taught_where"] == "The community college, two terms."

    def it_is_not_asked_of_a_first_time_teacher_and_drops_anything_typed():
        form = _form(experience="first_time", taught_where="x" * 1001)
        assert form.is_valid(), form.errors
        assert form.cleaned_data["taught_where"] == ""

    def it_adds_no_second_error_when_the_level_is_missing():
        form = _form(experience="")
        assert not form.is_valid()
        assert "taught_where" not in form.errors


def describe_the_website_and_socials_switches():
    @pytest.mark.parametrize(
        ("switch", "field", "error"),
        [
            ("has_website", "website", "Add your website's address, or pick No."),
            ("has_socials", "socials", "Add your handles or links, or pick No."),
        ],
    )
    def it_needs_the_answer_on_yes(switch, field, error):
        form = _form(**{switch: "yes", field: ""})
        assert not form.is_valid()
        assert form.errors[field] == [error]
        assert list(form.errors) == [field]  # every other answer stands

    @pytest.mark.parametrize(
        ("switch", "field", "typed"),
        [("has_website", "website", "https://robin.example"), ("has_socials", "socials", "@robinmakes")],
    )
    def it_drops_a_typed_answer_on_no(switch, field, typed):
        form = _form(**{switch: "no", field: typed})
        assert form.is_valid(), form.errors
        assert form.cleaned_data[field] == ""

    def it_drops_a_broken_address_on_no_with_its_error():
        form = _form(has_website="no", website="not a site")
        assert form.is_valid(), form.errors
        assert form.cleaned_data["website"] == ""

    def it_keeps_the_address_error_on_yes_rather_than_adding_a_second():
        form = _form(has_website="yes", website="not a site")
        assert not form.is_valid()
        assert form.errors["website"] == ["That does not look like a web address. Check it and try again."]

    def it_keeps_both_answers_on_yes():
        form = _form(has_website="yes", website="https://robin.example", has_socials="yes", socials="@robinmakes")
        assert form.is_valid(), form.errors
        assert (form.cleaned_data["website"], form.cleaned_data["socials"]) == ("https://robin.example", "@robinmakes")


def describe_answers():
    def it_returns_the_apply_to_teach_keywords_and_not_the_switches():
        form = _form(
            experience="a_few",
            taught_where="Libraries.",
            has_website="yes",
            website="https://robin.example",
            shop_area="Ceramics",
            certifications="First aid.",
            space_needs="Two wheels.",
            anything_else="Mornings are best.",
        )
        assert form.is_valid(), form.errors
        assert form.answers() == {
            "note": NOTE,
            "contact_method": "email",
            "contact_detail": "r@example.com",
            "website": "https://robin.example",
            "socials": "",
            "experience": "a_few",
            "class_title": "Wheel Throwing 101",
            "class_format": "one_time",
            "shop_area": "Ceramics",
            "practice_years": "3_to_10",
            "taught_where": "Libraries.",
            "certifications": "First aid.",
            "space_needs": "Two wheels.",
            "class_size": "5_to_8",
            "availability": ["weekends"],
            "anything_else": "Mornings are best.",
        }


def describe_initial_for():
    def it_is_blank_for_a_member_who_never_applied(db):
        initial = TeachingApplicationForm.initial_for(MemberFactory())
        assert initial["class_title"] == ""
        assert initial["availability"] == []
        assert (initial["has_website"], initial["has_socials"]) == ("", "")

    def it_starts_from_the_last_answers_and_says_yes_where_an_address_was_given(db):
        member = MemberFactory(
            teaching_class_title="Wheel Throwing 101",
            teaching_application_note=NOTE,
            teaching_class_format=Member.TeachingClassFormat.SERIES,
            teaching_availability=["weekends"],
            teaching_website="https://robin.example",
            teaching_contact_method="text",
            teaching_contact_detail="503 555 0100",
        )
        initial = TeachingApplicationForm.initial_for(member)
        assert initial["class_title"] == "Wheel Throwing 101"
        assert initial["note"] == NOTE
        assert initial["class_format"] == "series"
        assert initial["availability"] == ["weekends"]
        assert (initial["has_website"], initial["website"]) == ("yes", "https://robin.example")
        assert (initial["has_socials"], initial["socials"]) == ("", "")
        assert (initial["contact_method"], initial["contact_detail"]) == ("text", "503 555 0100")
