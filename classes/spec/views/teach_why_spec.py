"""BDD specs for the Teach at Past Lives page and the apply-to-teach POST (spec §A).

Teaching stopped being self-service: a member applies, an admin approves. These cover
the page in all four application states, the apply POST's guards, and the branch that
sends a member who CAN teach to their dashboard instead of the marketing page.
"""

from __future__ import annotations

from django.urls import reverse
from django.utils import timezone

from classes.factories import ClassOfferingFactory, UserFactory
from classes.models import ClassOffering, ClassSettings
from core.models import SiteActivity
from membership.models import Member, OrgInfoPage, WikiArticle

HERO_TITLE = "Teach at Past Lives"
APPLY_BUTTON = "Apply to Teach"
APPLY_AGAIN_BUTTON = "Apply Again"
PENDING_BANNER = "Your Application Is In"
DECLINED_BANNER = "Not This Time"
APPROVED_BANNER = "You Can Teach"
# Template-literal copy is NOT autoescaped (only variables are), so these match verbatim.
PLACEHOLDER = "The guide has not been loaded yet."
BLANK_NOTE_ERROR = "Tell us a little about what you want to teach."


def _active_member_user(username: str) -> tuple[object, Member]:
    user = UserFactory(username=username)
    member = Member.objects.get(user=user)
    member.status = Member.Status.ACTIVE
    member.save(update_fields=["status"])
    return user, member


def _seed_orientation_article() -> WikiArticle:
    return WikiArticle.objects.create(
        page=OrgInfoPage.load(),
        slug="instructor-orientation",
        title="Instructor orientation",
        body="## What we expect from instructors {#what-we-expect}\n\nShow up prepared.",
    )


def _published_example() -> ClassOffering:
    offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)
    settings_obj = ClassSettings.load()
    settings_obj.example_class = offering
    settings_obj.save()
    return offering


def describe_teach_why():
    def it_renders_the_hero_and_the_apply_button_for_a_member_who_never_applied(db, client):
        _seed_orientation_article()
        user, _ = _active_member_user("never-applied@example.com")
        client.force_login(user)
        response = client.get(reverse("classes:teach_why"))
        assert response.status_code == 200
        content = response.content.decode()
        assert HERO_TITLE in content
        assert APPLY_BUTTON in content
        assert "What we expect from instructors" in content
        assert PENDING_BANNER not in content
        assert DECLINED_BANNER not in content

    def it_renders_the_pending_banner_and_no_apply_button_while_waiting(db, client):
        user, member = _active_member_user("pending-page@example.com")
        member.apply_to_teach("I would like to run a two hour intro to wheel throwing.")
        client.force_login(user)
        content = client.get(reverse("classes:teach_why")).content.decode()
        assert PENDING_BANNER in content
        assert "Waiting on an Admin" in content
        # The modal (and therefore its submit button) is not rendered while pending.
        assert "Send My Application" not in content

    def it_renders_the_decline_reason_and_an_apply_again_button(db, client):
        user, member = _active_member_user("declined-page@example.com")
        member.apply_to_teach("Wheel throwing.")
        member.decline_teaching(decided_by=None, reason="Finish the wheel orientation first.")
        client.force_login(user)
        content = client.get(reverse("classes:teach_why")).content.decode()
        assert DECLINED_BANNER in content
        assert "Finish the wheel orientation first." in content
        assert APPLY_AGAIN_BUTTON in content

    def it_renders_the_approved_banner_for_an_instructor(db, client):
        user, member = _active_member_user("approved-page@example.com")
        member.grant_teaching(granted_by=None)
        client.force_login(user)
        content = client.get(reverse("classes:teach_why")).content.decode()
        assert APPROVED_BANNER in content
        assert "Go to the Teaching Portal" in content
        assert APPLY_BUTTON not in content

    def it_reads_approved_for_a_grandfathered_instructor_who_never_applied(db, client):
        user, member = _active_member_user("grandfathered-page@example.com")
        member.instructor_oriented_at = timezone.now()
        member.save(update_fields=["instructor_oriented_at"])
        client.force_login(user)
        content = client.get(reverse("classes:teach_why")).content.decode()
        assert APPROVED_BANNER in content

    def it_403s_an_inactive_member(db, client):
        user = UserFactory(username="inactive-why@example.com")
        member = Member.objects.get(user=user)
        member.status = Member.Status.FORMER
        member.save(update_fields=["status"])
        client.force_login(user)
        assert client.get(reverse("classes:teach_why")).status_code == 403

    def it_fails_soft_when_the_help_center_seed_is_missing(db, client):
        user, _ = _active_member_user("noseed-why@example.com")
        client.force_login(user)
        content = client.get(reverse("classes:teach_why")).content.decode()
        assert PLACEHOLDER in content
        assert APPLY_BUTTON in content  # the apply path is never blocked by a missing seed

    def describe_the_example_class():
        def it_renders_the_real_catalog_card_when_one_is_configured(db, client):
            example = _published_example()
            user, _ = _active_member_user("example-set@example.com")
            client.force_login(user)
            content = client.get(reverse("classes:teach_why")).content.decode()
            assert "pl-teach-showcase__grid" in content
            assert "This is a live catalog card, not a picture of one." in content
            assert example.title in content
            assert example.public_url in content

        def it_falls_back_to_a_catalog_link_when_none_is_configured(db, client):
            user, _ = _active_member_user("example-none@example.com")
            client.force_login(user)
            content = client.get(reverse("classes:teach_why")).content.decode()
            assert "Have a Look at the Catalog" in content
            assert "pl-teach-showcase__grid" not in content

        def it_falls_back_when_the_configured_example_is_not_published(db, client):
            offering = ClassOfferingFactory(status=ClassOffering.Status.DRAFT)
            settings_obj = ClassSettings.load()
            settings_obj.example_class = offering
            settings_obj.save()
            user, _ = _active_member_user("example-draft@example.com")
            client.force_login(user)
            content = client.get(reverse("classes:teach_why")).content.decode()
            assert "Have a Look at the Catalog" in content
            assert "pl-teach-showcase__grid" not in content


def describe_teach_overview_branching():
    def it_shows_the_marketing_page_to_a_member_who_cannot_teach(db, client):
        user, _ = _active_member_user("locked-overview@example.com")
        client.force_login(user)
        response = client.get(reverse("classes:teach_overview"))
        assert response.status_code == 200
        assert HERO_TITLE in response.content.decode()

    def it_shows_the_dashboard_to_a_member_who_can_teach(db, client):
        user, member = _active_member_user("unlocked-overview@example.com")
        member.instructor_oriented_at = timezone.now()
        member.save(update_fields=["instructor_oriented_at"])
        client.force_login(user)
        content = client.get(reverse("classes:teach_overview")).content.decode()
        assert HERO_TITLE not in content

    def it_403s_an_inactive_member(db, client):
        user = UserFactory(username="inactive-overview@example.com")
        member = Member.objects.get(user=user)
        member.status = Member.Status.FORMER
        member.save(update_fields=["status"])
        client.force_login(user)
        assert client.get(reverse("classes:teach_overview")).status_code == 403


def describe_teach_apply():
    def it_files_the_application_and_redirects_to_the_portal(db, client):
        user, member = _active_member_user("apply-ok@example.com")
        client.force_login(user)
        response = client.post(reverse("classes:teach_apply"), {"note": "Intro to wheel throwing, two hours."})
        assert response.status_code == 302
        assert response["Location"] == reverse("classes:teach_overview")
        member.refresh_from_db()
        assert member.teaching_applied_at is not None
        assert member.teaching_application_note == "Intro to wheel throwing, two hours."
        assert SiteActivity.objects.filter(kind=SiteActivity.Kind.TEACHING_APPLIED).count() == 1

    def it_rerenders_with_the_field_error_on_a_blank_note(db, client):
        user, member = _active_member_user("apply-blank@example.com")
        client.force_login(user)
        response = client.post(reverse("classes:teach_apply"), {"note": "   "})
        assert response.status_code == 200
        assert BLANK_NOTE_ERROR in response.content.decode()
        member.refresh_from_db()
        assert member.teaching_applied_at is None

    def it_reopens_the_modal_on_an_invalid_submit(db, client):
        user, _ = _active_member_user("apply-reopen@example.com")
        client.force_login(user)
        content = client.post(reverse("classes:teach_apply"), {"note": ""}).content.decode()
        assert "$dispatch('open-modal', 'apply-to-teach')" in content

    def it_refuses_a_second_application_while_one_is_pending(db, client):
        user, member = _active_member_user("apply-twice@example.com")
        member.apply_to_teach("First ask.")
        first_stamp = Member.objects.get(pk=member.pk).teaching_applied_at
        client.force_login(user)
        response = client.post(reverse("classes:teach_apply"), {"note": "Second ask."})
        assert response.status_code == 302
        assert response["Location"] == reverse("classes:teach_why")
        member.refresh_from_db()
        assert member.teaching_applied_at == first_stamp
        assert member.teaching_application_note == "First ask."

    def it_403s_an_inactive_member_posting_directly(db, client):
        user = UserFactory(username="apply-inactive@example.com")
        member = Member.objects.get(user=user)
        member.status = Member.Status.FORMER
        member.save(update_fields=["status"])
        client.force_login(user)
        response = client.post(reverse("classes:teach_apply"), {"note": "Let me in."})
        assert response.status_code == 403
        member.refresh_from_db()
        assert member.teaching_applied_at is None

    def it_405s_a_get(db, client):
        user, _ = _active_member_user("apply-get@example.com")
        client.force_login(user)
        assert client.get(reverse("classes:teach_apply")).status_code == 405

    def it_redirects_an_anonymous_visitor_to_login(db, client):
        response = client.post(reverse("classes:teach_apply"), {"note": "Let me in."})
        assert response.status_code == 302
        assert "login" in response["Location"]


def describe_retired_orientation_route():
    def it_permanently_redirects_to_the_marketing_page(db, client):
        user, _ = _active_member_user("old-orientation@example.com")
        client.force_login(user)
        response = client.get(reverse("classes:teach_orientation"))
        assert response.status_code == 301
        assert response["Location"] == reverse("classes:teach_why")
