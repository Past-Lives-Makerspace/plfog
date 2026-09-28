"""BDD specs for core.email_policy: the staging marking and delivery allowlist.

Pure decisions and string transforms; the only database access is the address-to-user
lookup behind the role check, exercised here against real users and members.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.contrib.auth.models import User

from core import email_policy
from membership.models import Member

pytestmark = pytest.mark.django_db

_HOST = "staging.pastlives.space"
_NOTICE = "STAGING. This came from staging.pastlives.space, not the real Past Lives portal. Nothing here is real."


@pytest.fixture
def staging(settings):
    settings.IS_STAGING = True
    settings.MEMBER_HOST = _HOST
    settings.EMAIL_DELIVERY_ALLOWLIST = frozenset()
    return settings


@pytest.fixture
def production(settings):
    settings.IS_STAGING = False
    settings.EMAIL_DELIVERY_ALLOWLIST = frozenset()
    return settings


def describe_staging_notice():
    def it_names_the_member_host(staging):
        assert email_policy.staging_notice() == _NOTICE


def describe_staging_subject():
    def it_prefixes_on_staging(staging):
        assert email_policy.staging_subject("Your login code") == "[STAGING] Your login code"

    def it_never_stacks_the_prefix(staging):
        assert email_policy.staging_subject("[STAGING] Your login code") == "[STAGING] Your login code"

    def it_leaves_the_subject_alone_off_staging(production):
        assert email_policy.staging_subject("Your login code") == "Your login code"


def describe_staging_text_body():
    def it_puts_the_notice_on_the_first_line(staging):
        assert email_policy.staging_text_body("Hi there") == f"{_NOTICE}\n\nHi there"

    def it_leaves_the_body_alone_off_staging(production):
        assert email_policy.staging_text_body("Hi there") == "Hi there"


def describe_staging_html_body():
    def it_inserts_the_banner_right_after_the_body_tag(staging):
        html = '<html><BODY class="x" style="margin:0"><p>Hi</p></body></html>'
        out = email_policy.staging_html_body(html)
        assert out is not None
        head, _, tail = out.partition('<div style="')
        assert head == '<html><BODY class="x" style="margin:0">'
        assert _NOTICE in tail
        assert tail.endswith("</div><p>Hi</p></body></html>")

    def it_prepends_the_banner_when_there_is_no_body_tag(staging):
        out = email_policy.staging_html_body("<p>Hi</p>")
        assert out is not None
        assert out.startswith("<div style=")
        assert out.endswith("</div><p>Hi</p>")
        assert _NOTICE in out

    def it_keeps_none_as_none(staging):
        assert email_policy.staging_html_body(None) is None

    def it_leaves_the_html_alone_off_staging(production):
        assert email_policy.staging_html_body("<body><p>Hi</p></body>") == "<body><p>Hi</p></body>"


def describe_is_deliverable():
    def it_delivers_to_anyone_off_staging(production):
        assert email_policy.is_deliverable("stranger@example.com") is True

    def describe_on_staging():
        def it_refuses_an_address_nobody_owns(staging):
            assert email_policy.is_deliverable("stranger@example.com") is False

        def it_allows_an_exact_allowlist_address_case_insensitively(staging):
            staging.EMAIL_DELIVERY_ALLOWLIST = frozenset({"tester@example.com"})
            assert email_policy.is_deliverable("Tester@Example.com") is True

        def it_allows_a_whole_allowlisted_domain(staging):
            staging.EMAIL_DELIVERY_ALLOWLIST = frozenset({"plaza.codes"})
            assert email_policy.is_deliverable("anyone@plaza.codes") is True
            assert email_policy.is_deliverable("anyone@plaza.codes.evil.example") is False

        def it_judges_only_the_address_part_of_a_named_recipient(staging):
            staging.EMAIL_DELIVERY_ALLOWLIST = frozenset({"tester@example.com"})
            assert email_policy.is_deliverable("Lee Tester <tester@example.com>") is True

        def it_allows_a_staff_user(staging):
            User.objects.create_user("staffer", "staffer@example.com", is_staff=True)
            assert email_policy.is_deliverable("staffer@example.com") is True

        def it_allows_a_fog_admin_who_is_not_staff(staging):
            user = User.objects.create_user("adminish", "adminish@example.com")
            Member.objects.filter(user=user).update(fog_role=Member.FogRole.ADMIN)
            User.objects.filter(pk=user.pk).update(is_staff=False)
            assert email_policy.is_deliverable("adminish@example.com") is True

        def it_allows_an_instructor(staging):
            user = User.objects.create_user("teacher", "teacher@example.com")
            Member.objects.filter(user=user).update(instructor_slug="teacher")
            assert email_policy.is_deliverable("teacher@example.com") is True

        def it_refuses_a_plain_member(staging):
            user = User.objects.create_user("plain", "plain@example.com")
            assert Member.objects.get(user=user).fog_role == Member.FogRole.MEMBER
            assert email_policy.is_deliverable("plain@example.com") is False

        def it_refuses_a_user_with_no_member_row(staging):
            # Creating a User auto-provisions a Member (membership.signals); drop it to
            # reach the branch a member-less user takes.
            user = User.objects.create_user("loose", "loose@example.com")
            Member.objects.filter(user=user).delete()
            assert email_policy.is_deliverable("loose@example.com") is False


def describe_partition_recipients():
    def it_splits_in_order(staging):
        staging.EMAIL_DELIVERY_ALLOWLIST = frozenset({"a@example.com", "c@example.com"})
        delivered, suppressed = email_policy.partition_recipients(["a@example.com", "b@example.com", "c@example.com"])
        assert delivered == ["a@example.com", "c@example.com"]
        assert suppressed == ["b@example.com"]

    def it_suppresses_nothing_off_staging(production):
        delivered, suppressed = email_policy.partition_recipients(["a@example.com", "b@example.com"])
        assert delivered == ["a@example.com", "b@example.com"]
        assert suppressed == []

    def it_uses_the_same_rule_as_is_deliverable(staging):
        guild_like = SimpleNamespace()  # not an address; exercises the parse of an odd string
        delivered, suppressed = email_policy.partition_recipients([str(guild_like)])
        assert delivered == []
        assert suppressed == [str(guild_like)]
