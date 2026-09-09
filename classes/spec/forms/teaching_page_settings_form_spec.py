"""BDD specs for TeachingPageSettingsForm: the prose seam and the money split rule."""

from __future__ import annotations

from classes.forms import TeachingPageSettingsForm
from classes.models import ClassSettings

SUM_ERROR = "The three shares have to add up to 100."


def _data(**overrides: object) -> dict[str, object]:
    return {
        "teach_page_title": "T",
        "teach_page_split_enabled": "on",
        "teach_page_split_instructor_pct": 70,
        "teach_page_split_space_pct": 20,
        "teach_page_split_guild_pct": 10,
        **overrides,
    }


def describe_TeachingPageSettingsForm():
    def describe_the_money_split():
        def it_is_valid_when_the_shares_add_up_to_100(db):
            form = TeachingPageSettingsForm(_data(), instance=ClassSettings.load())
            assert form.is_valid(), form.errors

        def it_puts_the_sum_error_on_the_host_share(db):
            form = TeachingPageSettingsForm(_data(teach_page_split_guild_pct=15), instance=ClassSettings.load())
            assert not form.is_valid()
            assert form.errors["teach_page_split_instructor_pct"] == [SUM_ERROR]

        def it_skips_the_sum_while_the_section_is_hidden(db):
            data = _data(teach_page_split_guild_pct=15)
            del data["teach_page_split_enabled"]
            form = TeachingPageSettingsForm(data, instance=ClassSettings.load())
            assert form.is_valid(), form.errors

        def it_leaves_a_share_that_failed_its_own_check_with_that_error_only(db):
            form = TeachingPageSettingsForm(_data(teach_page_split_guild_pct=150), instance=ClassSettings.load())
            assert not form.is_valid()
            assert "teach_page_split_instructor_pct" not in form.errors
            assert form.errors["teach_page_split_guild_pct"] == ["Ensure this value is less than or equal to 100."]

    def describe_the_prose_fields():
        def it_sanitizes_editor_html_and_passes_markdown_through(db):
            form = TeachingPageSettingsForm(
                _data(
                    teach_page_how_it_works="<ol><li>Step</li></ol><script>x()</script>",
                    teach_page_expectations="- Be kind.",
                    teach_page_faq="",
                ),
                instance=ClassSettings.load(),
            )
            assert form.is_valid(), form.errors
            assert form.cleaned_data["teach_page_how_it_works"] == "<ol><li>Step</li></ol>x()"
            assert form.cleaned_data["teach_page_expectations"] == "- Be kind."
            assert form.cleaned_data["teach_page_faq"] == ""

        def it_offers_only_published_classes_as_the_example(db):
            from classes.factories import ClassOfferingFactory
            from classes.models import ClassOffering

            published = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED, title="B Live")
            earlier = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED, title="A Live")
            ClassOfferingFactory(status=ClassOffering.Status.DRAFT)
            form = TeachingPageSettingsForm(instance=ClassSettings.load())
            assert list(form.fields["example_class"].queryset) == [earlier, published]
