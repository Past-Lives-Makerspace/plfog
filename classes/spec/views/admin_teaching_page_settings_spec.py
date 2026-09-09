"""BDD specs for the Teaching Marketing Page under Class Settings.

Every word of the Host a Workshop page plus the money split, edited on its own page with
the rich editor on the three prose fields. A Quill save is sanitized before storage; a
Markdown value (a no JS textarea) passes through unchanged and still renders.
"""

from __future__ import annotations

from django.contrib.messages import get_messages
from django.urls import reverse

from classes.factories import ClassOfferingFactory
from classes.models import DEFAULT_TEACH_PAGE_SPLIT_NOTE, ClassOffering, ClassSettings

URL_NAME = "classes:admin_teaching_page_settings"
SAVED_MESSAGE = "Teaching Marketing Page saved."
SUM_ERROR = "The three shares have to add up to 100."

# What an admin would type into every field, each distinct so a round trip proves each
# one landed in its own column. The prose fields carry HTML, as the editor saves them.
TEACH_PAGE_INPUT = {
    "teach_page_title": "Teach Us Something",
    "teach_page_lead": "A lead an admin wrote.",
    "teach_page_features": "One: first\nTwo: second",
    "teach_page_how_it_works": "<ol><li><strong>Ask.</strong> Say hi.</li><li><strong>Build.</strong> Make it.</li></ol>",
    "teach_page_split_enabled": "on",
    "teach_page_split_instructor_pct": "60",
    "teach_page_split_space_pct": "25",
    "teach_page_split_guild_pct": "15",
    "teach_page_split_note": "A note about the split.",
    "teach_page_expectations": "<ul><li>Be kind.</li><li>Be safe.</li></ul>",
    "teach_page_faq": "<h3>Is It Free?</h3><p>Yes.</p>",
    "teach_page_cta_title": "Ready?",
    "teach_page_cta_line": "Tell us.",
}


def _post(**overrides: str) -> dict[str, str]:
    return {**TEACH_PAGE_INPUT, **overrides}


def describe_admin_teaching_page_settings():
    def it_is_served_under_class_settings(db):
        assert reverse(URL_NAME) == "/classes/admin/settings/teaching-page/"

    def it_renders_the_page_with_the_settings_tab_active(admin_user, client, db):
        client.force_login(admin_user)
        response = client.get(reverse(URL_NAME))
        assert response.status_code == 200
        content = response.content.decode()
        assert "Teaching Marketing Page" in content.split('class="pl-teach-page-settings__title">')[1][:60]
        assert 'vote-tab vote-tab--active">Settings</a>' in content
        assert "until an admin says yes to them." in content
        assert "View the Page" in content
        assert f'href="{reverse("classes:teach_why")}"' in content
        assert "Every field here is one section of the page. Blank a field to hide its section." in content

    def it_renders_every_field_in_page_order_with_one_save_at_the_bottom(admin_user, client, db):
        client.force_login(admin_user)
        content = client.get(reverse(URL_NAME)).content.decode()
        positions = [content.index(f'name="{name}"') for name in TEACH_PAGE_INPUT]
        assert positions == sorted(positions)
        assert content.index('name="example_class"') > positions[-1]
        for heading in (
            "Top of the Page",
            "What You Get",
            "How It Works",
            "Where the Money Goes",
            "What We Ask Of You",
            "Common Questions",
            "Bottom Card",
            "Example Workshop",
        ):
            assert f'class="pl-teach-page-settings__section">{heading}</h3>' in content
        assert ">Save</button>" in content
        after_save = content.split(">Save</button>")[1]
        assert "<input" not in after_save and "<textarea" not in after_save

    def it_mounts_the_rich_editor_on_the_three_prose_fields_only(admin_user, client, db):
        client.force_login(admin_user)
        content = client.get(reverse(URL_NAME)).content.decode()
        assert "rich-editor-init.js" in content
        for name in ("teach_page_how_it_works", "teach_page_expectations", "teach_page_faq"):
            assert f'id="pl-rte-mount-id_{name}"' in content
        assert content.count('data-rte-seed="server"') == 3
        assert content.count('data-rte-toolbar="page"') == 3
        assert 'id="pl-rte-mount-id_teach_page_features"' not in content
        assert '<textarea name="teach_page_features"' in content

    def it_prefills_the_defaults_rendered_into_the_editor(admin_user, client, db):
        client.force_login(admin_user)
        content = client.get(reverse(URL_NAME)).content.decode()
        assert 'value="Share What You Love"' in content
        assert "A Page Worth Sharing: Your workshop gets its own page" in content
        assert "<h3>Do I Need to Be an Expert?</h3>" in content
        assert "<strong>Build your page.</strong>" in content
        assert 'value="70"' in content and 'value="20"' in content and 'value="10"' in content
        assert DEFAULT_TEACH_PAGE_SPLIT_NOTE in content
        assert 'inputmode="numeric"' in content

    def it_round_trips_every_field_including_the_split(admin_user, client, db):
        client.force_login(admin_user)
        response = client.post(reverse(URL_NAME), _post())
        assert response.status_code == 302
        assert response["Location"] == reverse(URL_NAME)
        settings_obj = ClassSettings.load()
        assert settings_obj.teach_page_title == "Teach Us Something"
        assert settings_obj.teach_page_lead == "A lead an admin wrote."
        assert settings_obj.teach_page_features == "One: first\nTwo: second"
        assert settings_obj.teach_page_how_it_works == TEACH_PAGE_INPUT["teach_page_how_it_works"]
        assert settings_obj.teach_page_expectations == TEACH_PAGE_INPUT["teach_page_expectations"]
        assert settings_obj.teach_page_faq == TEACH_PAGE_INPUT["teach_page_faq"]
        assert settings_obj.teach_page_cta_title == "Ready?"
        assert settings_obj.teach_page_cta_line == "Tell us."
        assert settings_obj.teach_page_split_enabled is True
        assert settings_obj.teach_page_split_instructor_pct == 60
        assert settings_obj.teach_page_split_space_pct == 25
        assert settings_obj.teach_page_split_guild_pct == 15
        assert settings_obj.teach_page_split_note == "A note about the split."
        assert len(settings_obj.teach_page_feature_cards()) == 2

    def it_says_so_after_saving(admin_user, client, db):
        client.force_login(admin_user)
        response = client.post(reverse(URL_NAME), _post(), follow=True)
        assert [m.message for m in get_messages(response.wsgi_request)] == [SAVED_MESSAGE]
        assert SAVED_MESSAGE in response.content.decode()

    def it_stores_sanitized_html_for_a_rich_editor_save(admin_user, client, db):
        client.force_login(admin_user)
        response = client.post(
            reverse(URL_NAME),
            _post(
                teach_page_faq='<h3 class="ql-align-center">Q</h3><p style="color:red">A</p><script>alert(1)</script>'
            ),
        )
        assert response.status_code == 302
        stored = ClassSettings.load().teach_page_faq
        assert "<script" not in stored
        assert "style=" not in stored
        assert 'class="' not in stored
        assert "<h3>Q</h3>" in stored
        assert "<p>A</p>" in stored

    def it_stores_a_markdown_value_unchanged(admin_user, client, db):
        client.force_login(admin_user)
        response = client.post(reverse(URL_NAME), _post(teach_page_how_it_works="1. **Ask.** Say hi."))
        assert response.status_code == 302
        settings_obj = ClassSettings.load()
        assert settings_obj.teach_page_how_it_works == "1. **Ask.** Say hi."
        assert "<strong>Ask.</strong>" in settings_obj.teach_page_how_it_works_html

    def it_accepts_a_blanked_field_and_stores_it_blank(admin_user, client, db):
        client.force_login(admin_user)
        response = client.post(reverse(URL_NAME), _post(teach_page_faq="", teach_page_title=""))
        assert response.status_code == 302
        settings_obj = ClassSettings.load()
        assert settings_obj.teach_page_faq == ""
        assert settings_obj.teach_page_title == ""

    def describe_the_money_split():
        def it_rejects_shares_that_do_not_add_up_to_100_and_saves_nothing(admin_user, client, db):
            client.force_login(admin_user)
            response = client.post(reverse(URL_NAME), _post(teach_page_split_guild_pct="20"))
            assert response.status_code == 200
            content = response.content.decode()
            assert SUM_ERROR in content
            assert content.index(SUM_ERROR) > content.index('name="teach_page_split_instructor_pct"')
            settings_obj = ClassSettings.load()
            assert settings_obj.teach_page_split_instructor_pct == 70
            assert settings_obj.teach_page_title == "Share What You Love"

        def it_accepts_any_shares_while_the_section_is_hidden(admin_user, client, db):
            client.force_login(admin_user)
            payload = _post(teach_page_split_guild_pct="20")
            del payload["teach_page_split_enabled"]
            response = client.post(reverse(URL_NAME), payload)
            assert response.status_code == 302
            settings_obj = ClassSettings.load()
            assert settings_obj.teach_page_split_enabled is False
            assert settings_obj.teach_page_split_guild_pct == 20

        def it_rejects_a_share_over_100(admin_user, client, db):
            client.force_login(admin_user)
            response = client.post(reverse(URL_NAME), _post(teach_page_split_instructor_pct="150"))
            assert response.status_code == 200
            assert "less than or equal to 100" in response.content.decode()

    def describe_the_example_class():
        def it_saves_a_published_pick_and_refuses_a_draft(admin_user, client, db):
            published = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED, title="Live Class")
            draft = ClassOfferingFactory(status=ClassOffering.Status.DRAFT, title="Draft Class")
            client.force_login(admin_user)
            picker = (
                client.get(reverse(URL_NAME)).content.decode().split('name="example_class"')[1].split("</select>")[0]
            )
            assert f'value="{published.pk}"' in picker
            assert f'value="{draft.pk}"' not in picker
            assert client.post(reverse(URL_NAME), _post(example_class=str(published.pk))).status_code == 302
            assert ClassSettings.load().example_class_id == published.pk
            response = client.post(reverse(URL_NAME), _post(example_class=str(draft.pk)))
            assert response.status_code == 200
            assert "Select a valid choice" in response.content.decode()
            assert ClassSettings.load().example_class_id == published.pk

    def it_refuses_a_non_admin_on_get_and_post(member_user, client, db):
        client.force_login(member_user)
        assert client.get(reverse(URL_NAME)).status_code == 403
        assert client.post(reverse(URL_NAME), _post()).status_code == 403
        assert ClassSettings.load().teach_page_title == "Share What You Love"
