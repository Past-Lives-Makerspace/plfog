"""BDD specs for Instructor Resources under Class Settings (#765).

An admin keeps a list of links, each a label and a URL, and every instructor sees them in a
card on their Teaching page. The editor is the Help editor's links rows: no blank row, a
"+ Add a link" button, a Delete button on every saved row, one Save. The server numbers
``sort_order`` from the order the rows were posted in, whatever the hidden inputs carried.
"""

from __future__ import annotations

from django.contrib.messages import get_messages
from django.urls import reverse

from classes.factories import InstructorResourceFactory
from classes.models import InstructorResource

URL_NAME = "classes:admin_instructor_resources"
SAVED_MESSAGE = "Instructor Resources saved."
PREFIX = "links"


def _row(index: int, label: str, url: str, pk: int | None = None, sort_order: int = 0, delete: bool = False) -> dict:
    row = {
        f"{PREFIX}-{index}-label": label,
        f"{PREFIX}-{index}-url": url,
        f"{PREFIX}-{index}-sort_order": str(sort_order),
        f"{PREFIX}-{index}-id": "" if pk is None else str(pk),
    }
    if delete:
        row[f"{PREFIX}-{index}-DELETE"] = "on"
    return row


def _saved_row(resource: InstructorResource, index: int, delete: bool = False, label: str | None = None) -> dict:
    """The row as the page posts it back: its own values, its pk, its stored sort_order."""
    return _row(
        index,
        resource.label if label is None else label,
        resource.url,
        pk=resource.pk,
        sort_order=resource.sort_order,
        delete=delete,
    )


def _post(*rows: dict, initial: int) -> dict:
    payload = {
        f"{PREFIX}-TOTAL_FORMS": str(len(rows)),
        f"{PREFIX}-INITIAL_FORMS": str(initial),
        f"{PREFIX}-MIN_NUM_FORMS": "0",
        f"{PREFIX}-MAX_NUM_FORMS": "1000",
    }
    for row in rows:
        payload.update(row)
    return payload


def _three_saved() -> tuple[InstructorResource, InstructorResource, InstructorResource]:
    return (
        InstructorResourceFactory(label="Review Us", url="https://g.page/review", sort_order=0),
        InstructorResourceFactory(label="Model Release", url="https://forms.example.com/release", sort_order=1),
        InstructorResourceFactory(label="Liability Form", url="https://forms.example.com/liability", sort_order=2),
    )


def _ordered() -> list[tuple[str, int]]:
    return list(InstructorResource.objects.values_list("label", "sort_order"))


def describe_admin_instructor_resources():
    def it_is_served_under_class_settings(db):
        assert reverse(URL_NAME) == "/classes/admin/settings/instructor-resources/"

    def it_names_a_resource_by_its_label(db):
        assert str(InstructorResourceFactory(label="Review Us")) == "Review Us"

    def it_refuses_a_non_admin_on_get_and_post(member_user, client, db):
        client.force_login(member_user)
        assert client.get(reverse(URL_NAME)).status_code == 403
        payload = _post(_row(0, "Review Us", "https://g.page/review"), initial=0)
        assert client.post(reverse(URL_NAME), payload).status_code == 403
        assert InstructorResource.objects.count() == 0

    def it_renders_for_an_admin_with_the_settings_tab_active(admin_user, client, db):
        client.force_login(admin_user)
        response = client.get(reverse(URL_NAME))
        assert response.status_code == 200
        content = response.content.decode()
        assert "Instructor Resources" in content.split('class="pl-teach-page-settings__title">')[1][:60]
        assert 'vote-tab vote-tab--active" aria-current="page">Settings</a>' in content
        assert "These links show in a card on every instructor's Teaching page." in content
        assert "View the Page" in content
        assert f'href="{reverse("classes:teach_overview")}" target="_blank" rel="noopener"' in content

    def describe_the_rows():
        def it_lists_the_saved_links_in_order_as_label_and_url_rows_each_with_delete(admin_user, client, db):
            InstructorResourceFactory(label="Third Link", url="https://example.com/third", sort_order=2)
            InstructorResourceFactory(label="First Link", url="https://example.com/first", sort_order=0)
            InstructorResourceFactory(label="Second Link", url="https://example.com/second", sort_order=1)
            client.force_login(admin_user)
            content = client.get(reverse(URL_NAME)).content.decode()
            positions = [content.index(f'value="https://example.com/{slug}"') for slug in ("first", "second", "third")]
            assert positions == sorted(positions)
            assert content.index('value="First Link"') < content.index('value="https://example.com/first"')
            for index in range(3):
                assert f'name="{PREFIX}-{index}-label"' in content
                assert f'name="{PREFIX}-{index}-url"' in content
                assert f'type="hidden" name="{PREFIX}-{index}-sort_order"' in content
                assert f'id="id_{PREFIX}-{index}-DELETE"' in content
            rows = content.split('id="instructor-resource-rows"')[1].split("<template")[0]
            assert rows.count('class="pl-instructor-resources__row"') == 3
            assert rows.count(">\n                Delete\n            </button>") == 3
            assert "pl-instructor-resources__empty" not in rows

        def it_shows_the_empty_state_with_no_links(admin_user, client, db):
            client.force_login(admin_user)
            content = client.get(reverse(URL_NAME)).content.decode()
            rows = content.split('id="instructor-resource-rows"')[1].split("<template")[0]
            assert "No links yet. Add your first." in rows
            assert "pl-instructor-resources__empty" in rows
            assert f'name="{PREFIX}-0-label"' not in content
            assert f'id="id_{PREFIX}-0-DELETE"' not in content

        def it_has_the_management_form_the_add_button_and_one_save_last(admin_user, client, db):
            InstructorResourceFactory()
            client.force_login(admin_user)
            content = client.get(reverse(URL_NAME)).content.decode()
            assert f'name="{PREFIX}-TOTAL_FORMS"' in content
            assert f'name="{PREFIX}-INITIAL_FORMS"' in content
            assert "+ Add a link" in content
            template = content.split('<template id="instructor-resource-empty-template">')[1].split("</template>")[0]
            assert f'name="{PREFIX}-__prefix__-label"' in template
            assert f'name="{PREFIX}-__prefix__-url"' in template
            assert ">Remove</button>" in template
            assert "DELETE" not in template
            assert content.count(">Save</button>") == 1
            after_save = content.split(">Save</button>")[1]
            assert "<input" not in after_save and "<textarea" not in after_save
            assert content.index("+ Add a link") < content.index(">Save</button>")

    def describe_saving():
        def it_creates_the_rows_posted_numbered_in_page_order(admin_user, client, db):
            client.force_login(admin_user)
            payload = _post(
                _row(0, "Review Us", "https://g.page/review"),
                _row(1, "Model Release", "https://forms.example.com/release"),
                initial=0,
            )
            response = client.post(reverse(URL_NAME), payload)
            assert response.status_code == 302
            assert response["Location"] == reverse(URL_NAME)
            assert _ordered() == [("Review Us", 0), ("Model Release", 1)]

        def it_says_so_after_saving(admin_user, client, db):
            client.force_login(admin_user)
            payload = _post(_row(0, "Review Us", "https://g.page/review"), initial=0)
            response = client.post(reverse(URL_NAME), payload, follow=True)
            assert [m.message for m in get_messages(response.wsgi_request)] == [SAVED_MESSAGE]
            assert SAVED_MESSAGE in response.content.decode()

        def it_removes_a_row_whose_delete_flag_is_set_and_renumbers_the_rest(admin_user, client, db):
            first, second, third = _three_saved()
            client.force_login(admin_user)
            payload = _post(_saved_row(first, 0, delete=True), _saved_row(second, 1), _saved_row(third, 2), initial=3)
            assert client.post(reverse(URL_NAME), payload).status_code == 302
            assert not InstructorResource.objects.filter(pk=first.pk).exists()
            assert _ordered() == [("Model Release", 0), ("Liability Form", 1)]

        def it_keeps_an_edit_on_another_row_when_one_is_deleted(admin_user, client, db):
            first, second, third = _three_saved()
            client.force_login(admin_user)
            payload = _post(
                _saved_row(first, 0, label="Leave a Review"),
                _saved_row(second, 1, delete=True),
                _saved_row(third, 2),
                initial=3,
            )
            assert client.post(reverse(URL_NAME), payload).status_code == 302
            assert _ordered() == [("Leave a Review", 0), ("Liability Form", 1)]

        def it_renumbers_sort_order_from_the_order_the_rows_were_posted_in(admin_user, client, db):
            first, second, third = _three_saved()
            client.force_login(admin_user)
            # The rows arrive in a new order, each still carrying its old sort_order.
            payload = _post(_saved_row(third, 0), _saved_row(first, 1), _saved_row(second, 2), initial=3)
            assert client.post(reverse(URL_NAME), payload).status_code == 302
            assert _ordered() == [("Liability Form", 0), ("Review Us", 1), ("Model Release", 2)]

        def it_numbers_a_new_row_after_the_saved_ones(admin_user, client, db):
            first, second, third = _three_saved()
            client.force_login(admin_user)
            payload = _post(
                _saved_row(first, 0),
                _saved_row(second, 1),
                _saved_row(third, 2),
                _row(3, "Hosting Guide", "https://example.com/guide", sort_order=0),
                initial=3,
            )
            assert client.post(reverse(URL_NAME), payload).status_code == 302
            assert _ordered() == [("Review Us", 0), ("Model Release", 1), ("Liability Form", 2), ("Hosting Guide", 3)]

        def it_ignores_an_added_row_left_blank(admin_user, client, db):
            first, _second, _third = _three_saved()
            client.force_login(admin_user)
            payload = _post(_saved_row(first, 0), _row(1, "", ""), initial=1)
            assert client.post(reverse(URL_NAME), payload).status_code == 302
            assert InstructorResource.objects.count() == 3
            assert InstructorResource.objects.get(pk=first.pk).sort_order == 0

    def describe_validation():
        def it_rejects_a_blank_label_with_the_error_under_the_field_and_saves_nothing(admin_user, client, db):
            first, second, third = _three_saved()
            client.force_login(admin_user)
            payload = _post(
                _saved_row(first, 0),
                _saved_row(second, 1),
                _saved_row(third, 2),
                _row(3, "", "https://example.com/guide"),
                initial=3,
            )
            response = client.post(reverse(URL_NAME), payload)
            assert response.status_code == 200
            content = response.content.decode()
            label_input = content.index(f'name="{PREFIX}-3-label"')
            url_input = content.index(f'name="{PREFIX}-3-url"')
            assert label_input < content.index("This field is required.") < url_input
            assert InstructorResource.objects.count() == 3

        def it_rejects_a_url_that_is_not_one_and_saves_nothing(admin_user, client, db):
            client.force_login(admin_user)
            payload = _post(_row(0, "Hosting Guide", "not a url"), initial=0)
            response = client.post(reverse(URL_NAME), payload)
            assert response.status_code == 200
            content = response.content.decode()
            assert content.index(f'name="{PREFIX}-0-url"') < content.index("Enter a valid URL.")
            assert 'value="Hosting Guide"' in content
            assert InstructorResource.objects.count() == 0
