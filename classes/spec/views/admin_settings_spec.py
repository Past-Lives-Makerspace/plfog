"""BDD specs for the admin Waivers & Reminders page (the general class settings).

The Host a Workshop page's words moved to the Teaching Marketing Page
(admin_teaching_page_settings_spec.py); this page is the waivers and reminders form again.
"""

from __future__ import annotations

from django.urls import reverse

from classes.models import ClassSettings

TEACH_PAGE_FIELDS = (
    "teach_page_title",
    "teach_page_lead",
    "teach_page_features",
    "teach_page_how_it_works",
    "teach_page_split_enabled",
    "teach_page_split_instructor_pct",
    "teach_page_split_space_pct",
    "teach_page_split_guild_pct",
    "teach_page_split_note",
    "teach_page_expectations",
    "teach_page_faq",
    "teach_page_cta_title",
    "teach_page_cta_line",
)


def _general_post(**overrides: object) -> dict[str, object]:
    return {
        "liability_waiver_text": "LIABILITY",
        "model_release_waiver_text": "MODEL RELEASE",
        "default_member_discount_pct": 10,
        "reminder_hours_before": 24,
        "confirmation_email_footer": "",
        **overrides,
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

    def it_renders_only_the_general_fields(admin_user, client, db):
        """The Host a Workshop words and the example picker live on their own page now."""
        client.force_login(admin_user)
        page = client.get(reverse("classes:admin_settings")).content.decode()
        # The release notes render on every hub page and mention the new page by name,
        # so the negatives are scoped to the settings form itself.
        content = page[
            page.index("<h2>Class Settings</h2>") : page.index("</form>", page.index("<h2>Class Settings</h2>"))
        ]
        for name in (
            "liability_waiver_text",
            "model_release_waiver_text",
            "default_member_discount_pct",
            "reminder_hours_before",
            "instructor_approval_required",
            "confirmation_email_footer",
        ):
            assert f'name="{name}"' in content
        for name in TEACH_PAGE_FIELDS:
            assert f'name="{name}"' not in content
        assert 'name="example_class"' not in content
        assert "Host a Workshop Page" not in content
        assert "Teaching Marketing Page" not in content
        assert "rich-editor-init.js" not in content

    def it_saves_settings(admin_user, client, db):
        client.force_login(admin_user)
        response = client.post(
            reverse("classes:admin_settings"),
            _general_post(
                liability_waiver_text="NEW LIABILITY TEXT",
                model_release_waiver_text="NEW MODEL RELEASE",
                default_member_discount_pct=15,
                reminder_hours_before=48,
            ),
        )
        assert response.status_code == 302
        settings_obj = ClassSettings.load()
        assert settings_obj.default_member_discount_pct == 15
        assert settings_obj.reminder_hours_before == 48
        assert settings_obj.liability_waiver_text == "NEW LIABILITY TEXT"

    def it_leaves_the_teaching_page_words_alone_when_saving(admin_user, client, db):
        settings_obj = ClassSettings.load()
        settings_obj.teach_page_title = "Kept"
        settings_obj.teach_page_split_guild_pct = 15
        settings_obj.teach_page_split_space_pct = 15
        settings_obj.save()
        client.force_login(admin_user)
        response = client.post(reverse("classes:admin_settings"), _general_post())
        assert response.status_code == 302
        settings_obj = ClassSettings.load()
        assert settings_obj.teach_page_title == "Kept"
        assert settings_obj.teach_page_split_guild_pct == 15
        assert settings_obj.liability_waiver_text == "LIABILITY"

    def it_gates_behind_admin_role(member_user, client, db):
        client.force_login(member_user)
        response = client.get(reverse("classes:admin_settings"))
        assert response.status_code == 403
