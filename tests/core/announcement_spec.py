"""Home announcements (#889): ``core.models.Announcement``, its queryset and its sanitizer."""

from __future__ import annotations

from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

import pytest
from django.contrib.auth.models import User

from core.factories import AnnouncementFactory
from core.html_sanitize import ANNOUNCEMENT_MAX_CHARS, sanitize_announcement_html
from core.models import Announcement, AnnouncementOrderChangedError

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 10, 23, 18, 0, tzinfo=dt_timezone.utc)
HOUR = timedelta(hours=1)


def _live_pks() -> list[int]:
    return list(Announcement.objects.live(NOW).values_list("pk", flat=True))


def describe_live():
    def it_keeps_one_with_no_dates():
        announcement = AnnouncementFactory()

        assert _live_pks() == [announcement.pk]

    def it_leaves_out_one_that_is_off():
        AnnouncementFactory(is_active=False)

        assert _live_pks() == []

    def it_leaves_out_one_that_starts_later():
        AnnouncementFactory(show_from=NOW + HOUR)

        assert _live_pks() == []

    def it_keeps_one_that_starts_exactly_now():
        announcement = AnnouncementFactory(show_from=NOW)

        assert _live_pks() == [announcement.pk]

    def it_keeps_one_that_has_begun_and_not_ended():
        announcement = AnnouncementFactory(show_from=NOW - HOUR, show_until=NOW + HOUR)

        assert _live_pks() == [announcement.pk]

    def it_leaves_out_one_whose_end_has_passed():
        AnnouncementFactory(show_until=NOW - HOUR)

        assert _live_pks() == []

    def it_leaves_out_one_that_ends_exactly_now():
        AnnouncementFactory(show_until=NOW)

        assert _live_pks() == []

    def it_keeps_one_that_ends_later():
        announcement = AnnouncementFactory(show_until=NOW + HOUR)

        assert _live_pks() == [announcement.pk]

    def it_orders_by_position_then_newest_first():
        older_first = AnnouncementFactory(position=0)
        newer_first = AnnouncementFactory(position=0)
        second = AnnouncementFactory(position=1)
        Announcement.objects.filter(pk=older_first.pk).update(created_at=NOW - HOUR)
        Announcement.objects.filter(pk=newer_first.pk).update(created_at=NOW)
        Announcement.objects.filter(pk=second.pk).update(created_at=NOW + HOUR)

        assert _live_pks() == [newer_first.pk, older_first.pk, second.pk]


def describe_in_order():
    def it_lists_every_announcement_live_or_not_in_order():
        off = AnnouncementFactory(is_active=False, position=1)
        ended = AnnouncementFactory(show_until=NOW - HOUR, position=0)

        assert list(Announcement.objects.in_order()) == [ended, off]

    def it_breaks_a_tie_on_the_same_moment_by_the_newer_row():
        first = AnnouncementFactory()
        second = AnnouncementFactory()
        Announcement.objects.update(created_at=NOW)

        assert list(Announcement.objects.in_order()) == [second, first]


def describe_status():
    def it_is_live_with_no_dates():
        assert AnnouncementFactory.build().status(NOW) == Announcement.Status.LIVE

    def it_is_off_when_turned_off_whatever_its_dates():
        announcement = AnnouncementFactory.build(is_active=False, show_until=NOW - HOUR)

        assert announcement.status(NOW) == Announcement.Status.OFF

    def it_is_scheduled_before_its_start():
        assert AnnouncementFactory.build(show_from=NOW + HOUR).status(NOW) == Announcement.Status.SCHEDULED

    def it_is_live_from_the_moment_it_starts():
        assert AnnouncementFactory.build(show_from=NOW).status(NOW) == Announcement.Status.LIVE

    def it_is_ended_from_the_moment_it_ends():
        assert AnnouncementFactory.build(show_until=NOW).status(NOW) == Announcement.Status.ENDED

    def it_is_live_until_it_ends():
        assert AnnouncementFactory.build(show_until=NOW + HOUR).status(NOW) == Announcement.Status.LIVE

    def it_is_ended_when_both_dates_are_past():
        announcement = AnnouncementFactory.build(show_from=NOW - 2 * HOUR, show_until=NOW - HOUR)

        assert announcement.status(NOW) == Announcement.Status.ENDED


def describe_reorder():
    def it_puts_each_at_its_index():
        first, second, third = AnnouncementFactory(), AnnouncementFactory(), AnnouncementFactory()

        Announcement.reorder([third.pk, first.pk, second.pk])

        assert dict(Announcement.objects.values_list("pk", "position")) == {third.pk: 0, first.pk: 1, second.pk: 2}

    def it_leaves_the_last_save_alone():
        first, second = AnnouncementFactory(), AnnouncementFactory()
        before = {row.pk: row.updated_at for row in Announcement.objects.all()}

        Announcement.reorder([second.pk, first.pk])

        assert {row.pk: row.updated_at for row in Announcement.objects.all()} == before

    @pytest.mark.parametrize("shape", ["missing", "extra", "repeated"])
    def it_refuses_an_order_that_does_not_name_each_one_once(shape: str):
        first, second = AnnouncementFactory(), AnnouncementFactory()
        order = {
            "missing": [first.pk],
            "extra": [first.pk, second.pk, second.pk + 1],
            "repeated": [first.pk, first.pk, second.pk],
        }[shape]

        with pytest.raises(AnnouncementOrderChangedError, match="Reload it and try again"):
            Announcement.reorder(order)
        assert set(Announcement.objects.values_list("position", flat=True)) == {0}


def describe_signature():
    def it_names_each_row_and_its_last_save_by_id():
        second, first = AnnouncementFactory.build(pk=9), AnnouncementFactory.build(pk=4)
        second.updated_at = datetime(2026, 10, 1, tzinfo=dt_timezone.utc)
        first.updated_at = datetime(2026, 10, 2, 0, 0, 0, 500, tzinfo=dt_timezone.utc)

        assert Announcement.signature([second, first]) == "4:1790899200.000500,9:1790812800.000000"

    def it_changes_when_a_row_is_saved_again():
        announcement = AnnouncementFactory()
        before = Announcement.signature([announcement])

        announcement.save()

        assert Announcement.signature([announcement]) != before


def describe_text_and_body():
    def it_reads_the_visible_words():
        announcement = AnnouncementFactory.build(body="<p><strong>Open</strong> house &amp; tea</p><p>Saturday</p>")

        assert announcement.text == "Open house & tea Saturday"
        assert str(announcement) == "Open house & tea Saturday"

    def it_names_itself_by_its_first_sixty_characters():
        announcement = AnnouncementFactory.build(body=f"<p>{'x' * 70}</p>")

        assert str(announcement) == "x" * 60

    def it_sanitizes_the_body_again_on_the_way_out():
        announcement = AnnouncementFactory.build(body='<p>Hi <img src="x.png"><script>alert(1)</script></p>')

        assert announcement.body_html == "<p>Hi </p>"

    def it_records_who_added_it_and_lets_them_go():
        user = User.objects.create_user(username="zorblax-author")
        announcement = AnnouncementFactory(created_by=user)

        user.delete()
        announcement.refresh_from_db()

        assert announcement.created_by is None


def describe_sanitize_announcement_html():
    def it_keeps_paragraphs_breaks_bold_italic_and_links():
        html = '<p><strong>Bold</strong> <em>it</em><br><a href="/classes/">classes</a></p>'

        assert sanitize_announcement_html(html) == (
            '<p><strong>Bold</strong> <em>it</em><br><a href="/classes/" rel="noopener noreferrer">classes</a></p>'
        )

    def it_drops_scripts_and_styles_with_their_contents():
        html = "<p>Hi<script>alert(1)</script><style>p{color:red}</style></p>"

        assert sanitize_announcement_html(html) == "<p>Hi</p>"

    def it_drops_images_headings_lists_and_underline_keeping_their_words():
        html = '<h2>Title</h2><ul><li>one</li></ul><p><u>under</u><img src="a.png" alt="x"></p>'

        assert sanitize_announcement_html(html) == "Title\n\none<p>under</p>"

    def it_drops_attributes_outside_the_allowlist():
        html = '<p class="big" style="color:red" onclick="x()"><strong data-x="1">Hi</strong></p>'

        assert sanitize_announcement_html(html) == "<p><strong>Hi</strong></p>"

    def it_opens_an_external_link_in_a_new_tab():
        html = '<p><a href="https://example.org/x" target="_self" rel="opener" title="t">out</a></p>'

        assert sanitize_announcement_html(html) == (
            '<p><a href="https://example.org/x" rel="noopener noreferrer" target="_blank">out</a></p>'
        )

    @pytest.mark.parametrize(
        "href", ["HTTP://example.org", "//example.org/x", " https://example.org", "/\\example.org", "\\\\example.org"]
    )
    def it_treats_any_absolute_web_address_as_external(href: str):
        assert 'target="_blank"' in sanitize_announcement_html(f'<p><a href="{href}">out</a></p>')

    @pytest.mark.parametrize("href", ["/classes/", "#top", "mailto:hi@example.org", "tel:5035550100"])
    def it_keeps_a_link_inside_the_site_in_place(href: str):
        assert 'target="_blank"' not in sanitize_announcement_html(f'<p><a href="{href}">here</a></p>')

    def it_drops_a_javascript_link_and_keeps_the_anchor_in_place():
        assert sanitize_announcement_html('<p><a href="javascript:alert(1)">x</a></p>') == (
            '<p><a rel="noopener noreferrer">x</a></p>'
        )

    def it_links_a_bare_address():
        assert sanitize_announcement_html("<p>See example.org</p>") == (
            '<p>See <a href="http://example.org" rel="noopener noreferrer" target="_blank">example.org</a></p>'
        )

    @pytest.mark.parametrize("raw", ["", "   ", "<p><br></p>", "<p><script>x</script></p>"])
    def it_answers_empty_for_input_with_no_words(raw: str):
        assert sanitize_announcement_html(raw) == ""


def it_limits_an_announcement_to_160_characters():
    assert ANNOUNCEMENT_MAX_CHARS == 160
