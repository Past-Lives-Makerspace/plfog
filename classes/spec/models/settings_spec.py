"""BDD specs for ClassSettings singleton, including the Host a Workshop page's copy."""

from __future__ import annotations

from django.utils.safestring import SafeString

from classes.models import (
    DEFAULT_LIABILITY_TEXT,
    DEFAULT_MODEL_RELEASE_TEXT,
    DEFAULT_TEACH_PAGE_CTA_LINE,
    DEFAULT_TEACH_PAGE_CTA_TITLE,
    DEFAULT_TEACH_PAGE_EXPECTATIONS,
    DEFAULT_TEACH_PAGE_FAQ,
    DEFAULT_TEACH_PAGE_FEATURES,
    DEFAULT_TEACH_PAGE_HOW_IT_WORKS,
    DEFAULT_TEACH_PAGE_LEAD,
    DEFAULT_TEACH_PAGE_SPLIT_NOTE,
    DEFAULT_TEACH_PAGE_TITLE,
    TEACH_PAGE_FEATURE_ICONS,
    ClassSettings,
    FaqItem,
    FeatureCard,
)


def describe_ClassSettings():
    def describe_load():
        def it_creates_singleton_on_first_call(db):
            settings = ClassSettings.load()
            assert settings.pk == 1

        def it_returns_same_instance_on_repeat_call(db):
            a = ClassSettings.load()
            b = ClassSettings.load()
            assert a.pk == b.pk == 1

        def it_seeds_default_waiver_text(db):
            settings = ClassSettings.load()
            assert settings.liability_waiver_text == DEFAULT_LIABILITY_TEXT
            assert settings.model_release_waiver_text == DEFAULT_MODEL_RELEASE_TEXT

        def it_has_sensible_defaults(db):
            settings = ClassSettings.load()
            assert settings.default_member_discount_pct == 10
            assert settings.reminder_hours_before == 24
            assert settings.instructor_approval_required is True

    def describe_save():
        def it_forces_pk_to_one(db):
            ClassSettings.objects.create(pk=99, liability_waiver_text="x", model_release_waiver_text="y")
            assert ClassSettings.objects.count() == 1
            assert ClassSettings.objects.first().pk == 1

        def it_creates_row_when_none_exists(db):
            ClassSettings.objects.all().delete()
            ClassSettings.objects.create(liability_waiver_text="fresh", model_release_waiver_text="fresh")
            assert ClassSettings.objects.count() == 1
            assert ClassSettings.objects.first().pk == 1

    def it_stringifies_as_class_settings(db):
        assert str(ClassSettings.load()) == "Class Settings"

    def describe_teach_page_defaults():
        """A fresh row carries the whole Host a Workshop page; no seed command, no data migration."""

        def it_carries_the_hero_and_bottom_card_copy(db):
            settings = ClassSettings.load()
            assert settings.teach_page_title == DEFAULT_TEACH_PAGE_TITLE == "Share What You Love"
            assert settings.teach_page_lead == DEFAULT_TEACH_PAGE_LEAD
            assert settings.teach_page_lead.startswith("Run a workshop or a class for the people already in the shop.")
            assert settings.teach_page_cta_title == DEFAULT_TEACH_PAGE_CTA_TITLE == "Got Something to Share?"
            assert settings.teach_page_cta_line == DEFAULT_TEACH_PAGE_CTA_LINE
            assert settings.teach_page_cta_line == "Tell us what you have in mind and an admin will take it from there."

        def it_carries_the_prose_sections(db):
            """The three prose defaults are the HTML the rich editor would save, not Markdown."""
            settings = ClassSettings.load()
            assert settings.teach_page_features == DEFAULT_TEACH_PAGE_FEATURES
            assert settings.teach_page_how_it_works == DEFAULT_TEACH_PAGE_HOW_IT_WORKS
            assert settings.teach_page_expectations == DEFAULT_TEACH_PAGE_EXPECTATIONS
            assert settings.teach_page_faq == DEFAULT_TEACH_PAGE_FAQ
            assert settings.teach_page_how_it_works.startswith("<ol><li><strong>")
            assert settings.teach_page_expectations.startswith("<ul><li>")
            assert settings.teach_page_faq.startswith("<h3>")
            for value in (settings.teach_page_how_it_works, settings.teach_page_expectations, settings.teach_page_faq):
                assert "**" not in value and "### " not in value and "\n- " not in value

        def it_parses_six_feature_cards(db):
            cards = ClassSettings.load().teach_page_feature_cards()
            assert [card.title for card in cards] == [
                "A Page Worth Sharing",
                "Your Words, Your Photos",
                "Sign Ups That Run Themselves",
                "Everyone On One Screen",
                "Free, Paid, Or On Sale",
                "Run It Again In One Click",
            ]
            assert cards[0] == FeatureCard(
                title="A Page Worth Sharing",
                description=(
                    "Your workshop gets its own page with a wide banner photo, a gallery, the schedule, "
                    "your bio, and a sign up panel that follows the reader down the page."
                ),
                icon="page",
            )
            assert [card.icon for card in cards] == list(TEACH_PAGE_FEATURE_ICONS[:6])

        def it_renders_three_numbered_steps(db):
            html = ClassSettings.load().teach_page_how_it_works_html
            assert html.count("<li>") == 3
            assert html.count("<strong>") == 3
            assert "<ol>" in html
            assert "Build your page." in html

        def it_renders_four_bullets(db):
            html = ClassSettings.load().teach_page_expectations_html
            assert html.count("<li>") == 4
            assert "<ul>" in html
            assert "Show up on time and leave the space the way you found it." in html

        def it_renders_four_questions_as_headings(db):
            html = ClassSettings.load().teach_page_faq_html
            assert html.count("<h3>") == 4
            assert "<h3>Do I Need to Be an Expert?</h3>" in html
            assert "<h3>What If Nobody Signs Up?</h3>" in html
            assert html.count("<p>") == 4

        def it_keeps_every_member_facing_default_free_of_dashes(db):
            settings = ClassSettings.load()
            for value in (
                settings.teach_page_title,
                settings.teach_page_lead,
                settings.teach_page_features,
                settings.teach_page_how_it_works,
                settings.teach_page_expectations,
                settings.teach_page_faq,
                settings.teach_page_cta_title,
                settings.teach_page_cta_line,
                settings.teach_page_split_note,
            ):
                assert "—" not in value
                assert " - " not in value

    def describe_money_split():
        def it_defaults_to_seventy_twenty_ten_shown_with_the_note(db):
            settings = ClassSettings.load()
            assert settings.teach_page_split_enabled is True
            assert settings.teach_page_split_instructor_pct == 70
            assert settings.teach_page_split_space_pct == 20
            assert settings.teach_page_split_guild_pct == 10
            assert settings.teach_page_split_note == DEFAULT_TEACH_PAGE_SPLIT_NOTE
            assert DEFAULT_TEACH_PAGE_SPLIT_NOTE == (
                "Every paid class splits the same way. Run it free and there is nothing to split."
            )

    def describe_teach_page_faq_items():
        """The accordion split runs over the SANITIZED HTML, never the raw field."""

        def it_splits_the_default_into_four_items(db):
            items = ClassSettings.load().teach_page_faq_items()
            assert [item.question for item in items] == [
                "Do I Need to Be an Expert?",
                "How Long Until I Hear Back?",
                "Can I Charge for It?",
                "What If Nobody Signs Up?",
            ]
            assert items[3] == FaqItem(
                question="What If Nobody Signs Up?",
                answer_html=(
                    "<p>You can cancel from your dashboard and everyone who signed up is told automatically. "
                    "Nothing is stuck.</p>"
                ),
            )
            assert isinstance(items[0].answer_html, SafeString)
            assert ClassSettings.load().teach_page_faq_intro_html == ""

        def it_keeps_what_comes_before_the_first_heading_as_the_intro():
            settings = ClassSettings(teach_page_faq="<p>Ask away.</p><h3>Q1</h3><p>A1</p><h2>Q2</h2><p>A2</p>")
            assert settings.teach_page_faq_intro_html == "<p>Ask away.</p>"
            assert isinstance(settings.teach_page_faq_intro_html, SafeString)
            assert [(i.question, str(i.answer_html)) for i in settings.teach_page_faq_items()] == [
                ("Q1", "<p>A1</p>"),
                ("Q2", "<p>A2</p>"),
            ]

        def it_yields_no_items_without_headings():
            settings = ClassSettings(teach_page_faq="<p>Just one paragraph.</p>")
            assert settings.teach_page_faq_items() == []
            assert settings.teach_page_faq_intro_html == "<p>Just one paragraph.</p>"
            assert ClassSettings(teach_page_faq="").teach_page_faq_items() == []

        def it_splits_an_older_markdown_value_the_same_way():
            items = ClassSettings(teach_page_faq="### Is It Free?\n\nYes.").teach_page_faq_items()
            assert [(i.question, str(i.answer_html)) for i in items] == [("Is It Free?", "<p>Yes.</p>")]

        def it_strips_a_script_from_a_heading_before_splitting():
            settings = ClassSettings(teach_page_faq='<h3>Q <script>alert("x")</script></h3><p>A</p>')
            items = settings.teach_page_faq_items()
            assert len(items) == 1
            assert "<script" not in items[0].question
            assert items[0].question == 'Q alert("x")'

        def it_unescapes_the_question_text_so_the_template_escapes_it_once():
            items = ClassSettings(teach_page_faq="<h3>Tools &amp; Safety</h3><p>A</p>").teach_page_faq_items()
            assert items[0].question == "Tools & Safety"

        def it_skips_a_heading_with_no_text():
            items = ClassSettings(teach_page_faq="<h3> </h3><p>Orphan</p><h3>Real</h3><p>A</p>").teach_page_faq_items()
            assert [i.question for i in items] == ["Real"]

        def it_skips_a_heading_with_nothing_under_it():
            """Two headings in a row, or a heading bleach split out of another, never make an empty item."""
            items = ClassSettings(
                teach_page_faq="<h3>Empty</h3><h3>Real</h3><p>A</p><h3>Trailing</h3>  "
            ).teach_page_faq_items()
            assert [(i.question, str(i.answer_html)) for i in items] == [("Real", "<p>A</p>")]

    def describe_teach_page_feature_cards():
        """Pure parsing over the field; no row needed."""

        def it_splits_each_line_on_the_first_colon_only():
            settings = ClassSettings(teach_page_features="Hours: 10:00 to 12:00\nRoom: The big one")
            cards = settings.teach_page_feature_cards()
            assert cards[0].title == "Hours"
            assert cards[0].description == "10:00 to 12:00"
            assert cards[1] == FeatureCard(title="Room", description="The big one", icon=TEACH_PAGE_FEATURE_ICONS[1])

        def it_makes_a_title_only_card_from_a_line_without_a_colon():
            cards = ClassSettings(teach_page_features="Just a title").teach_page_feature_cards()
            assert cards == [FeatureCard(title="Just a title", description="", icon=TEACH_PAGE_FEATURE_ICONS[0])]

        def it_skips_blank_lines_and_trims_the_rest():
            cards = ClassSettings(
                teach_page_features="\n  One: first  \n\n   \nTwo: second\n"
            ).teach_page_feature_cards()
            assert [(card.title, card.description) for card in cards] == [("One", "first"), ("Two", "second")]
            assert [card.icon for card in cards] == list(TEACH_PAGE_FEATURE_ICONS[:2])

        def it_wraps_the_icon_past_eight_cards():
            lines = "\n".join(f"Card {n}: text" for n in range(1, 11))
            cards = ClassSettings(teach_page_features=lines).teach_page_feature_cards()
            assert len(cards) == 10
            assert len(TEACH_PAGE_FEATURE_ICONS) == 8
            assert [card.icon for card in cards[:8]] == list(TEACH_PAGE_FEATURE_ICONS)
            assert cards[8].icon == TEACH_PAGE_FEATURE_ICONS[0]
            assert cards[9].icon == TEACH_PAGE_FEATURE_ICONS[1]

        def it_yields_an_empty_list_for_an_empty_field():
            assert ClassSettings(teach_page_features="").teach_page_feature_cards() == []
            assert ClassSettings(teach_page_features="  \n \n").teach_page_feature_cards() == []

    def describe_teach_page_prose_html():
        """The three prose properties are dual mode: editor HTML is sanitized, older Markdown rendered."""

        def it_returns_a_safe_string():
            html = ClassSettings(teach_page_faq="### Q\n\nA").teach_page_faq_html
            assert isinstance(html, SafeString)
            assert html == "<h3>Q</h3>\n<p>A</p>"

        def it_sanitizes_editor_html_and_drops_quill_classes():
            html = ClassSettings(
                teach_page_faq='<h3 class="ql-align-center">Q</h3><p>A</p><script>alert(1)</script>'
            ).teach_page_faq_html
            assert isinstance(html, SafeString)
            assert html == "<h3>Q</h3><p>A</p>alert(1)"

        def it_strips_a_script_tag_and_keeps_the_text():
            html = ClassSettings(
                teach_page_how_it_works="1. Step <script>alert(1)</script> done"
            ).teach_page_how_it_works_html
            assert "<script" not in html
            assert "alert(1)" in html
            assert "<li>" in html

        def it_strips_an_inline_style():
            html = ClassSettings(
                teach_page_expectations='<p style="color:red">Be safe.</p>'
            ).teach_page_expectations_html
            assert "style=" not in html
            assert "<p>Be safe.</p>" in html

        def it_hardens_a_link_per_the_member_profile():
            html = ClassSettings(teach_page_faq="[The guide](https://example.com/guide)").teach_page_faq_html
            assert 'href="https://example.com/guide"' in html
            assert 'rel="noopener nofollow noreferrer"' in html
            assert 'target="_blank"' in html

        def it_renders_an_empty_string_for_a_blank_field():
            settings = ClassSettings(teach_page_how_it_works="", teach_page_expectations="", teach_page_faq="")
            assert settings.teach_page_how_it_works_html == ""
            assert settings.teach_page_expectations_html == ""
            assert settings.teach_page_faq_html == ""
