"""Access + rendering for the /manage/activity/ staff page."""

import re
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth.models import User
from django.urls import reverse

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
