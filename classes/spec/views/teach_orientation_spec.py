"""BDD specs for the instructor orientation page and completion POST (Spec D §6).

The page is guarded by ``active_member_required`` (a locked member must be able
to reach the page that unlocks them); the completion form is server-enforced,
idempotent on a double-submit, and never blocked by a missing seed.
"""

from __future__ import annotations

from django.urls import reverse
from django.utils import timezone

from classes.factories import UserFactory
from classes.forms import InstructorOrientationCompleteForm
from core.models import SiteActivity
from membership.models import Member, OrgInfoPage, WikiArticle

BANNER = "One quick step before you can teach"
UNLOCK_BUTTON = "Unlock teaching"
# Template-literal copy is NOT autoescaped (only variables are), so these match verbatim.
COMPLETED_CARD = "You're an instructor"
PLACEHOLDER = "Orientation content hasn't been loaded yet"
FORM_ERROR = "Please confirm you&#x27;ve read the orientation before unlocking teaching."


def _active_member_user(username: str):
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


def describe_teach_orientation():
    def it_renders_banner_content_and_form_for_a_locked_member(db, client):
        _seed_orientation_article()
        user, _ = _active_member_user("locked-page@example.com")
        client.force_login(user)
        response = client.get(reverse("classes:teach_orientation"))
        assert response.status_code == 200
        content = response.content.decode()
        assert BANNER in content
        assert "What we expect from instructors" in content
        assert UNLOCK_BUTTON in content
        assert 'data-help-key="teach.become-instructor"' in content

    def it_renders_the_completed_state_without_the_form_for_an_unlocked_member(db, client):
        _seed_orientation_article()
        user, member = _active_member_user("done-page@example.com")
        member.instructor_oriented_at = timezone.now()
        member.save(update_fields=["instructor_oriented_at"])
        client.force_login(user)
        content = client.get(reverse("classes:teach_orientation")).content.decode()
        assert COMPLETED_CARD in content
        assert BANNER not in content
        assert UNLOCK_BUTTON not in content
        assert "What we expect from instructors" in content  # content stays readable

    def it_403s_an_inactive_member(db, client):
        user = UserFactory(username="inactive-page@example.com")
        member = Member.objects.get(user=user)
        member.status = Member.Status.FORMER
        member.save(update_fields=["status"])
        client.force_login(user)
        assert client.get(reverse("classes:teach_orientation")).status_code == 403

    def it_fails_soft_when_the_seed_is_missing_but_keeps_the_form(db, client):
        user, _ = _active_member_user("noseed-page@example.com")
        client.force_login(user)
        content = client.get(reverse("classes:teach_orientation")).content.decode()
        assert PLACEHOLDER in content
        assert UNLOCK_BUTTON in content  # the gate must never be un-passable


def describe_teach_orientation_complete():
    def it_rerenders_with_the_field_error_when_the_box_is_unchecked(db, client):
        user, member = _active_member_user("unchecked@example.com")
        client.force_login(user)
        response = client.post(reverse("classes:teach_orientation_complete"), {})
        assert response.status_code == 200
        assert FORM_ERROR in response.content.decode()
        member.refresh_from_db()
        assert member.instructor_oriented_at is None

    def it_unlocks_and_redirects_to_the_teach_overview_when_checked(db, client):
        user, member = _active_member_user("checked@example.com")
        client.force_login(user)
        response = client.post(reverse("classes:teach_orientation_complete"), {"acknowledge": "on"})
        assert response.status_code == 302
        assert response["Location"] == reverse("classes:teach_overview")
        member.refresh_from_db()
        assert member.instructor_oriented_at is not None
        assert SiteActivity.objects.filter(kind=SiteActivity.Kind.INSTRUCTOR_ORIENTED).count() == 1

    def it_logs_only_one_activity_row_on_a_double_post(db, client):
        user, _ = _active_member_user("double@example.com")
        client.force_login(user)
        client.post(reverse("classes:teach_orientation_complete"), {"acknowledge": "on"})
        response = client.post(reverse("classes:teach_orientation_complete"), {"acknowledge": "on"})
        assert response.status_code == 302
        assert SiteActivity.objects.filter(kind=SiteActivity.Kind.INSTRUCTOR_ORIENTED).count() == 1


def describe_InstructorOrientationCompleteForm():
    def it_requires_the_acknowledge_box_with_the_stated_message():
        form = InstructorOrientationCompleteForm({})
        assert not form.is_valid()
        assert form.errors["acknowledge"] == ["Please confirm you've read the orientation before unlocking teaching."]

    def it_accepts_a_ticked_box():
        form = InstructorOrientationCompleteForm({"acknowledge": "on"})
        assert form.is_valid()
