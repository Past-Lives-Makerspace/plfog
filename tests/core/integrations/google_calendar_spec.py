"""BDD specs for the Google Calendar push service.

All mocked — these NEVER hit Google. The client is either patched at
``GoogleCalendarClient.from_settings`` or constructed with an injected fake ``service``.
"""

from __future__ import annotations

import base64
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone
from googleapiclient.errors import HttpError

from core.integrations import google_calendar as gc
from core.models import SiteConfiguration
from membership.models import CommunityEvent
from tests.membership.factories import CommunityEventFactory, GuildFactory

CALENDAR_ID = "storedcal@group.calendar.google.com"  # a previously-stored calendar id (remove/delete cases)
MEMBER_CALENDAR_ID = "member@group.calendar.google.com"
PUBLIC_CALENDAR_ID = "public@group.calendar.google.com"


def _enable_config() -> SiteConfiguration:
    """Turn the admin runtime toggle on (the second sync gate) and set both target calendars."""
    config = SiteConfiguration.load()
    config.google_calendar_sync_enabled = True
    config.member_google_calendar_id = MEMBER_CALENDAR_ID
    config.public_google_calendar_id = PUBLIC_CALENDAR_ID
    config.save(
        update_fields=[
            "google_calendar_sync_enabled",
            "member_google_calendar_id",
            "public_google_calendar_id",
        ]
    )
    return config


def _fake_client(*, enabled: bool = True, **methods: MagicMock) -> MagicMock:
    client = MagicMock(spec=gc.GoogleCalendarClient)
    client.enabled = enabled
    client.insert_event = methods.get(
        "insert_event", MagicMock(return_value={"id": "gid", "iCalUID": "gid@google.com"})
    )
    client.update_event = methods.get(
        "update_event", MagicMock(return_value={"id": "gid", "iCalUID": "gid@google.com"})
    )
    client.delete_event = methods.get("delete_event", MagicMock(return_value=None))
    return client


def _http_error(status: int = 500) -> HttpError:
    resp = MagicMock(status=status, reason="Error")
    return HttpError(resp=resp, content=b'{"error": {"message": "boom"}}')


@pytest.mark.django_db
def describe_push_community_event():
    def describe_when_sync_is_disabled():
        def it_marks_pending_with_a_reason_and_makes_no_api_call():
            # Config toggle off (default) → disabled regardless of a valid target.
            event = CommunityEventFactory()
            client = _fake_client(enabled=False)
            with patch.object(gc.GoogleCalendarClient, "from_settings", return_value=client):
                gc.push_community_event(event)
            assert event.sync_state == CommunityEvent.SyncState.PENDING
            assert event.sync_error == gc.GoogleCalendarConfig.SYNC_OFF
            client.insert_event.assert_not_called()
            client.update_event.assert_not_called()

    def describe_when_no_target_calendar():
        def it_marks_pending_when_the_member_calendar_is_unset():
            config = SiteConfiguration.load()
            config.google_calendar_sync_enabled = True
            config.member_google_calendar_id = ""
            config.save(update_fields=["google_calendar_sync_enabled", "member_google_calendar_id"])
            event = CommunityEventFactory(google_calendar_target=CommunityEvent.GoogleCalendarTarget.MEMBER)
            with patch.object(gc.GoogleCalendarClient, "from_settings", return_value=_fake_client()):
                gc.push_community_event(event)
            assert event.sync_state == CommunityEvent.SyncState.PENDING
            assert event.sync_error == gc.GoogleCalendarConfig.NO_CALENDAR

        def it_marks_pending_when_the_public_calendar_is_unset():
            config = SiteConfiguration.load()
            config.google_calendar_sync_enabled = True
            config.public_google_calendar_id = ""
            config.save(update_fields=["google_calendar_sync_enabled", "public_google_calendar_id"])
            event = CommunityEventFactory(google_calendar_target=CommunityEvent.GoogleCalendarTarget.PUBLIC)
            with patch.object(gc.GoogleCalendarClient, "from_settings", return_value=_fake_client()):
                gc.push_community_event(event)
            assert event.sync_state == CommunityEvent.SyncState.PENDING
            assert event.sync_error == gc.GoogleCalendarConfig.NO_CALENDAR

    def describe_target_calendar():
        def it_routes_a_member_event_to_the_member_calendar():
            _enable_config()
            event = CommunityEventFactory(google_calendar_target=CommunityEvent.GoogleCalendarTarget.MEMBER)
            insert = MagicMock(return_value={"id": "m1", "iCalUID": "m1@google.com"})
            client = _fake_client(insert_event=insert)
            with patch.object(gc.GoogleCalendarClient, "from_settings", return_value=client):
                gc.push_community_event(event)
            assert insert.call_args.args[0] == MEMBER_CALENDAR_ID
            assert event.google_calendar_id == MEMBER_CALENDAR_ID

        def it_routes_a_public_event_to_the_public_calendar():
            _enable_config()
            event = CommunityEventFactory(google_calendar_target=CommunityEvent.GoogleCalendarTarget.PUBLIC)
            insert = MagicMock(return_value={"id": "p1", "iCalUID": "p1@google.com"})
            client = _fake_client(insert_event=insert)
            with patch.object(gc.GoogleCalendarClient, "from_settings", return_value=client):
                gc.push_community_event(event)
            assert insert.call_args.args[0] == PUBLIC_CALENDAR_ID
            assert event.google_calendar_id == PUBLIC_CALENDAR_ID

        def it_routes_a_guild_event_by_target_not_by_guild():
            # Retired per-guild calendars: a guild event follows its target like any other event.
            _enable_config()
            event = CommunityEventFactory(
                guild=GuildFactory(), google_calendar_target=CommunityEvent.GoogleCalendarTarget.MEMBER
            )
            insert = MagicMock(return_value={"id": "g1", "iCalUID": "g1@google.com"})
            client = _fake_client(insert_event=insert)
            with patch.object(gc.GoogleCalendarClient, "from_settings", return_value=client):
                gc.push_community_event(event)
            assert insert.call_args.args[0] == MEMBER_CALENDAR_ID

    def describe_insert():
        def it_inserts_and_stores_the_returned_ids():
            _enable_config()
            event = CommunityEventFactory()  # rides the model default target = PUBLIC
            insert = MagicMock(return_value={"id": "new123", "iCalUID": "new123@google.com"})
            client = _fake_client(insert_event=insert)
            with patch.object(gc.GoogleCalendarClient, "from_settings", return_value=client):
                gc.push_community_event(event)
            insert.assert_called_once()
            assert insert.call_args.args[0] == PUBLIC_CALENDAR_ID
            assert event.google_event_id == "new123"
            assert event.google_ical_uid == "new123@google.com"
            assert event.google_calendar_id == PUBLIC_CALENDAR_ID
            assert event.sync_state == CommunityEvent.SyncState.SYNCED
            assert event.synced_at is not None
            assert event.sync_error == ""
            client.update_event.assert_not_called()

        def it_synthesizes_an_ical_uid_when_google_omits_it():
            _enable_config()
            event = CommunityEventFactory()
            client = _fake_client(insert_event=MagicMock(return_value={"id": "onlyid"}))
            with patch.object(gc.GoogleCalendarClient, "from_settings", return_value=client):
                gc.push_community_event(event)
            assert event.google_ical_uid == "onlyid@google.com"

    def describe_update():
        def it_updates_an_already_pushed_event_instead_of_inserting():
            _enable_config()
            event = CommunityEventFactory(google_event_id="existing1", google_calendar_id=PUBLIC_CALENDAR_ID)
            update = MagicMock(return_value={"id": "existing1", "iCalUID": "existing1@google.com"})
            client = _fake_client(update_event=update)
            with patch.object(gc.GoogleCalendarClient, "from_settings", return_value=client):
                gc.push_community_event(event)
            update.assert_called_once()
            assert update.call_args.args[:2] == (PUBLIC_CALENDAR_ID, "existing1")
            client.insert_event.assert_not_called()
            client.delete_event.assert_not_called()
            assert event.sync_state == CommunityEvent.SyncState.SYNCED

    def describe_target_change_after_push():
        @pytest.fixture
        def moved_event():
            _enable_config()
            return CommunityEventFactory(
                google_calendar_target=CommunityEvent.GoogleCalendarTarget.MEMBER,
                google_event_id="oldid",
                google_ical_uid="oldid@google.com",
                google_calendar_id=PUBLIC_CALENDAR_ID,
            )

        def it_deletes_the_old_copy_and_inserts_fresh_on_the_new_calendar(moved_event):
            insert = MagicMock(return_value={"id": "newid", "iCalUID": "newid@google.com"})
            delete = MagicMock()
            client = _fake_client(insert_event=insert, delete_event=delete)
            with patch.object(gc.GoogleCalendarClient, "from_settings", return_value=client):
                gc.push_community_event(moved_event)
            delete.assert_called_once_with(PUBLIC_CALENDAR_ID, "oldid")
            client.update_event.assert_not_called()
            assert insert.call_args.args[0] == MEMBER_CALENDAR_ID
            assert moved_event.google_event_id == "newid"
            assert moved_event.google_ical_uid == "newid@google.com"
            assert moved_event.google_calendar_id == MEMBER_CALENDAR_ID
            assert moved_event.sync_state == CommunityEvent.SyncState.SYNCED

        def it_tolerates_an_already_gone_copy_on_the_old_calendar(moved_event):
            gone = gc.GoogleCalendarError("not found")
            gone.__cause__ = _http_error(404)
            insert = MagicMock(return_value={"id": "newid", "iCalUID": "newid@google.com"})
            client = _fake_client(insert_event=insert, delete_event=MagicMock(side_effect=gone))
            with patch.object(gc.GoogleCalendarClient, "from_settings", return_value=client):
                gc.push_community_event(moved_event)
            assert insert.call_args.args[0] == MEMBER_CALENDAR_ID
            assert moved_event.google_event_id == "newid"
            assert moved_event.sync_state == CommunityEvent.SyncState.SYNCED

        def it_keeps_the_stored_ids_and_marks_failed_when_the_old_delete_fails(moved_event):
            boom = gc.GoogleCalendarError("boom")
            boom.__cause__ = _http_error(500)
            client = _fake_client(delete_event=MagicMock(side_effect=boom))
            with patch.object(gc.GoogleCalendarClient, "from_settings", return_value=client):
                gc.push_community_event(moved_event)  # must not raise
            client.insert_event.assert_not_called()
            client.update_event.assert_not_called()
            assert moved_event.google_event_id == "oldid"
            assert moved_event.google_calendar_id == PUBLIC_CALENDAR_ID
            assert moved_event.sync_state == CommunityEvent.SyncState.FAILED

    def describe_failure():
        def it_marks_failed_with_a_truncated_error_and_never_raises():
            _enable_config()
            event = CommunityEventFactory()
            long_error = gc.GoogleCalendarError("x" * 900)
            client = _fake_client(insert_event=MagicMock(side_effect=long_error))
            with patch.object(gc.GoogleCalendarClient, "from_settings", return_value=client):
                gc.push_community_event(event)  # must not raise
            assert event.sync_state == CommunityEvent.SyncState.FAILED
            assert len(event.sync_error) == 500
            assert event.google_event_id == ""


@pytest.mark.django_db
def describe_remove_community_event():
    def it_deletes_when_ids_are_present():
        _enable_config()
        event = CommunityEventFactory(google_event_id="gid1", google_calendar_id=CALENDAR_ID)
        delete = MagicMock()
        client = _fake_client(delete_event=delete)
        with patch.object(gc.GoogleCalendarClient, "from_settings", return_value=client):
            gc.remove_community_event(event)
        delete.assert_called_once_with(CALENDAR_ID, "gid1")

    def it_swallows_a_delete_error():
        _enable_config()
        event = CommunityEventFactory(google_event_id="gid1", google_calendar_id=CALENDAR_ID)
        client = _fake_client(delete_event=MagicMock(side_effect=gc.GoogleCalendarError("gone")))
        with patch.object(gc.GoogleCalendarClient, "from_settings", return_value=client):
            gc.remove_community_event(event)  # must not raise

    def it_is_a_noop_when_disabled():
        event = CommunityEventFactory(google_event_id="gid1", google_calendar_id=CALENDAR_ID)
        client = _fake_client(enabled=False)
        with patch.object(gc.GoogleCalendarClient, "from_settings", return_value=client):
            gc.remove_community_event(event)
        client.delete_event.assert_not_called()

    def it_is_a_noop_when_the_event_was_never_pushed():
        _enable_config()
        event = CommunityEventFactory()  # blank google_event_id
        client = _fake_client()
        with patch.object(gc.GoogleCalendarClient, "from_settings", return_value=client):
            gc.remove_community_event(event)
        client.delete_event.assert_not_called()


@pytest.mark.django_db
def describe__build_event_body():
    def _event(**kwargs) -> CommunityEvent:
        defaults = dict(
            title="Forge Night",
            location="Bay 3",
            description="Bring a project.",
            starts_at=timezone.now() + timedelta(days=3),
            ends_at=timezone.now() + timedelta(days=3, hours=2),
        )
        defaults.update(kwargs)
        return CommunityEventFactory(**defaults)

    def it_appends_the_attribution_from_created_by():
        from django.contrib.auth.models import User

        creator = User.objects.create_user(username="ada", first_name="Ada", last_name="Lovelace", password="x")
        body = gc._build_event_body(_event(created_by=creator), actor=None)
        assert body["description"].endswith("Added by Ada Lovelace via FOG")
        assert body["description"].startswith("Bring a project.")

    def it_falls_back_to_the_submitter_then_the_actor_then_a_generic_name():
        from django.contrib.auth.models import User

        submitter = User.objects.create_user(username="grace", first_name="Grace", last_name="Hopper", password="x")
        by_submitter = gc._build_event_body(_event(created_by=None, submitted_by=submitter), actor=None)
        assert by_submitter["description"].endswith("Added by Grace Hopper via FOG")

        actor = User.objects.create_user(username="alan", first_name="Alan", last_name="Turing", password="x")
        by_actor = gc._build_event_body(_event(created_by=None, submitted_by=None), actor=actor)
        assert by_actor["description"].endswith("Added by Alan Turing via FOG")

        generic = gc._build_event_body(_event(created_by=None, submitted_by=None), actor=None)
        assert generic["description"].endswith("Added by a Past Lives member via FOG")

    def it_falls_back_to_the_username_when_a_user_has_no_name():
        from django.contrib.auth.models import User

        nameless = User.objects.create_user(username="just_username", password="x")
        body = gc._build_event_body(_event(created_by=nameless), actor=None)
        assert body["description"].endswith("Added by just_username via FOG")

    def it_falls_back_to_the_members_display_name_before_the_username():
        from django.contrib.auth.models import User

        from tests.membership.factories import MembershipPlanFactory

        MembershipPlanFactory()  # so the signal links a Member to the new user
        user = User.objects.create_user(username="prefonly", password="x")
        user.member.preferred_name = "Preferred Name"
        user.member.save(update_fields=["preferred_name"])
        body = gc._build_event_body(_event(created_by=user), actor=None)
        assert body["description"].endswith("Added by Preferred Name via FOG")

    def it_uses_the_username_when_the_user_has_no_linked_member():
        # A user with no Member row (getattr returns None) → the login name is the attribution.
        user = MagicMock()
        user.get_full_name.return_value = ""
        user.member = None
        user.get_username.return_value = "bare_login"
        assert gc._display_name(user) == "bare_login"

    def it_uses_timed_datetimes_in_the_portland_timezone():
        body = gc._build_event_body(_event(), actor=None)
        assert body["start"]["timeZone"] == "America/Los_Angeles"
        assert body["end"]["timeZone"] == "America/Los_Angeles"
        assert "dateTime" in body["start"]
        assert "date" not in body["start"]

    def it_omits_recurrence_for_a_one_off_event():
        body = gc._build_event_body(_event(recurrence=CommunityEvent.Recurrence.NONE), actor=None)
        assert "recurrence" not in body

    def it_prepends_a_join_online_line_when_video_url_is_set():
        body = gc._build_event_body(_event(video_url="https://meet.google.com/abc-defg-hij"), actor=None)
        assert body["description"].startswith("Join online: https://meet.google.com/abc-defg-hij")

    def it_omits_the_join_online_line_when_video_url_is_blank():
        body = gc._build_event_body(_event(video_url=""), actor=None)
        assert "Join online:" not in body["description"]

    @pytest.mark.parametrize(
        "recurrence",
        [
            CommunityEvent.Recurrence.MONTHLY,
            CommunityEvent.Recurrence.SEMI_MONTHLY,
            CommunityEvent.Recurrence.EVERY_2_MONTHS,
            CommunityEvent.Recurrence.EVERY_3_MONTHS,
            CommunityEvent.Recurrence.EVERY_6_MONTHS,
            CommunityEvent.Recurrence.YEARLY,
        ],
    )
    def it_round_trips_every_recurrence_flavor_through_ical_rrule(recurrence):
        event = _event(recurrence=recurrence)
        body = gc._build_event_body(event, actor=None)
        assert body["recurrence"] == [f"RRULE:{event.ical_rrule()}"]


@pytest.mark.django_db
def describe_CommunityEvent_push_to_google():
    def it_persists_the_sync_fields_after_a_successful_push():
        _enable_config()
        event = CommunityEventFactory()
        client = _fake_client(insert_event=MagicMock(return_value={"id": "saved1", "iCalUID": "saved1@google.com"}))
        with patch.object(gc.GoogleCalendarClient, "from_settings", return_value=client):
            event.push_to_google()
        event.refresh_from_db()
        assert event.google_event_id == "saved1"
        assert event.sync_state == CommunityEvent.SyncState.SYNCED

    def it_persists_a_failed_state_without_raising():
        _enable_config()
        event = CommunityEventFactory()
        client = _fake_client(insert_event=MagicMock(side_effect=gc.GoogleCalendarError("nope")))
        with patch.object(gc.GoogleCalendarClient, "from_settings", return_value=client):
            event.push_to_google()
        event.refresh_from_db()
        assert event.sync_state == CommunityEvent.SyncState.FAILED

    def it_delegates_removal_to_the_service():
        _enable_config()
        event = CommunityEventFactory(google_event_id="gid9", google_calendar_id=CALENDAR_ID)
        delete = MagicMock()
        client = _fake_client(delete_event=delete)
        with patch.object(gc.GoogleCalendarClient, "from_settings", return_value=client):
            event.remove_from_google()
        delete.assert_called_once_with(CALENDAR_ID, "gid9")


def describe_GoogleCalendarClient():
    def describe_from_settings():
        def it_is_disabled_when_the_flag_is_off(settings):
            settings.GOOGLE_CALENDAR_SYNC_ENABLED = False
            settings.GOOGLE_SERVICE_ACCOUNT_JSON = '{"type": "service_account"}'
            assert gc.GoogleCalendarClient.from_settings().enabled is False

        def it_is_disabled_when_the_creds_are_blank(settings):
            settings.GOOGLE_CALENDAR_SYNC_ENABLED = True
            settings.GOOGLE_SERVICE_ACCOUNT_JSON = ""
            assert gc.GoogleCalendarClient.from_settings().enabled is False

        def it_is_disabled_when_the_creds_are_unparseable(settings):
            settings.GOOGLE_CALENDAR_SYNC_ENABLED = True
            settings.GOOGLE_SERVICE_ACCOUNT_JSON = "not json {"
            assert gc.GoogleCalendarClient.from_settings().enabled is False

        def it_builds_a_service_from_raw_json(settings):
            settings.GOOGLE_CALENDAR_SYNC_ENABLED = True
            settings.GOOGLE_SERVICE_ACCOUNT_JSON = '{"type": "service_account", "project_id": "p"}'
            with (
                patch("core.integrations.google_calendar.service_account") as sa,
                patch("core.integrations.google_calendar.build", return_value=MagicMock()) as build,
            ):
                client = gc.GoogleCalendarClient.from_settings()
            assert client.enabled is True
            sa.Credentials.from_service_account_info.assert_called_once()
            assert build.call_args.kwargs["cache_discovery"] is False

        def it_accepts_base64_encoded_creds(settings):
            settings.GOOGLE_CALENDAR_SYNC_ENABLED = True
            settings.GOOGLE_SERVICE_ACCOUNT_JSON = base64.b64encode(b'{"type": "service_account"}').decode()
            with (
                patch("core.integrations.google_calendar.service_account"),
                patch("core.integrations.google_calendar.build", return_value=MagicMock()),
            ):
                assert gc.GoogleCalendarClient.from_settings().enabled is True

        def it_stays_disabled_when_credential_construction_raises(settings):
            settings.GOOGLE_CALENDAR_SYNC_ENABLED = True
            settings.GOOGLE_SERVICE_ACCOUNT_JSON = '{"type": "service_account"}'
            with patch("core.integrations.google_calendar.service_account") as sa:
                sa.Credentials.from_service_account_info.side_effect = ValueError("bad key")
                assert gc.GoogleCalendarClient.from_settings().enabled is False

    def describe_api_methods():
        def it_returns_the_resource_on_insert():
            service = MagicMock()
            service.events.return_value.insert.return_value.execute.return_value = {"id": "abc"}
            client = gc.GoogleCalendarClient(service=service)
            assert client.insert_event("cal", {"summary": "x"}) == {"id": "abc"}

        def it_returns_the_resource_on_update():
            service = MagicMock()
            service.events.return_value.update.return_value.execute.return_value = {"id": "abc"}
            client = gc.GoogleCalendarClient(service=service)
            assert client.update_event("cal", "abc", {"summary": "x"}) == {"id": "abc"}

        def it_deletes_without_returning():
            service = MagicMock()
            service.events.return_value.delete.return_value.execute.return_value = ""
            client = gc.GoogleCalendarClient(service=service)
            assert client.delete_event("cal", "abc") is None

        def it_wraps_httperror_as_googlecalendarerror():
            service = MagicMock()
            service.events.return_value.insert.return_value.execute.side_effect = _http_error()
            client = gc.GoogleCalendarClient(service=service)
            with pytest.raises(gc.GoogleCalendarError):
                client.insert_event("cal", {})

    def describe__parse_service_account_info():
        def it_rejects_json_that_is_not_an_object():
            # Valid JSON, but a list/number is not a service-account dict → None.
            assert gc._parse_service_account_info("[1, 2, 3]") is None
