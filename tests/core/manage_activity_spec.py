"""Access + rendering for the /manage/activity/ staff page."""

import itertools
import re
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone

from core.factories import TransactionalEmailLogFactory
from core.models import SiteActivity, TransactionalEmailLog

pytestmark = pytest.mark.django_db


def describe_manage_activity():
    def it_redirects_anonymous_users(client):
        resp = client.get(reverse("manage_activity"))
        assert resp.status_code in (302, 301)

    def it_forbids_non_staff(client):
        User.objects.create_user(username="m", email="m@example.com", password="pw12345!")
        client.login(username="m", password="pw12345!")
        resp = client.get(reverse("manage_activity"))
        assert resp.status_code in (302, 403)

    def it_renders_for_staff(client):
        staff = User.objects.create_user(
            username="a",
            email="a@example.com",
            password="pw12345!",
            is_staff=True,
        )
        client.login(username="a", password="pw12345!")
        SiteActivity.log(SiteActivity.Kind.LOGIN, actor=staff)
        resp = client.get(reverse("manage_activity"))
        assert resp.status_code == 200
        assert b"Site Activity" in resp.content

    def it_renders_the_late_fee_waived_and_refunded_kinds(client):
        # #456 part 3: the two new kinds render in the feed and its Event filter like any other.
        staff = User.objects.create_user(
            username="a_fees", email="a_fees@example.com", password="pw12345!", is_staff=True
        )
        client.login(username="a_fees", password="pw12345!")
        SiteActivity.log(SiteActivity.Kind.LATE_FEE_WAIVED, actor=staff)
        SiteActivity.log(SiteActivity.Kind.LATE_FEE_REFUNDED, actor=staff)
        content = client.get(reverse("manage_activity"), {"tab": "feed"}).content.decode()
        assert content.count("Late cancellation fee waived") >= 2  # the row and the filter option
        assert content.count("Late cancellation fee refunded") >= 2
        assert 'value="late_fee_waived"' in content
        assert 'value="late_fee_refunded"' in content

    def it_names_people_instead_of_showing_their_email(client):
        from classes.factories import RegistrationFactory

        staff = User.objects.create_user(
            username="a_names", email="a_names@example.com", password="pw12345!", is_staff=True, first_name="Rae"
        )
        client.login(username="a_names", password="pw12345!")
        SiteActivity.log(SiteActivity.Kind.LOGIN, actor=staff)
        RegistrationFactory(first_name="Guest", last_name="Person", email="guest@example.com")
        content = client.get(reverse("manage_activity"), {"tab": "feed"}).content.decode()
        assert "Rae ·" in content
        assert "Guest Person ·" in content
        assert "a_names@example.com ·" not in content
        assert "System ·" not in content

    def it_filters_the_feed_by_kind(client):
        staff = User.objects.create_user(
            username="a2",
            email="a2@example.com",
            password="pw12345!",
            is_staff=True,
        )
        client.login(username="a2", password="pw12345!")
        SiteActivity.log(SiteActivity.Kind.LOGIN, actor=staff)
        SiteActivity.log(SiteActivity.Kind.LOGOUT, actor=staff)
        resp = client.get(reverse("manage_activity"), {"kind": "login"})
        assert resp.status_code == 200

    def it_filters_the_feed_by_actor(client):
        staff = User.objects.create_user(
            username="a3",
            email="a3@example.com",
            password="pw12345!",
            is_staff=True,
        )
        client.login(username="a3", password="pw12345!")
        SiteActivity.log(SiteActivity.Kind.LOGIN, actor=staff)
        resp = client.get(reverse("manage_activity"), {"actor": "a3@example.com"})
        assert resp.status_code == 200

    def it_filters_the_emails_by_status(client):
        User.objects.create_user(
            username="a4",
            email="a4@example.com",
            password="pw12345!",
            is_staff=True,
        )
        client.login(username="a4", password="pw12345!")
        TransactionalEmailLog.objects.create(
            to_email="x@example.com",
            subject="s",
            trigger_kind="billing.receipt",
            status=TransactionalEmailLog.Status.SENT,
        )
        resp = client.get(reverse("manage_activity"), {"tab": "emails", "status": "sent"})
        assert resp.status_code == 200


def _pacific(*args: int) -> datetime:
    return datetime(*args, tzinfo=ZoneInfo("America/Los_Angeles"))


def _email_log(client, **params: str) -> str:
    return client.get(reverse("manage_activity"), {"tab": "emails", **params}).content.decode()


def _testid(content: str, testid: str) -> str:
    match = re.search(rf'data-testid="{testid}">([^<]*)<', content)
    assert match, f"no {testid} in the page"
    return match.group(1).strip()


def describe_email_log_filters():
    @pytest.fixture(autouse=True)
    def staff(client):
        user = User.objects.create_user(
            username="elog", email="elog-staff@example.com", password="pw12345!", is_staff=True
        )
        client.force_login(user)
        return user

    def it_finds_a_recipient_by_any_part_of_the_address_in_any_case(client):
        TransactionalEmailLogFactory(to_email="Zoe.Quillfeather@Example.com", subject="Quillfeather welcome")
        TransactionalEmailLogFactory(to_email="zoe@brambleworth.org", subject="Brambleworth receipt")
        content = _email_log(client, recipient="QUILLFEATHER@example")
        assert "Quillfeather welcome" in content
        assert "Brambleworth receipt" not in content
        assert 'value="QUILLFEATHER@example"' in content

    def it_filters_by_type_and_labels_the_types_in_plain_words(client):
        TransactionalEmailLogFactory(trigger_kind="member.login_invite", subject="Sign in subject zephyr")
        TransactionalEmailLogFactory(trigger_kind="billing.receipt", subject="Receipt subject zephyr")
        content = _email_log(client, type="member.login_invite")
        assert "Sign in subject zephyr" in content
        assert "Receipt subject zephyr" not in content
        # The registry's label for a key it knows; the raw key for one it does not.
        assert re.search(
            r'<option value="member.login_invite"\s+selected>Sign in to Past Lives for the first time</option>', content
        )
        assert re.search(r'<option value="billing.receipt"\s*>billing.receipt</option>', content)

    def it_offers_suppressed_as_a_status(client):
        TransactionalEmailLogFactory(status=TransactionalEmailLog.Status.SUPPRESSED, subject="Held back gorse")
        TransactionalEmailLogFactory(status=TransactionalEmailLog.Status.SENT, subject="Went out gorse")
        content = _email_log(client, status="suppressed")
        assert re.search(r'<option value="suppressed"\s+selected>Suppressed</option>', content)
        assert "Held back gorse" in content
        assert "Went out gorse" not in content

    def it_bounds_the_send_date_by_pacific_day_inclusive(client):
        # 23:30 Pacific on Nov 4 is already Nov 5 in UTC: a UTC bound would let it in.
        TransactionalEmailLogFactory(subject="Late on the fourth", sent_at=_pacific(2026, 11, 4, 23, 30))
        TransactionalEmailLogFactory(subject="Early on the fifth", sent_at=_pacific(2026, 11, 5, 0, 10))
        TransactionalEmailLogFactory(subject="Late on the sixth", sent_at=_pacific(2026, 11, 6, 23, 59))
        TransactionalEmailLogFactory(subject="Early on the seventh", sent_at=_pacific(2026, 11, 7, 0, 1))
        content = _email_log(client, date_from="2026-11-05", date_to="2026-11-06")
        assert "Early on the fifth" in content
        assert "Late on the sixth" in content
        assert "Late on the fourth" not in content
        assert "Early on the seventh" not in content
        assert 'value="2026-11-05"' in content
        assert 'value="2026-11-06"' in content

    def it_counts_the_filtered_set_by_outcome(client):
        for status in ("sent", "sent", "failed", "suppressed"):
            TransactionalEmailLogFactory(to_email="counted@example.com", status=status)
        TransactionalEmailLogFactory(to_email="elsewhere@example.com", status="sent")
        assert _testid(_email_log(client), "email-log-count") == "5 emails: 3 sent, 1 failed, 1 suppressed"
        content = _email_log(client, recipient="counted", status="sent")
        assert _testid(content, "email-log-count") == "2 emails: 2 sent"

    def it_keeps_every_filter_on_page_two(client):
        for n in range(55):
            TransactionalEmailLogFactory(to_email=f"pager{n}@example.com", status="failed", subject=f"Paged row {n}")
        for n in range(10):
            TransactionalEmailLogFactory(
                to_email=f"pager-other{n}@example.com", status="sent", subject=f"Other row {n}"
            )
        params = {"recipient": "pager", "status": "failed", "date_from": "2000-01-01", "date_to": "2100-12-31"}
        # show= is a one time guidance highlight; paging must not replay it.
        first = _email_log(client, **params, show="admin.site-activity")
        assert "show=" not in re.search(r'href="([^"]*)">Next</a>', first).group(1)
        next_href = re.search(r'href="([^"]*)">Next</a>', first).group(1).replace("&amp;", "&")
        for pair in (
            "tab=emails",
            "epage=2",
            "recipient=pager",
            "status=failed",
            "date_from=2000-01-01",
            "date_to=2100-12-31",
        ):
            assert pair in next_href
        second = client.get(reverse("manage_activity") + next_href).content.decode()
        assert second.count("Paged row") == 5
        assert "Other row" not in second
        assert "Page 2 of 2" in second
        previous_href = re.search(r'href="([^"]*)">Previous</a>', second).group(1).replace("&amp;", "&")
        assert "epage=1" in previous_href and "recipient=pager" in previous_href

    def it_keeps_the_feed_filters_on_the_feed_next_page(client, staff):
        for _ in range(51):
            SiteActivity.log(SiteActivity.Kind.LOGIN, actor=staff)
        content = client.get(reverse("manage_activity"), {"tab": "feed", "kind": "login"}).content.decode()
        next_href = re.search(r'href="([^"]*)">Next</a>', content).group(1).replace("&amp;", "&")
        assert "tab=feed" in next_href and "kind=login" in next_href and "page=2" in next_href

    def it_accepts_the_last_representable_day_as_to(client):
        # date.max has no next midnight; the bound is skipped rather than overflowing to a 500.
        TransactionalEmailLogFactory(subject="Far future kept")
        resp = client.get(reverse("manage_activity"), {"tab": "emails", "date_to": "9999-12-31"})
        assert resp.status_code == 200
        assert "Far future kept" in resp.content.decode()

    def it_finds_nothing_for_an_over_long_recipient_instead_of_the_whole_log(client):
        TransactionalEmailLogFactory(subject="Must not show")
        long_recipient = "z" * 300
        content = _email_log(client, recipient=long_recipient)
        assert "Must not show" not in content
        assert _testid(content, "email-log-empty") == f"No emails to {long_recipient} have been logged."
        assert 'data-testid="email-log-count"' not in content

    def it_finds_nothing_for_an_over_long_type(client):
        TransactionalEmailLogFactory(subject="Must not show either")
        content = _email_log(client, type="t" * 150)
        assert "Must not show either" not in content
        assert _testid(content, "email-log-empty") == "No emails match these filters."

    def it_names_the_address_when_a_recipient_search_finds_nothing(client):
        TransactionalEmailLogFactory(to_email="someone@example.com")
        content = _email_log(client, recipient="zoe@example.com")
        assert _testid(content, "email-log-empty") == "No emails to zoe@example.com have been logged."
        assert 'data-testid="email-log-count"' not in content

    def it_says_no_emails_logged_yet_on_an_empty_log(client):
        assert _testid(_email_log(client), "email-log-empty") == "No emails logged yet."


def _feed(client, **params: str):
    return client.get(reverse("manage_activity"), {"tab": "feed", **params})


def _feed_kinds(resp) -> set[str]:
    # Read from the page object, not the HTML: the Event select lists every kind's label.
    return {activity.kind for activity in resp.context["feed_page"]}


def _log_at(kind: str, when: datetime, actor: User | None = None) -> SiteActivity:
    activity = SiteActivity.log(kind, actor=actor)
    SiteActivity.objects.filter(pk=activity.pk).update(created_at=when)
    return activity


def _person(username: str, *, preferred_name: str = "", full_legal_name: str = "") -> User:
    from membership.models import Member

    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="pw12345!")
    # Creating the user links it a Member (membership.signals.ensure_user_has_member).
    Member.objects.filter(user=user).update(preferred_name=preferred_name, full_legal_name=full_legal_name)
    return user


def describe_activity_feed_filters():
    @pytest.fixture(autouse=True)
    def staff(client):
        user = User.objects.create_user(
            username="feedstaff", email="feed-staff@example.com", password="pw12345!", is_staff=True
        )
        client.force_login(user)
        return user

    def it_shows_only_voting_kinds_for_voting(client):
        for kind in SiteActivity.Kind.values:
            SiteActivity.log(kind)
        assert _feed_kinds(_feed(client, category="voting")) == {
            "vote_submitted",
            "vote_changed",
            "funding_snapshot_taken",
            "funding_snapshot_deleted",
        }

    def it_shows_only_class_and_teaching_kinds_for_classes_and_teaching(client):
        for kind in SiteActivity.Kind.values:
            SiteActivity.log(kind)
        assert _feed_kinds(_feed(client, category="classes")) == {
            "class_registered",
            "class_registration_cancelled",
            "class_waitlist_joined",
            "class_published",
            "class_submitted",
            "class_approved",
            "class_cancelled",
            "instructor_oriented",
            "teaching_applied",
            "teaching_application_declined",
            "teaching_granted",
            "teaching_revoked",
            "teaching_inquiry_added",
        }

    def it_groups_the_event_options_by_category(client):
        content = _feed(client).content.decode()
        assert content.count("<optgroup ") == 9
        assert re.search(r'<optgroup label="Voting">\s*<option value="vote_submitted"', content)
        assert re.search(r'<option value="voting"\s*>Voting</option>', content)

    def it_narrows_the_event_options_to_the_chosen_category(client):
        content = _feed(client, category="voting", kind="login").content.decode()
        assert content.count("<optgroup ") == 1
        assert 'value="vote_changed"' in content
        assert 'value="login"' not in content
        assert re.search(r'<option value="voting"\s+selected>Voting</option>', content)

    def it_marks_the_chosen_event(client):
        content = _feed(client, kind="vote_changed").content.decode()
        assert re.search(r'<option value="vote_changed"\s+selected>Changed vote</option>', content)

    def it_finds_rows_by_the_actors_preferred_name(client):
        quill = _person("quill", preferred_name="Zephyrine", full_legal_name="Ann Quillfeather")
        other = _person("other", preferred_name="Bramble", full_legal_name="Bo Other")
        SiteActivity.log(SiteActivity.Kind.LOGIN, actor=quill)
        SiteActivity.log(SiteActivity.Kind.LOGOUT, actor=other)
        resp = _feed(client, person="zephyr")
        assert _feed_kinds(resp) == {"login"}
        assert 'value="zephyr"' in resp.content.decode()

    def it_finds_rows_by_the_actors_legal_name(client):
        quill = _person("quill2", preferred_name="Zephyrine", full_legal_name="Ann Quillfeather")
        SiteActivity.log(SiteActivity.Kind.LOGIN, actor=quill)
        SiteActivity.log(SiteActivity.Kind.LOGOUT)
        assert _feed_kinds(_feed(client, person="QUILLFEATHER")) == {"login"}

    def it_still_answers_an_old_actor_link(client):
        quill = _person("quill3")
        SiteActivity.log(SiteActivity.Kind.LOGIN, actor=quill)
        SiteActivity.log(SiteActivity.Kind.LOGOUT)
        resp = _feed(client, actor="quill3@example.com")
        assert _feed_kinds(resp) == {"login"}
        assert 'name="person" value="quill3@example.com"' in resp.content.decode()

    def it_bounds_the_feed_by_pacific_day_inclusive(client):
        # 23:30 Pacific on Nov 4 is already Nov 5 in UTC: a UTC bound would let it in.
        _log_at(SiteActivity.Kind.LOGIN, _pacific(2026, 11, 4, 23, 30))
        _log_at(SiteActivity.Kind.VOTE_SUBMITTED, _pacific(2026, 11, 5, 0, 10))
        _log_at(SiteActivity.Kind.VOTE_CHANGED, _pacific(2026, 11, 6, 23, 59))
        _log_at(SiteActivity.Kind.LOGOUT, _pacific(2026, 11, 7, 0, 1))
        resp = _feed(client, feed_from="2026-11-05", feed_to="2026-11-06")
        assert _feed_kinds(resp) == {"vote_submitted", "vote_changed"}
        content = resp.content.decode()
        assert 'name="feed_from" value="2026-11-05"' in content
        assert 'name="feed_to" value="2026-11-06"' in content

    def it_counts_the_filtered_feed_above_the_list(client):
        for _ in range(3):
            SiteActivity.log(SiteActivity.Kind.VOTE_SUBMITTED)
        SiteActivity.log(SiteActivity.Kind.LOGIN)
        assert _testid(_feed(client).content.decode(), "activity-feed-count") == "4 events"
        assert _testid(_feed(client, category="voting").content.decode(), "activity-feed-count") == "3 events"

    def it_says_so_when_the_filters_match_nothing(client):
        SiteActivity.log(SiteActivity.Kind.LOGIN)
        content = _feed(client, category="wiki").content.decode()
        assert _testid(content, "activity-feed-empty") == "No activity matches these filters."
        assert 'data-testid="activity-feed-count"' not in content

    def it_rings_the_category_field_for_guidance(client):
        content = _feed(client).content.decode()
        assert re.search(
            r'data-guidance-target="activity.feed-filter">\s*<label for="activity-feed-category">', content
        )

    def it_keeps_every_filter_on_page_two(client):
        voter = _person("pager", preferred_name="Pager Person")
        for n in range(55):
            _log_at(SiteActivity.Kind.VOTE_SUBMITTED, _pacific(2026, 11, 5, 12, n % 60), actor=voter)
        for _ in range(10):
            _log_at(SiteActivity.Kind.VOTE_SUBMITTED, _pacific(2026, 11, 5, 12, 0))
            _log_at(SiteActivity.Kind.LOGIN, _pacific(2026, 11, 5, 12, 0), actor=voter)
        _log_at(SiteActivity.Kind.VOTE_SUBMITTED, _pacific(2026, 11, 8, 12, 0), actor=voter)
        params = {"category": "voting", "person": "pager person", "feed_from": "2026-11-05", "feed_to": "2026-11-06"}
        first = _feed(client, **params, show="admin.site-activity").content.decode()
        assert _testid(first, "activity-feed-count") == "55 events"
        next_href = re.search(r'href="([^"]*)">Next</a>', first).group(1).replace("&amp;", "&")
        assert "show=" not in next_href
        for pair in (
            "tab=feed",
            "page=2",
            "category=voting",
            "person=pager+person",
            "feed_from=2026-11-05",
            "feed_to=2026-11-06",
        ):
            assert pair in next_href
        second = client.get(reverse("manage_activity") + next_href)
        assert len(second.context["feed_page"]) == 5
        assert {activity.actor_id for activity in second.context["feed_page"]} == {voter.pk}
        assert _feed_kinds(second) == {"vote_submitted"}
        assert "Page 2 of 2" in second.content.decode()


def describe_feed_and_email_log_dates_stay_apart():
    # Every tab renders in one page from one request.GET, so the two forms' dates must not share names.
    @pytest.fixture(autouse=True)
    def staff(client):
        user = User.objects.create_user(
            username="apart", email="apart-staff@example.com", password="pw12345!", is_staff=True
        )
        client.force_login(user)
        return user

    @pytest.fixture
    def one_of_each():
        _log_at(SiteActivity.Kind.LOGIN, _pacific(2026, 11, 5, 12, 0))
        TransactionalEmailLogFactory(subject="Recent email kestrel", sent_at=_pacific(2026, 11, 5, 12, 0))

    def it_leaves_the_feed_alone_when_the_email_log_has_dates(client, one_of_each):
        resp = client.get(
            reverse("manage_activity"), {"tab": "emails", "date_from": "2020-01-01", "date_to": "2020-01-02"}
        )
        assert _feed_kinds(resp) == {"login"}
        assert len(resp.context["email_page"]) == 0
        content = resp.content.decode()
        assert 'name="feed_from" value=""' in content
        assert 'name="feed_to" value=""' in content
        assert 'name="date_from" value="2020-01-01"' in content

    def it_leaves_the_email_log_alone_when_the_feed_has_dates(client, one_of_each):
        resp = _feed(client, feed_from="2020-01-01", feed_to="2020-01-02")
        assert _feed_kinds(resp) == set()
        assert [email.subject for email in resp.context["email_page"]] == ["Recent email kestrel"]
        content = resp.content.decode()
        assert 'name="date_from" value=""' in content
        assert 'name="date_to" value=""' in content
        assert 'name="feed_from" value="2020-01-01"' in content


def describe_admin_tools_activity_link():
    def _member(fog_role: str) -> User:
        from membership.models import Member, MembershipPlan

        plan, _ = MembershipPlan.objects.get_or_create(name="Standard", defaults={"monthly_price": "50.00"})
        user = User.objects.create_user(
            username=f"hub-{fog_role}@example.com", email=f"hub-{fog_role}@example.com", password="pw12345!"
        )
        Member.objects.update_or_create(
            user=user,
            defaults={
                "full_legal_name": f"Hub {fog_role}",
                "fog_role": fog_role,
                "membership_plan": plan,
                "status": Member.Status.ACTIVE,
            },
        )
        return user

    def it_appears_on_the_admin_tools_page_for_admins(client):
        from membership.models import Member

        client.force_login(_member(Member.FogRole.ADMIN))
        resp = client.get(reverse("hub_admin_tools"))
        assert resp.status_code == 200
        assert reverse("manage_activity").encode() in resp.content

    def it_is_hidden_from_non_admins(client):
        from membership.models import Member

        client.force_login(_member(Member.FogRole.MEMBER))
        resp = client.get(reverse("hub_admin_tools"))
        # Plain members cannot use Admin Tools, so they are redirected home.
        assert resp.status_code == 302
        assert reverse("manage_activity").encode() not in resp.content


def describe_new_members_tab():
    """#1115: the New Members tab, end to end through the page. Airtable is mocked; it is never reached."""

    _ids = itertools.count(1)

    def _admin(client):
        User.objects.create_superuser(username="nm_admin", email="nm_admin@example.com", password="pw12345!")
        client.login(username="nm_admin", password="pw12345!")

    def _new_member(*, days_ago=3, **kwargs):
        from membership.models import Member
        from tests.membership.factories import MemberFactory

        kwargs.setdefault("airtable_record_id", f"recVIEW{next(_ids):05d}")
        member = MemberFactory(**kwargs)
        Member.objects.filter(pk=member.pk).update(created_at=timezone.now() - timedelta(days=days_ago))
        return member

    def _row(content, member):
        """The markup of ``member``'s row, up to the next row."""
        for chunk in content.split('data-testid="new-member-row"')[1:]:
            if chunk.startswith(f' data-member="{member.pk}"'):
                return chunk
        raise AssertionError(f"no row for member {member.pk}")

    def _tab(client, **params):
        return client.get(reverse("manage_activity"), {"tab": "newmembers", **params}).content.decode()

    @pytest.fixture
    def airtable(settings):
        settings.AIRTABLE_API_TOKEN = "test-token"
        settings.AIRTABLE_BASE_ID = "appTEST"
        table = MagicMock()
        table.all.return_value = []
        with patch("airtable_sync.client.get_table", return_value=table) as get_table:
            table.get_table = get_table
            yield table

    def it_redirects_non_staff_away_from_the_tab(client):
        User.objects.create_user(username="nm_plain", email="nm_plain@example.com", password="pw12345!")
        client.login(username="nm_plain", password="pw12345!")
        resp = client.get(reverse("manage_activity"), {"tab": "newmembers"})
        assert resp.status_code in (302, 403)
        assert b"new-member-row" not in resp.content

    def it_shows_added_sent_and_never_with_resend_for_a_member_from_3_days_ago(client):
        _admin(client)
        member = _new_member(_pre_signup_email="threedays@example.com", welcome_email_sent_at=timezone.now())
        content = _tab(client)
        row = _row(content, member)
        added = timezone.localtime(timezone.now() - timedelta(days=3)).strftime("%b %-d, %Y")
        assert f'data-fact="added">{added}<' in row
        assert 'data-state="sent">Sent ' in row
        assert 'data-fact="signed-in">Never<' in row
        assert reverse("hub_admin_member_send_login_invite", args=[member.pk]) in row
        assert f"{reverse('manage_activity')}?tab=emails&amp;recipient=threedays%40example.com" in row
        assert ">Resend Sign In Link<" in row

    def it_says_send_not_resend_to_someone_never_sent_one(client):
        from membership.models import Member

        _admin(client)
        waiting = _new_member()
        work_trade = _new_member(member_type=Member.MemberType.WORK_TRADE)
        content = _tab(client)
        for member in (waiting, work_trade):
            row = _row(content, member)
            assert ">Send Sign In Link<" in row
            assert ">Resend Sign In Link<" not in row

    def it_says_why_a_work_trade_or_former_member_got_no_welcome(client):
        from membership.models import Member

        _admin(client)
        work_trade = _new_member(member_type=Member.MemberType.WORK_TRADE)
        former = _new_member(status=Member.Status.FORMER)
        content = _tab(client)
        assert ">Not sent: not a paying member<" in _row(content, work_trade)
        assert ">Not sent: not Active (Former)<" in _row(content, former)

    def it_shows_a_failed_welcome_with_its_reason(client):
        _admin(client)
        member = _new_member(_pre_signup_email="bounced@example.com")
        TransactionalEmailLogFactory(
            to_email="bounced@example.com",
            trigger_kind="member.login_invite",
            status=TransactionalEmailLog.Status.FAILED,
            error_message="Mailbox unavailable",
        )
        assert 'data-state="failed">Failed: Mailbox unavailable<' in _row(_tab(client), member)

    def it_says_when_a_qualifying_member_goes_out(client):
        from core.models import ScheduledJobState

        _admin(client)
        ScheduledJobState.objects.create(task_key="welcome_new_members", enabled=True)
        member = _new_member()
        assert 'data-state="scheduled">Goes out at the next morning run, ' in _row(_tab(client), member)

    def it_narrows_to_never_signed_in_and_widens_to_60_days(client):
        _admin(client)
        recent = _new_member(_pre_signup_email="recent@example.com")
        older = _new_member(days_ago=45)
        signed_in = _new_member(_pre_signup_email="signedin@example.com")
        user = User.objects.create_user(username="nm_signed", email="signedin@example.com")
        User.objects.filter(pk=user.pk).update(last_login=timezone.now())

        def shown(content):
            return {int(pk) for pk in re.findall(r'data-member="(\d+)"', content)}

        assert shown(_tab(client)) == {recent.pk, signed_in.pk}
        assert shown(_tab(client, nm_never="on")) == {recent.pk}
        assert shown(_tab(client, nm_days="60")) == {recent.pk, older.pk, signed_in.pk}
        content = _tab(client, nm_days="60", nm_never="on")
        assert shown(content) == {recent.pk, older.pk}
        assert "2 new members who never signed in in the last 60 days" in content
        assert reverse("hub_admin_member_send_login_invite", args=[signed_in.pk]) not in _tab(client)

    def it_says_so_when_the_window_is_empty(client):
        _admin(client)
        assert 'data-testid="new-members-empty">Nobody was added from Airtable in the last 30 days.<' in _tab(client)

    def it_hides_resend_from_staff_who_are_not_admins(client):
        User.objects.create_user(username="nm_staff", email="nm_staff@example.com", password="pw12345!", is_staff=True)
        client.login(username="nm_staff", password="pw12345!")
        member = _new_member()
        content = _tab(client)
        assert 'data-testid="new-member-row"' in content
        assert reverse("hub_admin_member_send_login_invite", args=[member.pk]) not in content

    def it_offers_no_email_link_or_resend_without_an_address(client):
        _admin(client)
        member = _new_member(_pre_signup_email="")
        row = _row(_tab(client), member)
        assert 'data-testid="new-member-emails"' not in row
        assert 'data-testid="new-member-resend"' not in row

    def it_sends_resend_back_to_this_tab_without_the_search(client):
        _admin(client)
        member = _new_member()
        row = _row(_tab(client, nm_days="60", nm_q="someone", show="activity.new-members"), member)
        next_value = re.search(r'name="next" value="([^"]+)"', row).group(1)
        assert next_value.startswith(reverse("manage_activity") + "?")
        assert "tab=newmembers" in next_value
        assert "nm_days=60" in next_value
        assert "nm_q" not in next_value
        assert "show=" not in next_value

    def it_never_calls_airtable_on_a_tab_load(client, airtable):
        _admin(client)
        content = _tab(client, nm_days="60")
        airtable.get_table.assert_not_called()
        assert 'data-testid="new-members-lookup-result"' not in content

    def it_says_in_neither_for_an_unknown_email(client, airtable):
        _admin(client)
        content = _tab(client, nm_q="nobody.anywhere@example.com")
        assert "Not in Airtable or the portal. Add them in Airtable first." in content

    def it_says_not_imported_yet_for_a_name_only_airtable_has(client, airtable):
        _admin(client)
        airtable.all.return_value = [
            {
                "id": "recONLYAT",
                "fields": {
                    "Member Name": "Sorrel Penhallow",
                    "Email": "sorrel@example.com",
                    "Status": "Active",
                    "Role": "Standard Member",
                },
            }
        ]
        content = _tab(client, nm_q="Sorrel")
        assert "Sorrel Penhallow is in Airtable but not imported yet. The next import runs " in content

    def it_says_airtable_could_not_be_reached_and_still_shows_the_portal(client, airtable):
        from tests.membership.factories import MemberFactory

        _admin(client)
        member = MemberFactory(full_legal_name="Marisol Quench")
        airtable.all.side_effect = TimeoutError("read timed out")
        content = _tab(client, nm_q="Marisol")
        assert "Couldn&#x27;t search Airtable. Only the portal was searched." in content
        assert "Marisol Quench is in the portal." in content
        assert reverse("hub_admin_member_edit", args=[member.pk]) in content

    def it_keeps_each_tabs_filters_to_itself(client):
        _admin(client)
        member = _new_member(_pre_signup_email="ownparams@example.com")
        resp = client.get(reverse("manage_activity"), {"tab": "newmembers", "recipient": "zzz", "person": "zzz"})
        assert f'data-member="{member.pk}"' in resp.content.decode()
        email_filters = client.get(reverse("manage_activity"), {"nm_q": "zzz", "nm_days": "90"}).context[
            "email_filters"
        ]
        assert email_filters.recipient_value == ""


def describe_orientation_and_space_rows():
    """Each orientation or space row says whose it is, who acted, and any note (Felix: "The system can't be requested an orientation?")."""

    def _staff(client) -> User:
        staff = User.objects.create_user(
            username="feed_staff", email="feed_staff@example.com", password="pw12345!", is_staff=True
        )
        staff.member.preferred_name = "Alex Smith"
        staff.member.save(update_fields=["preferred_name"])
        client.login(username="feed_staff", password="pw12345!")
        return staff

    def _robin():
        from tests.membership.factories import MembershipPlanFactory

        MembershipPlanFactory()
        member = User.objects.create_user(username="robin", email="robin@example.com").member
        member.preferred_name = "Robin Vale"
        member.save(update_fields=["preferred_name"])
        return member

    def _booking(member, **slot_kwargs):
        from tests.membership.factories import (
            GuildFactory,
            OrientationBookingFactory,
            OrientationSlotFactory,
            OrientationTypeFactory,
        )

        guild = GuildFactory(name="Laser Guild")
        orientation = OrientationTypeFactory(guild=guild, name="Laser Basics")
        return OrientationBookingFactory(
            slot=OrientationSlotFactory(guild=guild, orientation_type=orientation, **slot_kwargs), member=member
        )

    def _feed(client) -> str:
        return client.get(reverse("manage_activity"), {"tab": "feed"}).content.decode()

    def it_names_the_member_and_orientation_on_a_request_the_member_made_with_their_note(client):
        from membership import orientations
        from tests.membership.factories import GuildFactory, OrientationSlotFactory, OrientationTypeFactory

        _staff(client)
        guild = GuildFactory(name="Laser Guild")
        slot = OrientationSlotFactory(
            guild=guild, orientation_type=OrientationTypeFactory(guild=guild, name="Laser Basics")
        )
        orientations.request_orientation(slot, _robin(), note="I have used a <b>laser</b> before")

        content = _feed(client)
        assert "Orientation requested: Robin Vale for Laser Basics (Laser Guild)" in content
        assert "Robin Vale · " in content
        assert "“I have used a &lt;b&gt;laser&lt;/b&gt; before”" in content
        assert "System · " not in content

    def it_names_the_staffer_on_a_decline_and_shows_their_note(client):
        from membership import orientations

        staff = _staff(client)
        orientations.decline_orientation(_booking(_robin()), note="Try the March date", actor=staff)

        content = _feed(client)
        assert "Orientation declined: Robin Vale for Laser Basics (Laser Guild)" in content
        assert "Alex Smith · " in content
        assert "“Try the March date”" in content

    def it_says_system_on_an_automatic_completion_and_still_names_the_member(client):
        from membership import orientations

        _staff(client)
        booking = _booking(
            _robin(), starts_at=timezone.now() - timedelta(hours=3), ends_at=timezone.now() - timedelta(hours=2)
        )
        booking.confirm()
        assert orientations.auto_complete() == 1

        content = _feed(client)
        assert "Orientation completed automatically: Robin Vale for Laser Basics (Laser Guild)" in content
        assert "System · " in content

    def it_says_system_and_why_on_a_decline_the_card_hold_sweep_made(client):
        from membership import orientations

        _staff(client)
        assert orientations._decline_unconfirmed(_booking(_robin()), reason=orientations.DECLINE_HOLD_EXPIRED)

        content = _feed(client)
        assert "Orientation declined automatically: Robin Vale for Laser Basics (Laser Guild)" in content
        assert "“Nobody confirmed the request before the hold on the card ran out.”" in content
        assert "System · " in content

    def it_names_the_member_on_an_old_row_with_no_actor_and_says_who_acted_was_not_recorded(client):
        _staff(client)
        SiteActivity.log(SiteActivity.Kind.ORIENTATION_CANCELLED, target=_booking(_robin()))

        content = _feed(client)
        assert "Orientation cancelled: Robin Vale for Laser Basics (Laser Guild)" in content
        assert "Not recorded · " in content
        assert "System · " not in content

    def it_draws_no_note_line_when_there_is_no_note(client):
        staff = _staff(client)
        SiteActivity.log(SiteActivity.Kind.ORIENTATION_CANCELLED, actor=staff, target=_booking(_robin()))

        content = _feed(client)
        assert "Orientation cancelled: Robin Vale" in content
        assert 'data-testid="activity-note"' not in content

    def it_leaves_more_to_the_browser_for_every_note_whatever_its_length(client):
        # The clamp is CSS the server cannot measure: More renders hidden on every note and the page
        # shows it when the note overflows (tests/e2e/activity_feed_note_spec.py drives that).
        staff = _staff(client)
        booking = _booking(_robin())
        SiteActivity.log(SiteActivity.Kind.ORIENTATION_DECLINED, actor=staff, target=booking, payload={"note": "short"})
        SiteActivity.log(
            SiteActivity.Kind.ORIENTATION_DECLINED, actor=staff, target=booking, payload={"note": "x" * 141}
        )

        content = _feed(client)
        assert content.count("'pl-activity-note__more--shown': overflows || full") == 2
        assert content.count("data-activity-note-more") == 2

    def it_hides_the_stray_request_rows_the_cancel_bell_used_to_write(client):
        _staff(client)
        SiteActivity.log(SiteActivity.Kind.ORIENTATION_REQUESTED)

        page = client.get(reverse("manage_activity"), {"tab": "feed"}).context["feed_page"]
        assert SiteActivity.Kind.ORIENTATION_REQUESTED not in [row.kind for row in page]
        weeks = SiteActivity.weekly_counts(timezone.now() - timedelta(days=7))
        assert "Orientation requested" not in [row.label for row in weeks.rows]

    def it_names_the_requester_and_their_message_on_a_space_request(client):
        from tests.membership.factories import SpaceFactory

        _staff(client)
        request = _space_request(_robin(), SpaceFactory(space_id="S-12", name="Hot Glass Room"), message="For my kiln")

        content = _feed(client)
        assert "Space requested: Robin Vale for S-12 (Hot Glass Room)" in content
        assert "Robin Vale · " in content
        assert "“For my kiln”" in content
        assert content.count("Space requested: Robin Vale") == 1
        assert SiteActivity.objects.get(kind=SiteActivity.Kind.SPACE_REQUEST).actor == request.requester.user

    def it_names_the_requester_and_the_reason_on_a_declined_shelf_request(client):
        from membership.models import SpaceRequest
        from tests.membership.factories import SpaceFactory

        staff = _staff(client)
        request = _space_request(_robin(), SpaceFactory(space_id="C-3"), kind=SpaceRequest.RequestKind.CUBBY)
        request.decline(reviewer=staff, notes="Shelves are full")

        content = _feed(client)
        assert "Shelf requested: Robin Vale for C-3" in content
        assert "Shelf request declined: Robin Vale for C-3" in content
        assert "“Shelves are full”" in content

    def it_falls_back_to_the_kind_label_when_the_target_was_deleted(client):
        staff = _staff(client)
        booking = _booking(_robin())
        row = SiteActivity.log(SiteActivity.Kind.ORIENTATION_CANCELLED, actor=staff, target=booking)
        booking.delete()

        content = _feed(client)
        assert ">Orientation cancelled</div>" in content
        assert "Robin Vale for" not in content
        assert SiteActivity.objects.get(pk=row.pk).line.headline == "Orientation cancelled"

    def it_reads_a_page_of_mixed_targets_in_the_same_number_of_queries_however_many_rows(client):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        from tests.membership.factories import MemberFactory, SpaceFactory

        staff = _staff(client)

        def add_rows() -> None:
            booking = _booking(MemberFactory())
            booking.write_message(author=None, body="first")
            SiteActivity.log(SiteActivity.Kind.ORIENTATION_REQUESTED, target=booking)
            SiteActivity.log(SiteActivity.Kind.ORIENTATION_DECLINED, actor=staff, target=booking, payload={"note": "n"})
            _space_request(MemberFactory(), SpaceFactory(), message="m")

        def feed_queries() -> int:
            with CaptureQueriesContext(connection) as queries:
                assert client.get(reverse("manage_activity"), {"tab": "feed"}).status_code == 200
            return len(queries)

        add_rows()
        feed_queries()  # the staffer's first page load opens their tab once; measure after it
        one_of_each = feed_queries()
        add_rows()
        add_rows()
        assert feed_queries() == one_of_each

    def it_shows_the_note_any_row_carries_in_its_payload(client):
        staff = _staff(client)
        SiteActivity.log(SiteActivity.Kind.TAB_ENTRY_ADDED, actor=staff, payload={"note": "Glaze bucket"})

        content = _feed(client)
        assert "Tab entry added" in content
        assert "“Glaze bucket”" in content


def _space_request(member, space, **kwargs):
    from membership.models import SpaceRequest

    request = SpaceRequest(space=space, **kwargs)
    request.submit(requester=member)
    return request


def describe_orientation_request_note():
    """A request row's note is the member's own first message, written no later than the row."""

    def _requested(booking) -> SiteActivity:
        return SiteActivity.log(SiteActivity.Kind.ORIENTATION_REQUESTED, actor=booking.member.user, target=booking)

    def _member():
        from tests.membership.factories import MembershipPlanFactory

        MembershipPlanFactory()
        return User.objects.create_user(username="noter", email="noter@example.com").member

    def it_reads_no_note_on_a_thread_with_no_messages():
        from tests.membership.factories import OrientationBookingFactory

        assert _requested(OrientationBookingFactory(member=_member())).line.note == ""

    def it_ignores_a_first_message_from_staff():
        from tests.membership.factories import OrientationBookingFactory

        booking = OrientationBookingFactory(member=_member())
        booking.write_message(author=None, body="Bring boots")
        assert _requested(booking).line.note == ""

    def it_ignores_a_member_message_written_after_the_request():
        from tests.membership.factories import OrientationBookingFactory

        booking = OrientationBookingFactory(member=_member())
        row = _requested(booking)
        message = booking.write_message(author=booking.member.user, body="Running late")
        SiteActivity.objects.filter(pk=row.pk).update(created_at=message.created_at - timedelta(seconds=1))
        row.refresh_from_db()
        assert SiteActivity.objects.get(pk=row.pk).line.note == ""
