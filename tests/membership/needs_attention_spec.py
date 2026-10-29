"""Needs attention (#953): what counts as a guild having something coming up, and the daily check
that puts an inbox item in front of its leads and staff when it has nothing."""

from __future__ import annotations

import logging
from datetime import time, timedelta
from io import StringIO
from unittest import mock

import pytest
from django.core.management import call_command
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from classes.factories import UserFactory
from core.models import EventDelivery, Notification
from membership import needs_attention
from membership.models import (
    CalendarEvent,
    CommunityEvent,
    Guild,
    GuildNeedsAttention,
    GuildStaffMembership,
    Member,
    NextMeetingSources,
)
from membership.needs_attention import RULES, NeedsAttentionCheckError, check_all, clear_fixed
from tests.membership.factories import (
    CommunityEventFactory,
    GuildFactory,
    GuildStaffMembershipFactory,
    MeetingFactory,
    MemberFactory,
)

pytestmark = pytest.mark.django_db

BODY = "No upcoming meeting has been set for your guild yet. Would you like to set one up now?"
KEY = "guild.needs_attention"


def _google(
    title: str, *, days: float = 3, source: str = CalendarEvent.Source.GENERAL, **extra: object
) -> CalendarEvent:
    """A Google calendar row from the general feed, with no guild, ``days`` from now."""
    start = timezone.now() + timedelta(days=days)
    return CalendarEvent.objects.create(
        uid=f"{title}-{days}@google.com",
        source=source,
        title=title,
        start_dt=start,
        end_dt=start + timedelta(hours=2),
        fetched_at=timezone.now(),
        **extra,
    )


def _loaded(guild: Guild) -> Guild:
    return Guild.objects.prefetch_related("events").get(pk=guild.pk)


def _bells(user_pk: int) -> list[Notification]:
    return list(Notification.objects.filter(user_id=user_pk, trigger=KEY).order_by("created_at", "pk"))


def _lead(guild: Guild) -> Member:
    member = UserFactory().member
    guild.guild_lead = member
    guild.save(update_fields=["guild_lead"])
    return member


def _staff(guild: Guild, member: Member | None = None) -> Member:
    member = member or UserFactory().member
    GuildStaffMembershipFactory(guild=guild, member=member)
    return member


def _age_first_items(days: int) -> None:
    """Pretend the first items went out ``days`` ago."""
    EventDelivery.objects.filter(event_key=KEY).exclude(period__endswith=":reminder").update(
        created_at=timezone.now() - timedelta(days=days)
    )


def describe_something_coming_up():
    def it_counts_a_guild_marked_tba_though_the_card_still_reads_tba():
        guild = GuildFactory(meeting_is_tba=True)
        assert guild.has_something_coming_up() is True
        assert guild.next_meeting_occurrence() is None

    def it_counts_nothing_for_a_bare_guild():
        assert GuildFactory().has_something_coming_up() is False

    def it_counts_the_cadence_override():
        guild = GuildFactory(meeting_next_override=timezone.localdate() + timedelta(days=4))
        assert guild.has_something_coming_up() is True

    def it_counts_an_upcoming_meeting_row_with_its_time():
        guild = GuildFactory()
        MeetingFactory(guild=guild, scheduled_date=timezone.localdate() + timedelta(days=6), scheduled_time=time(19))
        result = guild.next_meeting_occurrence()
        assert result is not None
        assert timezone.localtime(result.when).date() == timezone.localdate() + timedelta(days=6)
        assert result.has_time is True
        assert result.is_meeting is True
        assert result.title == ""

    def it_counts_an_untimed_meeting_row_as_a_date_only():
        guild = GuildFactory()
        MeetingFactory(guild=guild, scheduled_date=timezone.localdate() + timedelta(days=6))
        result = guild.next_meeting_occurrence()
        assert result is not None
        assert result.has_time is False

    def it_ignores_a_past_meeting_row():
        guild = GuildFactory()
        MeetingFactory(guild=guild, scheduled_date=timezone.localdate() - timedelta(days=6))
        assert guild.has_something_coming_up() is False

    def it_counts_any_published_guild_event_with_its_title():
        guild = GuildFactory()
        start = timezone.now() + timedelta(days=2)
        CommunityEventFactory(
            guild=guild,
            title="Seed Swap",
            event_type=CommunityEvent.EventType.COMMUNITY,
            starts_at=start,
            ends_at=start + timedelta(hours=2),
        )
        result = _loaded(guild).next_meeting_occurrence()
        assert result is not None
        assert result.title == "Seed Swap"
        assert result.is_meeting is False

    def it_marks_a_guild_meeting_event_as_a_meeting():
        guild = GuildFactory()
        start = timezone.now() + timedelta(days=2)
        CommunityEventFactory(guild_meeting=True, guild=guild, starts_at=start, ends_at=start + timedelta(hours=1))
        result = _loaded(guild).next_meeting_occurrence()
        assert result is not None
        assert result.is_meeting is True

    def it_ignores_an_unpublished_event():
        guild = GuildFactory()
        start = timezone.now() + timedelta(days=2)
        CommunityEventFactory(
            guild=guild,
            starts_at=start,
            ends_at=start + timedelta(hours=2),
            moderation_state=CommunityEvent.ModerationState.PENDING,
        )
        assert _loaded(guild).has_something_coming_up() is False

    def it_ignores_a_past_one_off_event():
        guild = GuildFactory()
        start = timezone.now() - timedelta(days=3)
        CommunityEventFactory(guild=guild, starts_at=start, ends_at=start + timedelta(hours=2))
        assert _loaded(guild).has_something_coming_up() is False

    def it_skips_a_weekly_occurrence_that_already_started_today():
        guild = GuildFactory()
        anchor = timezone.now() - timedelta(days=7, minutes=1)
        CommunityEventFactory(
            studio_hours=True,
            guild=guild,
            recurrence=CommunityEvent.Recurrence.WEEKLY,
            starts_at=anchor,
            ends_at=anchor + timedelta(hours=3),
        )
        result = _loaded(guild).next_meeting_occurrence()
        assert result is not None
        assert result.when > timezone.now()

    def it_counts_a_google_row_naming_the_guild_in_any_case():
        guild = GuildFactory(name="Food Independence Guild")
        _google("Monthly Pot Luck sponsored by FOOD independence guild", location="Kitchen")
        result = guild.next_meeting_occurrence()
        assert result is not None
        assert result.title == "Monthly Pot Luck sponsored by FOOD independence guild"
        assert result.location == "Kitchen"
        assert result.has_time is True
        assert result.is_meeting is False

    def it_reads_an_all_day_google_row_as_a_date_only():
        guild = GuildFactory(name="Gardeners Guild")
        _google("Gardeners Guild Work Day", all_day=True)
        result = guild.next_meeting_occurrence()
        assert result is not None
        assert result.has_time is False

    def it_ignores_google_rows_that_do_not_name_the_guild():
        guild = GuildFactory(name="Writers Guild")
        _google("Woodworking Guild Studio Hours")
        assert guild.has_something_coming_up() is False

    def it_ignores_class_rows_and_past_google_rows():
        guild = GuildFactory(name="Glass Guild")
        _google("Glass Guild Intro Class", source=CalendarEvent.Source.CLASSES)
        _google("Glass Guild Open Studio", days=-1)
        assert guild.has_something_coming_up() is False

    def it_never_writes_to_a_google_row():
        guild = GuildFactory(name="Textiles Guild")
        row = _google("Textiles Guild Sew Night")
        guild.next_meeting_occurrence()
        row.refresh_from_db()
        assert row.guild_id is None
        assert row.title == "Textiles Guild Sew Night"

    def it_shows_the_soonest_source():
        guild = GuildFactory(name="Events Guild", meeting_next_override=timezone.localdate() + timedelta(days=20))
        MeetingFactory(guild=guild, scheduled_date=timezone.localdate() + timedelta(days=10))
        _google("Events Guild Planning", days=5)
        result = guild.next_meeting_occurrence()
        assert result is not None
        assert result.title == "Events Guild Planning"

    def it_does_not_count_a_name_word_without_guild():
        # The match is the full guild name, so a word of it in a title is not enough.
        guild = GuildFactory(name="Events Guild")
        _google("Holiday Events Planning")
        assert guild.has_something_coming_up() is False

    def it_drops_a_timed_meeting_from_earlier_today():
        guild = GuildFactory()
        start = timezone.now() + timedelta(hours=3)
        CommunityEventFactory(
            guild=guild,
            event_type=CommunityEvent.EventType.COMMUNITY,
            starts_at=start,
            ends_at=start + timedelta(hours=1),
        )
        MeetingFactory(guild=guild, scheduled_date=timezone.localdate(), scheduled_time=time(0, 0))
        result = _loaded(guild).next_meeting_occurrence()
        assert result is not None
        assert result.when == start

    def it_counts_an_untimed_meeting_dated_today_for_today():
        guild = GuildFactory()
        MeetingFactory(guild=guild, scheduled_date=timezone.localdate())
        result = guild.next_meeting_occurrence()
        assert result is not None
        assert timezone.localtime(result.when).date() == timezone.localdate()
        assert result.has_time is False


def describe_meetings_only():
    def it_skips_studio_hours_for_the_monthly_meeting():
        guild = GuildFactory(meeting_next_override=timezone.localdate() + timedelta(days=12), meeting_time=time(19))
        start = timezone.now() + timedelta(hours=2)
        CommunityEventFactory(
            studio_hours=True,
            guild=guild,
            recurrence=CommunityEvent.Recurrence.WEEKLY,
            starts_at=start,
            ends_at=start + timedelta(hours=3),
        )
        guild = _loaded(guild)
        assert guild.next_meeting_occurrence().is_meeting is False
        meeting = guild.next_meeting_occurrence(meetings_only=True)
        assert meeting is not None
        assert timezone.localtime(meeting.when).date() == timezone.localdate() + timedelta(days=12)

    def it_skips_google_rows_and_keeps_meeting_rows():
        guild = GuildFactory(name="Writers Guild")
        _google("Writers Guild Read Night", days=1)
        MeetingFactory(guild=guild, scheduled_date=timezone.localdate() + timedelta(days=8))
        meeting = guild.next_meeting_occurrence(meetings_only=True)
        assert meeting is not None
        assert meeting.title == ""
        assert timezone.localtime(meeting.when).date() == timezone.localdate() + timedelta(days=8)

    def it_is_none_for_a_guild_with_only_studio_hours():
        guild = GuildFactory()
        start = timezone.now() + timedelta(days=1)
        CommunityEventFactory(studio_hours=True, guild=guild, starts_at=start, ends_at=start + timedelta(hours=3))
        assert _loaded(guild).next_meeting_occurrence(meetings_only=True) is None


def describe_next_meeting_sources():
    def it_loads_nothing_for_no_guilds(django_assert_num_queries):
        with django_assert_num_queries(0):
            sources = NextMeetingSources.load([])
        assert sources.meetings == {}
        assert sources.calendar_events == []

    def it_loads_every_guild_in_two_queries(django_assert_num_queries):
        guilds = [GuildFactory(name=f"Batch Guild {n}") for n in range(5)]
        for guild in guilds:
            MeetingFactory(guild=guild)
            _google(f"{guild.name} night")
        with django_assert_num_queries(2):
            sources = NextMeetingSources.load(guilds)
        assert set(sources.meetings) == {guild.pk for guild in guilds}
        loaded = [_loaded(guild) for guild in guilds]
        with django_assert_num_queries(0):
            for guild in loaded:
                assert guild.next_meeting_occurrence(sources) is not None

    def it_keeps_the_soonest_meeting_row_per_guild():
        guild = GuildFactory()
        MeetingFactory(guild=guild, scheduled_date=timezone.localdate() + timedelta(days=9))
        soonest = MeetingFactory(guild=guild, scheduled_date=timezone.localdate() + timedelta(days=2))
        assert NextMeetingSources.load([guild]).meetings[guild.pk] == soonest

    def it_reads_no_meeting_rows_without_meetings_but_keeps_the_google_rows(django_assert_num_queries):
        guild = GuildFactory(name="Quiet Guild")
        MeetingFactory(guild=guild)
        google = _google("Quiet Guild night")
        with django_assert_num_queries(1):
            sources = NextMeetingSources.load([guild], with_meetings=False)
        assert sources.meetings == {}
        assert sources.calendar_events == [google]


def describe_check_all():
    def it_gives_each_lead_and_staff_member_one_item_naming_the_guild():
        guild = GuildFactory(name="Writers Guild")
        lead = _lead(guild)
        staff = _staff(guild)
        result = check_all()
        for member in (lead, staff):
            [bell] = _bells(member.user_id)
            assert bell.title == "Needs attention: Writers Guild"
            assert bell.body == BODY
            assert bell.url == f"{reverse('hub_guild_edit', args=[guild.pk])}?tab=meetings"
        assert result.open == 1
        assert result.items == 2

    def it_sends_no_email_and_no_push():
        guild = GuildFactory()
        _lead(guild)
        check_all()
        channels = set(EventDelivery.objects.filter(event_key=KEY).values_list("channel", flat=True))
        assert channels == {"in_app"}

    def it_does_not_repeat_while_the_item_is_open():
        guild = GuildFactory()
        lead = _lead(guild)
        check_all()
        result = check_all()
        assert len(_bells(lead.user_id)) == 1
        assert result.items == 0
        assert GuildNeedsAttention.objects.open().count() == 1

    def it_gives_someone_who_staffs_two_such_guilds_one_item_per_guild():
        person = UserFactory().member
        first = GuildFactory(name="Glass Guild")
        second = GuildFactory(name="Textiles Guild")
        _staff(first, person)
        _staff(second, person)
        check_all()
        assert sorted(bell.title for bell in _bells(person.user_id)) == [
            "Needs attention: Glass Guild",
            "Needs attention: Textiles Guild",
        ]

    def it_sends_nothing_for_a_guild_with_no_lead_or_staff():
        GuildFactory()
        result = check_all()
        assert result.open == 1
        assert result.items == 0
        assert not Notification.objects.filter(trigger=KEY).exists()

    def it_skips_a_lead_with_no_login():
        guild = GuildFactory()
        guild.guild_lead = MemberFactory()
        guild.save(update_fields=["guild_lead"])
        assert guild.guild_lead.user_id is None
        assert check_all().items == 0

    def it_leaves_out_admins_who_do_not_run_the_guild():
        admin = UserFactory().member
        admin.fog_role = Member.FogRole.ADMIN
        admin.save(update_fields=["fog_role"])
        guild = GuildFactory()
        _lead(guild)
        check_all()
        assert _bells(admin.user_id) == []

    def it_leaves_alone_a_guild_with_something_coming_up():
        guild = GuildFactory(meeting_is_tba=True)
        lead = _lead(guild)
        result = check_all()
        assert _bells(lead.user_id) == []
        assert result.open == 0
        assert not GuildNeedsAttention.objects.exists()

    def it_skips_hidden_guilds():
        guild = GuildFactory(is_active=False)
        lead = _lead(guild)
        assert check_all().guilds == 0
        assert _bells(lead.user_id) == []

    def it_checks_the_other_guilds_when_one_fails_then_raises_once(caplog):
        broken = GuildFactory(name="Broken Guild")
        GuildNeedsAttention.objects.create(guild=broken, rule="nothing_coming_up")
        fine = GuildFactory(name="Fine Guild")
        lead = _lead(fine)
        real_nudge = needs_attention._nudge

        def nudge(guild, rule, row):
            if guild.pk == broken.pk:
                raise RuntimeError("boom")
            return real_nudge(guild, rule, row)

        with mock.patch.object(needs_attention, "_nudge", side_effect=nudge), caplog.at_level(logging.ERROR):
            with pytest.raises(NeedsAttentionCheckError) as raised:
                check_all()
        assert raised.value.result.failed_guild_pks == [broken.pk]
        assert str(broken.pk) in str(raised.value)
        assert f"guild pk={broken.pk}" in caplog.text
        assert len(_bells(lead.user_id)) == 1
        assert GuildNeedsAttention.objects.open().filter(guild=broken).exists()

    def describe_the_reminder():
        def it_reminds_once_30_days_on_and_reads_the_first_item():
            guild = GuildFactory()
            lead = _lead(guild)
            check_all()
            _age_first_items(30)
            result = check_all()
            first, reminder = _bells(lead.user_id)
            assert result.reminders == 1
            assert first.read_at is not None
            assert reminder.read_at is None
            assert reminder.body == BODY
            assert check_all().reminders == 0
            assert len(_bells(lead.user_id)) == 2

        def it_does_not_remind_someone_removed_from_staff():
            guild = GuildFactory()
            keep = _staff(guild)
            gone = _staff(guild)
            check_all()
            GuildStaffMembership.objects.filter(guild=guild, member=gone).delete()
            _age_first_items(31)
            assert check_all().reminders == 1
            assert len(_bells(keep.user_id)) == 2
            assert len(_bells(gone.user_id)) == 1

        def it_marks_a_removed_persons_open_item_read():
            guild = GuildFactory()
            keep = _staff(guild)
            gone = _staff(guild)
            check_all()
            GuildStaffMembership.objects.filter(guild=guild, member=gone).delete()
            check_all()
            assert _bells(gone.user_id)[0].read_at is not None
            assert _bells(keep.user_id)[0].read_at is None

        def it_waits_the_full_30_days():
            guild = GuildFactory()
            lead = _lead(guild)
            check_all()
            _age_first_items(29)
            assert check_all().reminders == 0
            assert len(_bells(lead.user_id)) == 1

        def it_counts_from_each_persons_own_first_item():
            guild = GuildFactory()
            _lead(guild)
            check_all()
            _age_first_items(31)
            newcomer = _staff(guild)
            result = check_all()
            assert result.items == 1
            assert result.reminders == 1
            assert len(_bells(newcomer.user_id)) == 1

    def describe_clearing():
        def it_marks_the_items_read_and_closes_the_stretch_once_something_is_set():
            guild = GuildFactory()
            lead = _lead(guild)
            staff = _staff(guild)
            check_all()
            MeetingFactory(guild=guild)
            result = check_all()
            assert result.cleared == 1
            assert all(bell.read_at is not None for bell in _bells(lead.user_id) + _bells(staff.user_id))
            assert not GuildNeedsAttention.objects.open().exists()

        def it_starts_over_when_the_guild_runs_dry_again():
            guild = GuildFactory()
            lead = _lead(guild)
            check_all()
            meeting = MeetingFactory(guild=guild)
            check_all()
            meeting.delete()
            check_all()
            bells = _bells(lead.user_id)
            assert len(bells) == 2
            assert bells[-1].read_at is None
            assert GuildNeedsAttention.objects.count() == 2

        def it_clears_a_guild_that_was_hidden():
            guild = GuildFactory()
            lead = _lead(guild)
            check_all()
            Guild.objects.filter(pk=guild.pk).update(is_active=False)
            assert check_all().cleared == 1
            assert _bells(lead.user_id)[0].read_at is not None

        def it_clears_a_stretch_whose_rule_was_removed():
            guild = GuildFactory()
            GuildNeedsAttention.objects.create(guild=guild, rule="retired_rule")
            assert check_all().cleared == 1
            assert not GuildNeedsAttention.objects.filter(rule="retired_rule", cleared_at__isnull=True).exists()


def describe_clear_fixed():
    def it_looks_once_and_does_nothing_when_nothing_is_open(django_assert_num_queries):
        guild = GuildFactory()
        with django_assert_num_queries(1):
            assert clear_fixed(guild) == 0

    def it_clears_a_guild_fixed_since_the_last_run():
        guild = GuildFactory()
        lead = _lead(guild)
        check_all()
        Guild.objects.filter(pk=guild.pk).update(meeting_is_tba=True)
        assert clear_fixed(guild) == 1
        assert _bells(lead.user_id)[0].read_at is not None

    def it_keeps_a_guild_that_is_still_empty():
        guild = GuildFactory()
        _lead(guild)
        check_all()
        assert clear_fixed(guild) == 0
        assert GuildNeedsAttention.objects.open().count() == 1

    def it_clears_a_stretch_whose_rule_was_removed():
        guild = GuildFactory(meeting_is_tba=False)
        GuildNeedsAttention.objects.create(guild=guild, rule="retired_rule")
        assert clear_fixed(guild) == 1

    def it_runs_when_guild_settings_are_saved(client: Client):
        guild = GuildFactory()
        lead = _lead(guild)
        check_all()
        client.force_login(lead.user)
        response = client.post(
            reverse("hub_guild_edit", args=[guild.pk]),
            data={"name": guild.name, "about": "", "meeting_is_tba": "on"},
        )
        assert response.status_code == 302
        assert _bells(lead.user_id)[0].read_at is not None


def describe_rules():
    def it_keeps_one_list_whose_first_rule_is_nothing_coming_up():
        assert [rule.key for rule in RULES] == ["nothing_coming_up"]
        assert RULES[0].body == BODY

    def it_gives_each_rule_its_own_link():
        guild = GuildFactory()
        urls = [rule.url(guild.pk) for rule in RULES]
        assert len(urls) == len(set(urls))


def describe_the_command():
    def it_reports_what_it_did():
        guild = GuildFactory()
        _lead(guild)
        out = StringIO()
        call_command("check_needs_attention", stdout=out)
        assert out.getvalue().strip() == (
            "1 guild(s) checked; 1 need attention; 1 item(s) and 0 reminder(s) sent; 0 cleared."
        )

    def it_names_the_stretch_in_the_admin():
        row = GuildNeedsAttention.objects.create(
            guild=GuildFactory(name="Prison Outreach Guild"), rule="nothing_coming_up"
        )
        assert str(row) == "Prison Outreach Guild: nothing_coming_up (open)"
        row.cleared_at = timezone.now()
        assert "cleared" in str(row)
