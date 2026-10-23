"""Admin Tools > Spotlight's Announcements section (#889): the gate, the rows, add, edit, the switch, order and delete."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.core import mail
from django.http import HttpResponse
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core.factories import AnnouncementFactory
from core.models import Announcement
from hub.announcement_admin_views import (
    DELETED,
    ORDER_SAVED,
    SAVED_EDIT,
    SAVED_NEW,
    TURNED_OFF,
    TURNED_ON,
    AnnouncementRow,
)
from hub.spotlight_forms import (
    ANNOUNCEMENT_EMPTY,
    ANNOUNCEMENT_ENDS_FIRST,
    ANNOUNCEMENT_TIME_NEEDS_DATE,
    AnnouncementForm,
    announcement_too_long,
)

pytestmark = pytest.mark.django_db

PORTLAND = ZoneInfo("America/Los_Angeles")
PAGE = reverse("hub_admin_spotlight")
SECTION = f"{PAGE}#announcements"
NEW = reverse("hub_admin_announcement_new")
ORDER = reverse("hub_admin_announcement_reorder")
HTMX = {"HTTP_HX_REQUEST": "true"}


def _edit(pk: int) -> str:
    return reverse("hub_admin_announcement_edit", args=[pk])


def _toggle(pk: int) -> str:
    return reverse("hub_admin_announcement_toggle", args=[pk])


def _delete(pk: int) -> str:
    return reverse("hub_admin_announcement_delete", args=[pk])


@pytest.fixture
def admin_user() -> User:
    return User.objects.create_superuser(username="annadmin", password="pass", email="annadmin@example.com")


@pytest.fixture
def admin_client(client: Client, admin_user: User) -> Client:
    client.login(username="annadmin", password="pass")
    return client


@pytest.fixture
def member_client(client: Client) -> Client:
    User.objects.create_user(username="annmember", password="pass", email="annmember@example.com")
    client.login(username="annmember", password="pass")
    return client


def _post(**overrides: str) -> dict[str, str]:
    data = {
        "body": "<p>Open house <strong>Saturday</strong></p>",
        "is_active": "on",
        "show_from_date": "",
        "show_from_time": "",
        "show_until_date": "",
        "show_until_time": "",
    }
    data.update(overrides)
    return data


def _form(**overrides: str) -> AnnouncementForm:
    form = AnnouncementForm(_post(**overrides))
    form.is_valid()
    return form


def _messages(response: HttpResponse) -> list[str]:
    return [str(message) for message in get_messages(response.wsgi_request)]


def _toast(response: HttpResponse) -> dict[str, str]:
    return json.loads(response["HX-Trigger"])["showToast"]


def describe_permissions():
    @pytest.mark.parametrize(
        ("method", "name", "args"),
        [
            ("get", "hub_admin_announcement_new", []),
            ("post", "hub_admin_announcement_new", []),
            ("get", "hub_admin_announcement_edit", [1]),
            ("post", "hub_admin_announcement_edit", [1]),
            ("post", "hub_admin_announcement_toggle", [1]),
            ("post", "hub_admin_announcement_reorder", []),
            ("post", "hub_admin_announcement_delete", [1]),
        ],
    )
    def it_refuses_a_member_who_is_not_an_admin(member_client: Client, method: str, name: str, args: list[int]):
        assert getattr(member_client, method)(reverse(name, args=args)).status_code == 403

    def it_sends_a_signed_out_visitor_to_sign_in(client: Client):
        response = client.get(NEW)

        assert response.status_code == 302
        assert "/accounts/login/" in response.url

    def it_changes_nothing_for_a_crafted_post_from_a_member(member_client: Client):
        first, second = AnnouncementFactory(), AnnouncementFactory()

        member_client.post(NEW, _post(body="<p>Sneaky</p>"))
        member_client.post(_edit(first.pk), _post(body="<p>Sneaky</p>"))
        member_client.post(_toggle(first.pk), {})
        member_client.post(ORDER, {"order": f"{second.pk},{first.pk}"})
        member_client.post(_delete(second.pk))

        assert list(Announcement.objects.order_by("pk").values_list("body", "is_active", "position")) == [
            (first.body, True, 0),
            (second.body, True, 0),
        ]

    @pytest.mark.parametrize("name", ["hub_admin_announcement_toggle", "hub_admin_announcement_delete"])
    def it_takes_only_posts(admin_client: Client, name: str):
        announcement = AnnouncementFactory()

        assert admin_client.get(reverse(name, args=[announcement.pk])).status_code == 405

    def it_answers_404_for_an_unknown_announcement(admin_client: Client):
        assert admin_client.get(_edit(999)).status_code == 404
        assert admin_client.post(_toggle(999)).status_code == 404
        assert admin_client.post(_delete(999)).status_code == 404


def describe_the_section():
    def it_shows_the_empty_state(admin_client: Client):
        html = admin_client.get(PAGE).content.decode()

        assert 'id="announcements"' in html
        assert "data-announcements-empty" in html
        assert "No announcements yet. Add one and it shows at the top of Home." in html
        assert "pl-ann-admin__rows" not in html
        assert f'href="{NEW}" class="pl-btn pl-btn--primary pl-btn--sm" data-announcement-add' in html

    def it_comes_before_the_spotlight_text(admin_client: Client):
        html = admin_client.get(PAGE).content.decode()

        assert html.index('id="announcements"') < html.index('id="spotlight-text"')

    def it_lists_every_announcement_in_order_with_its_status(admin_client: Client):
        now = timezone.now()
        live = AnnouncementFactory(body="<p>Zorblax live</p>", position=3)
        scheduled = AnnouncementFactory(body="<p>Zorblax soon</p>", show_from=now + timedelta(days=2), position=0)
        ended = AnnouncementFactory(body="<p>Zorblax ended</p>", show_until=now - timedelta(days=1), position=1)
        off = AnnouncementFactory(body="<p>Zorblax off</p>", is_active=False, position=2)

        html = admin_client.get(PAGE).content.decode()

        positions = [html.index(f'data-announcement-row="{row.pk}"') for row in (scheduled, ended, off, live)]
        assert positions == sorted(positions)
        assert '<span class="hub-pill hub-pill--primary" data-announcement-status="scheduled">Scheduled</span>' in html
        assert '<span class="hub-pill hub-pill--neutral" data-announcement-status="ended">Ended</span>' in html
        assert '<span class="hub-pill hub-pill--neutral" data-announcement-status="off">Off</span>' in html
        assert '<span class="hub-pill hub-pill--ok" data-announcement-status="live">Live</span>' in html
        assert f'value="{scheduled.pk},{ended.pk},{off.pk},{live.pk}" data-announcement-order' in html
        assert "<p>Zorblax live</p>" in html

    def it_shows_each_switch_in_its_saved_state(admin_client: Client):
        on, off = AnnouncementFactory(), AnnouncementFactory(is_active=False)

        html = admin_client.get(PAGE).content.decode()

        assert f'name="announcement-{on.pk}-is_active" id="id_announcement-{on.pk}-is_active" checked' in html
        assert f'name="announcement-{off.pk}-is_active" id="id_announcement-{off.pk}-is_active">' in html

    def it_offers_edit_and_a_confirmed_delete_on_each_row(admin_client: Client):
        announcement = AnnouncementFactory()

        html = admin_client.get(PAGE).content.decode()

        assert f'href="{_edit(announcement.pk)}"' in html
        assert f"$dispatch('open-confirm', 'delete-announcement-{announcement.pk}')" in html
        assert "Delete this announcement?" in html
        assert "Members stop seeing it at once." in html
        assert f'action="{_delete(announcement.pk)}"' in html


def describe_the_dates_line():
    def _dates(**fields: datetime | None) -> str:
        return AnnouncementRow.build(AnnouncementFactory.build(**fields), timezone.now()).dates

    def it_says_so_when_there_are_none():
        assert _dates() == "No dates. Shows until turned off."

    def it_shows_a_start_alone():
        assert _dates(show_from=datetime(2026, 10, 24, 9, 0, tzinfo=PORTLAND)) == "From Oct 24, 9:00 AM"

    def it_shows_an_end_alone():
        assert _dates(show_until=datetime(2026, 11, 8, 17, 30, tzinfo=PORTLAND)) == "Until Nov 8, 5:30 PM"

    def it_shows_both_in_portland_time():
        start = datetime(2026, 10, 24, 16, 0, tzinfo=ZoneInfo("UTC"))
        end = datetime(2026, 11, 8, 23, 59, 59, tzinfo=PORTLAND)

        assert _dates(show_from=start, show_until=end) == "Oct 24, 9:00 AM to Nov 8, 11:59 PM"


def describe_adding():
    def it_shows_the_form_with_the_short_editor_and_a_count(admin_client: Client):
        html = admin_client.get(NEW).content.decode()

        assert "New Announcement" in html
        assert 'data-rte-toolbar="inline"' in html
        assert 'data-announcement-count data-max="160" aria-live="polite">0 / 160</p>' in html
        assert 'name="show_from_date"' in html and 'name="show_until_time"' in html
        assert '<option value="" selected>Start of day</option>' in html
        assert '<option value="" selected>End of day</option>' in html
        assert f'href="{SECTION}" class="pl-btn pl-btn--ghost">Cancel</a>' in html

    def it_saves_a_new_announcement_and_goes_back_to_the_section(admin_client: Client, admin_user: User):
        response = admin_client.post(
            NEW, _post(body='<p>Open <strong>house</strong> <img src="x.png"></p>', show_from_date="2026-11-08")
        )

        assert response.status_code == 302
        assert response.url == SECTION
        assert _messages(response) == [SAVED_NEW]
        announcement = Announcement.objects.get()
        assert announcement.body == "<p>Open <strong>house</strong> </p>"
        assert announcement.is_active is True
        assert announcement.show_from == datetime(2026, 11, 8, 0, 0, tzinfo=PORTLAND)
        assert announcement.show_until is None
        assert announcement.created_by == admin_user

    def it_saves_one_turned_off(admin_client: Client):
        data = _post()
        del data["is_active"]

        admin_client.post(NEW, data)

        assert Announcement.objects.get().is_active is False

    def it_shows_the_form_again_with_what_was_typed_after_a_refusal(admin_client: Client):
        body = f"<p>{'z' * 161}</p>"

        response = admin_client.post(NEW, _post(body=body))

        html = response.content.decode()
        assert response.status_code == 200
        assert announcement_too_long(161) in html
        assert "z" * 161 in html
        assert 'class="pl-ann-admin-form__count is-over" data-announcement-count data-max="160"' in html
        assert ">161 / 160</p>" in html
        assert not Announcement.objects.exists()

    def it_sends_nothing(admin_client: Client):
        admin_client.post(NEW, _post())

        assert mail.outbox == []


def describe_editing():
    def it_opens_the_saved_text_dates_and_count(admin_client: Client):
        announcement = AnnouncementFactory(
            body="<p>Kiln <em>day</em> &amp; tea</p>",
            show_from=datetime(2026, 10, 24, 9, 30, tzinfo=PORTLAND),
            show_until=datetime(2026, 11, 8, 23, 59, 59, 999999, tzinfo=PORTLAND),
        )

        html = admin_client.get(_edit(announcement.pk)).content.decode()

        assert "Edit Announcement" in html
        assert ">14 / 160</p>" in html
        assert 'name="show_from_date" value="2026-10-24"' in html
        assert '<option value="09:30" selected>9:30 AM</option>' in html
        assert 'name="show_until_date" value="2026-11-08"' in html
        assert '<option value="" selected>End of day</option>' in html

    def it_saves_the_changes_and_keeps_the_author(admin_client: Client):
        author = User.objects.create_user(username="first-author")
        announcement = AnnouncementFactory(created_by=author, show_from=timezone.now())

        response = admin_client.post(_edit(announcement.pk), _post(body="<p>New words</p>", is_active=""))

        assert response.url == SECTION
        assert _messages(response) == [SAVED_EDIT]
        announcement.refresh_from_db()
        assert announcement.body == "<p>New words</p>"
        assert announcement.is_active is False
        assert announcement.show_from is None
        assert announcement.created_by == author

    def it_refuses_with_the_form_again(admin_client: Client):
        announcement = AnnouncementFactory(body="<p>Kept</p>")

        response = admin_client.post(_edit(announcement.pk), _post(body="<p><br></p>"))

        assert response.status_code == 200
        assert ANNOUNCEMENT_EMPTY in response.content.decode()
        announcement.refresh_from_db()
        assert announcement.body == "<p>Kept</p>"


def describe_the_160_rule():
    def it_takes_exactly_160_visible_characters():
        form = _form(body=f"<p>{'a' * 160}</p>")

        assert form.is_valid()
        assert form.cleaned_data["body"] == f"<p>{'a' * 160}</p>"

    def it_refuses_161_and_names_the_length():
        form = _form(body=f"<p>{'a' * 161}</p>")

        assert form.errors["body"] == ["Keep it to 160 characters (this one is 161)."]

    def it_counts_neither_tags_nor_entity_spelling():
        body = (
            f'<p><strong>{"a" * 150}</strong> <a href="https://example.org/a-very-long-path">&amp;&amp;&amp;</a> bc</p>'
        )

        form = _form(body=body)

        assert form.is_valid(), form.errors
        assert len(Announcement(body=form.cleaned_data["body"]).text) == 157

    def it_counts_a_line_break_as_one_space():
        assert _form(body=f"<p>{'a' * 80}</p><p>{'b' * 79}</p>").is_valid()
        assert not _form(body=f"<p>{'a' * 80}</p><p>{'b' * 80}</p>").is_valid()

    def it_counts_what_is_left_after_sanitizing():
        body = f"<p>{'a' * 150}<script>{'x' * 50}</script></p>"

        assert _form(body=body).is_valid()

    @pytest.mark.parametrize("body", ["", "<p><br></p>", "<p>   </p>"])
    def it_refuses_an_empty_announcement(body: str):
        assert _form(body=body).errors["body"] in ([ANNOUNCEMENT_EMPTY], ["This field is required."])

    def it_refuses_text_past_the_size_limit_before_cleaning_it():
        form = _form(body="<p>" + "a" * 100_001 + "</p>")

        assert "too long to save" in form.errors["body"][0]


def describe_the_dates():
    def it_starts_at_the_picked_half_hour_in_portland():
        form = _form(show_from_date="2026-11-08", show_from_time="18:30")

        assert form.cleaned_data["show_from"] == datetime(2026, 11, 8, 18, 30, tzinfo=PORTLAND)

    def it_starts_at_the_start_of_the_day_with_no_time():
        form = _form(show_from_date="2026-11-08")

        assert form.cleaned_data["show_from"] == datetime(2026, 11, 8, 0, 0, tzinfo=PORTLAND)

    def it_ends_at_the_end_of_the_day_with_no_time():
        form = _form(show_until_date="2026-11-08")

        assert form.cleaned_data["show_until"] == datetime(2026, 11, 8, 23, 59, 59, 999999, tzinfo=PORTLAND)

    def it_ends_at_the_picked_half_hour():
        form = _form(show_until_date="2026-11-08", show_until_time="06:00")

        assert form.cleaned_data["show_until"] == datetime(2026, 11, 8, 6, 0, tzinfo=PORTLAND)

    def it_leaves_both_empty_with_no_dates():
        form = _form()

        assert form.is_valid()
        assert (form.cleaned_data["show_from"], form.cleaned_data["show_until"]) == (None, None)

    def it_refuses_an_end_before_the_start():
        form = _form(show_from_date="2026-11-08", show_until_date="2026-11-07")

        assert form.errors == {"show_until_date": [ANNOUNCEMENT_ENDS_FIRST]}

    def it_refuses_an_end_at_the_start():
        form = _form(
            show_from_date="2026-11-08", show_from_time="09:00", show_until_date="2026-11-08", show_until_time="09:00"
        )

        assert form.errors == {"show_until_date": [ANNOUNCEMENT_ENDS_FIRST]}

    def it_takes_an_end_on_the_start_day_with_no_times():
        assert _form(show_from_date="2026-11-08", show_until_date="2026-11-08").is_valid()

    def it_takes_an_end_with_no_start_and_a_start_with_no_end():
        assert _form(show_until_date="2020-01-01").is_valid()
        assert _form(show_from_date="2099-01-01").is_valid()

    @pytest.mark.parametrize("name", ["show_from", "show_until"])
    def it_asks_for_a_date_for_a_time_alone(name: str):
        form = _form(**{f"{name}_time": "09:00"})

        assert form.errors == {f"{name}_date": [ANNOUNCEMENT_TIME_NEEDS_DATE]}

    def it_does_not_pile_a_second_error_on_a_bad_date():
        form = _form(show_from_date="not-a-date", show_from_time="09:00")

        assert form.errors == {"show_from_date": ["Enter a valid date."]}

    def it_refuses_a_time_off_the_half_hour_grid():
        assert "show_from_time" in _form(show_from_date="2026-11-08", show_from_time="09:07").errors


def describe_opening_saved_dates():
    def it_leaves_the_boxes_empty_with_no_dates():
        form = AnnouncementForm(instance=AnnouncementFactory.build())

        assert "show_from_date" not in form.initial and "show_until_date" not in form.initial

    def it_shows_a_start_at_midnight_as_the_start_of_the_day():
        form = AnnouncementForm(
            instance=AnnouncementFactory.build(show_from=datetime(2026, 11, 8, 0, 0, tzinfo=PORTLAND))
        )

        assert form.initial["show_from_date"].isoformat() == "2026-11-08"
        assert "show_from_time" not in form.initial

    def it_keeps_a_time_off_the_grid_as_its_own_choice():
        instance = AnnouncementFactory.build(show_until=datetime(2026, 11, 8, 23, 15, tzinfo=PORTLAND))

        form = AnnouncementForm(instance=instance)

        assert form.initial["show_until_time"] == "23:15"
        assert form.fields["show_until_time"].choices[-1] == ("23:15", "11:15 PM")
        assert form.fields["show_until_time"].choices[0] == ("", "End of day")

    def it_does_not_add_a_choice_for_a_time_on_the_grid():
        instance = AnnouncementFactory.build(show_from=datetime(2026, 11, 8, 9, 0, tzinfo=PORTLAND))

        form = AnnouncementForm(instance=instance)

        assert form.initial["show_from_time"] == "09:00"
        assert [value for value, _ in form.fields["show_from_time"].choices].count("09:00") == 1
        assert form.fields["show_from_time"].choices[-1] == ("21:30", "9:30 PM")


def describe_the_switch():
    def it_turns_one_off_at_once_and_answers_with_the_row(admin_client: Client):
        announcement = AnnouncementFactory()
        before = announcement.updated_at

        response = admin_client.post(_toggle(announcement.pk), {}, **HTMX)

        html = response.content.decode()
        assert response.status_code == 200
        assert html.lstrip().startswith(
            f'<li class="pl-slide-card pl-ann-admin__row" data-announcement-row="{announcement.pk}">'
        )
        assert 'data-announcement-status="off">Off</span>' in html
        assert _toast(response) == {"message": TURNED_OFF, "type": "success"}
        announcement.refresh_from_db()
        assert announcement.is_active is False
        assert announcement.updated_at > before

    def it_turns_one_on(admin_client: Client):
        announcement = AnnouncementFactory(is_active=False)

        response = admin_client.post(
            _toggle(announcement.pk), {f"announcement-{announcement.pk}-is_active": "on"}, **HTMX
        )

        assert 'data-announcement-status="live">Live</span>' in response.content.decode()
        assert _toast(response)["message"] == TURNED_ON
        announcement.refresh_from_db()
        assert announcement.is_active is True

    def it_reads_only_its_own_rows_switch(admin_client: Client):
        announcement, other = AnnouncementFactory(is_active=False), AnnouncementFactory()

        admin_client.post(_toggle(announcement.pk), {f"announcement-{other.pk}-is_active": "on"}, **HTMX)

        announcement.refresh_from_db()
        assert announcement.is_active is False

    def it_goes_back_to_the_section_without_htmx(admin_client: Client):
        announcement = AnnouncementFactory()

        response = admin_client.post(_toggle(announcement.pk), {})

        assert response.status_code == 302
        assert response.url == SECTION
        assert _messages(response) == [TURNED_OFF]

    def it_posts_itself_on_change_and_swaps_its_row(admin_client: Client):
        announcement = AnnouncementFactory()

        html = admin_client.get(PAGE).content.decode()

        assert (
            f'hx-post="{_toggle(announcement.pk)}" hx-trigger="change" hx-target="closest [data-announcement-row]" '
            'hx-swap="outerHTML" data-announcement-toggle>'
        ) in html


def describe_the_order():
    def it_saves_a_new_order_at_once(admin_client: Client):
        first, second = AnnouncementFactory(), AnnouncementFactory()

        response = admin_client.post(ORDER, {"order": f"{second.pk},{first.pk}"}, **HTMX)

        assert response.status_code == 204
        assert _toast(response) == {"message": ORDER_SAVED, "type": "success"}
        assert list(Announcement.objects.in_order()) == [second, first]

    def it_goes_back_to_the_section_without_htmx(admin_client: Client):
        first, second = AnnouncementFactory(), AnnouncementFactory()

        response = admin_client.post(ORDER, {"order": f"{first.pk},{second.pk}"})

        assert response.url == SECTION
        assert _messages(response) == [ORDER_SAVED]
        assert list(Announcement.objects.in_order()) == [first, second]

    def it_refuses_a_stale_order(admin_client: Client):
        first = AnnouncementFactory()
        AnnouncementFactory()

        response = admin_client.post(ORDER, {"order": str(first.pk)}, **HTMX)

        assert response.status_code == 409
        assert _toast(response) == {
            "message": "The list changed since this page loaded. Reload it and try again.",
            "type": "error",
        }

    @pytest.mark.parametrize(
        ("data", "message"),
        [
            ({"order": "1,two"}, "That order could not be read. Reload the page and try again."),
            ({}, "This field is required."),
        ],
    )
    def it_refuses_an_order_it_cannot_read(admin_client: Client, data: dict[str, str], message: str):
        AnnouncementFactory()

        response = admin_client.post(ORDER, data, **HTMX)

        assert response.status_code == 409
        assert _toast(response) == {"message": message, "type": "error"}

    def it_says_so_on_the_page_without_htmx(admin_client: Client):
        AnnouncementFactory()

        response = admin_client.post(ORDER, {"order": "999"})

        assert response.url == SECTION
        assert _messages(response) == ["The list changed since this page loaded. Reload it and try again."]

    def it_posts_over_htmx_without_a_swap(admin_client: Client):
        AnnouncementFactory()

        html = admin_client.get(PAGE).content.decode()

        assert f'hx-post="{ORDER}" hx-swap="none" hidden data-announcement-order-form>' in html


def describe_deleting():
    def it_deletes_and_goes_back_to_the_section(admin_client: Client):
        announcement, kept = AnnouncementFactory(), AnnouncementFactory()

        response = admin_client.post(_delete(announcement.pk))

        assert response.url == SECTION
        assert _messages(response) == [DELETED]
        assert list(Announcement.objects.all()) == [kept]
