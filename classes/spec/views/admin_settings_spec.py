"""BDD specs for the admin Settings tab, including the Host a Workshop Page section."""

from __future__ import annotations

from django.urls import reverse

from classes.models import ClassSettings

# What an admin would type into every Host a Workshop field, each distinct so a
# round trip proves each one landed in its own column.
TEACH_PAGE_INPUT = {
    "teach_page_title": "Teach Us Something",
    "teach_page_lead": "A lead an admin wrote.",
    "teach_page_features": "One: first\nTwo: second",
    "teach_page_how_it_works": "1. **Ask.** Say hi.\n2. **Build.** Make it.",
    "teach_page_expectations": "- Be kind.\n- Be safe.",
    "teach_page_faq": "### Is It Free?\n\nYes.",
    "teach_page_cta_title": "Ready?",
    "teach_page_cta_line": "Tell us.",
}


def _general_post() -> dict[str, object]:
    return {
        "liability_waiver_text": "LIABILITY",
        "model_release_waiver_text": "MODEL RELEASE",
        "default_member_discount_pct": 10,
        "reminder_hours_before": 24,
        "confirmation_email_footer": "",
    }


def describe_admin_settings():
    def it_shows_settings_form(admin_user, client, db):
        client.force_login(admin_user)
        response = client.get(reverse("classes:admin_settings"))
        assert response.status_code == 200
        assert b"Class Settings" in response.content

    def it_ends_with_a_button_that_just_says_save(admin_user, client, db):
        client.force_login(admin_user)
        content = client.get(reverse("classes:admin_settings")).content.decode()
        assert ">Save</button>" in content
        assert "Save Settings" not in content
        # Rule 21: nothing renders below the Save button.
        assert "<input" not in content.split(">Save</button>")[1]
        assert "<textarea" not in content.split(">Save</button>")[1]

    def it_saves_settings(admin_user, client, db):
        client.force_login(admin_user)
        response = client.post(
            reverse("classes:admin_settings"),
            {
                "liability_waiver_text": "NEW LIABILITY TEXT",
                "model_release_waiver_text": "NEW MODEL RELEASE",
                "default_member_discount_pct": 15,
                "reminder_hours_before": 48,
                "confirmation_email_footer": "",
            },
        )
        assert response.status_code == 302
        from classes.models import ClassSettings

        settings_obj = ClassSettings.load()
        assert settings_obj.default_member_discount_pct == 15

    def it_gates_behind_admin_role(member_user, client, db):
        client.force_login(member_user)
        response = client.get(reverse("classes:admin_settings"))
        assert response.status_code == 403

    def describe_example_class():
        def it_offers_the_teach_page_example_picker(admin_user, client, db):
            client.force_login(admin_user)
            content = client.get(reverse("classes:admin_settings")).content.decode()
            assert 'name="example_class"' in content

        def it_saves_the_chosen_example(admin_user, client, db):
            from classes.factories import ClassOfferingFactory
            from classes.models import ClassOffering, ClassSettings

            offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)
            client.force_login(admin_user)
            response = client.post(
                reverse("classes:admin_settings"),
                {
                    "liability_waiver_text": "LIABILITY",
                    "model_release_waiver_text": "MODEL RELEASE",
                    "default_member_discount_pct": 10,
                    "reminder_hours_before": 24,
                    "example_class": offering.pk,
                    "confirmation_email_footer": "",
                },
            )
            assert response.status_code == 302
            assert ClassSettings.load().example_class_id == offering.pk

        def it_offers_published_classes_and_not_drafts(admin_user, client, db):
            from classes.factories import ClassOfferingFactory
            from classes.models import ClassOffering

            published = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED, title="Live Class")
            draft = ClassOfferingFactory(status=ClassOffering.Status.DRAFT, title="Draft Class")
            archived = ClassOfferingFactory(status=ClassOffering.Status.ARCHIVED, title="Old Class")
            client.force_login(admin_user)
            content = client.get(reverse("classes:admin_settings")).content.decode()
            picker = content.split('name="example_class"')[1].split("</select>")[0]
            assert f'value="{published.pk}"' in picker
            assert f'value="{draft.pk}"' not in picker
            assert f'value="{archived.pk}"' not in picker

        def it_refuses_a_draft_as_the_example(admin_user, client, db):
            from classes.factories import ClassOfferingFactory
            from classes.models import ClassOffering, ClassSettings

            draft = ClassOfferingFactory(status=ClassOffering.Status.DRAFT)
            client.force_login(admin_user)
            response = client.post(
                reverse("classes:admin_settings"),
                {
                    "liability_waiver_text": "LIABILITY",
                    "model_release_waiver_text": "MODEL RELEASE",
                    "default_member_discount_pct": 10,
                    "reminder_hours_before": 24,
                    "example_class": draft.pk,
                    "confirmation_email_footer": "",
                },
            )
            assert response.status_code == 200  # re-rendered with the field error, not saved
            assert "Select a valid choice" in response.content.decode()
            assert ClassSettings.load().example_class_id is None

        def it_keeps_the_stored_pick_across_a_save(admin_user, client, db):
            from classes.factories import ClassOfferingFactory
            from classes.models import ClassOffering, ClassSettings

            offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)
            settings_obj = ClassSettings.load()
            settings_obj.example_class = offering
            settings_obj.save()
            client.force_login(admin_user)
            content = client.get(reverse("classes:admin_settings")).content.decode()
            assert f'<option value="{offering.pk}" selected>' in content
            response = client.post(
                reverse("classes:admin_settings"),
                {
                    "liability_waiver_text": "LIABILITY",
                    "model_release_waiver_text": "MODEL RELEASE",
                    "default_member_discount_pct": 10,
                    "reminder_hours_before": 24,
                    "example_class": offering.pk,
                    "confirmation_email_footer": "",
                },
            )
            assert response.status_code == 302
            assert ClassSettings.load().example_class_id == offering.pk

        def it_leaves_the_example_blank_when_none_is_chosen(admin_user, client, db):
            from classes.models import ClassSettings

            client.force_login(admin_user)
            client.post(
                reverse("classes:admin_settings"),
                {
                    "liability_waiver_text": "LIABILITY",
                    "model_release_waiver_text": "MODEL RELEASE",
                    "default_member_discount_pct": 10,
                    "reminder_hours_before": 24,
                    "example_class": "",
                    "confirmation_email_footer": "",
                },
            )
            assert ClassSettings.load().example_class_id is None

    def describe_host_a_workshop_page():
        def it_shows_the_section_with_every_field_and_a_view_the_page_link(admin_user, client, db):
            client.force_login(admin_user)
            content = client.get(reverse("classes:admin_settings")).content.decode()
            assert "Host a Workshop Page" in content
            for name in TEACH_PAGE_INPUT:
                assert f'name="{name}"' in content
            # example_class moved into this section: it renders after the heading.
            assert content.index('name="example_class"') > content.index("Host a Workshop Page")
            assert "View the Page" in content
            assert f'href="{reverse("classes:teach_why")}"' in content
            assert "until an admin says yes to them." in content
            assert "Markdown works here: **bold**, lists, links." in content
            # The section sits above the one Save button.
            assert content.index("Host a Workshop Page") < content.index(">Save</button>")

        def it_prefills_the_default_copy(admin_user, client, db):
            client.force_login(admin_user)
            content = client.get(reverse("classes:admin_settings")).content.decode()
            assert 'value="Share What You Love"' in content
            assert "A Page Worth Sharing: Your workshop gets its own page" in content
            assert "### Do I Need to Be an Expert?" in content

        def it_round_trips_every_field(admin_user, client, db):
            client.force_login(admin_user)
            response = client.post(reverse("classes:admin_settings"), {**_general_post(), **TEACH_PAGE_INPUT})
            assert response.status_code == 302
            settings_obj = ClassSettings.load()
            for name, value in TEACH_PAGE_INPUT.items():
                assert getattr(settings_obj, name) == value
            assert len(settings_obj.teach_page_feature_cards()) == 2

        def it_accepts_a_blanked_field_and_stores_it_blank(admin_user, client, db):
            client.force_login(admin_user)
            response = client.post(
                reverse("classes:admin_settings"),
                {**_general_post(), **TEACH_PAGE_INPUT, "teach_page_faq": "", "teach_page_title": ""},
            )
            assert response.status_code == 302
            settings_obj = ClassSettings.load()
            assert settings_obj.teach_page_faq == ""
            assert settings_obj.teach_page_title == ""
            assert settings_obj.teach_page_lead == "A lead an admin wrote."

        def it_refuses_a_non_admin_on_get_and_post(member_user, client, db):
            client.force_login(member_user)
            assert client.get(reverse("classes:admin_settings")).status_code == 403
            response = client.post(reverse("classes:admin_settings"), {**_general_post(), **TEACH_PAGE_INPUT})
            assert response.status_code == 403
            assert ClassSettings.load().teach_page_title == "Share What You Love"
