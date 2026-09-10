"""BDD specs for the admin Teaching Applications queue and its two actions (spec §A.7).

The queue card lives on the classes admin Overview. Approving routes through
``grant_instructor`` (the same call the member edit Permissions tab makes); declining
requires a reason the applicant will read. Both are admin-only, POST-only.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.urls import reverse

from core.models import Notification
from membership.models import Member, MembershipPlan


def _applicant(username: str, note: str = "Intro to wheel throwing.") -> Member:
    """An ACTIVE member with a pending teaching application."""
    plan, _ = MembershipPlan.objects.get_or_create(name="Standard", defaults={"monthly_price": "50.00"})
    user, _ = get_user_model().objects.get_or_create(username=username, defaults={"email": username})
    member = Member.objects.get(user=user)
    member.status = Member.Status.ACTIVE
    member.membership_plan = plan
    member.full_legal_name = "Robin Applicant"
    member.save(update_fields=["status", "membership_plan", "full_legal_name"])
    member.apply_to_teach(note)
    return member


def describe_teaching_applications_card():
    def it_lists_a_waiting_applicant_with_their_note(admin_user, client, db):
        member = _applicant("queued@example.com", note="I would like to run a two hour intro.")
        client.force_login(admin_user)
        content = client.get(reverse("classes:admin_overview")).content.decode()
        # The queue is a group inside the merged "Needs Attention" card now.
        assert "Needs Attention" in content
        assert "Interested in Teaching" in content
        assert "is interested in becoming an instructor." in content
        assert member.display_name in content
        assert "I would like to run a two hour intro." in content
        assert reverse("classes:admin_teaching_approve", kwargs={"pk": member.pk}) in content
        assert reverse("classes:admin_teaching_decline", kwargs={"pk": member.pk}) in content

    def it_lands_the_mailed_anchor_on_the_applications_themselves(admin_user, client, db):
        """membership/models.py mails admins #teaching-applications when somebody applies.

        Inside the merged card the applications sit below two other queues, so the anchor has
        to be ON that group, not on the card, or the link drops them somewhere else.
        """
        _applicant("anchored@example.com")
        client.force_login(admin_user)
        content = client.get(reverse("classes:admin_overview")).content.decode()
        assert content.count('id="teaching-applications"') == 1
        anchor_at = content.index('id="teaching-applications"')
        assert content.index("Interested in Teaching") > anchor_at
        # ...and it is the group carrying it, not the wrapper wrapping every queue.
        assert content.index("Needs Attention") < anchor_at

    def it_says_asked_today_for_a_same_day_ask(admin_user, client, db):
        """A zero day wait must not read as 'asked 0 days ago'."""
        member = _applicant("today@example.com", note="Intro to wheel throwing.")
        client.force_login(admin_user)
        content = client.get(reverse("classes:admin_overview")).content.decode()
        assert "asked today" in content
        assert "asked 0 day" not in content
        assert member.display_name in content

    def it_hides_the_group_entirely_with_nobody_waiting(admin_user, client, db):
        """An empty queue renders nothing at all now — not a heading over a reassurance line."""
        client.force_login(admin_user)
        content = client.get(reverse("classes:admin_overview")).content.decode()
        assert "Interested in Teaching" not in content
        assert "Needs Attention \u00b7 all clear" in content

    def it_drops_an_applicant_once_they_are_approved(admin_user, client, db):
        member = _applicant("approved-out@example.com")
        member.grant_teaching(granted_by=None)
        client.force_login(admin_user)
        content = client.get(reverse("classes:admin_overview")).content.decode()
        assert "Interested in Teaching" not in content
        assert member.display_name not in content

    def it_drops_an_applicant_once_they_are_declined(admin_user, client, db):
        member = _applicant("declined-out@example.com")
        member.decline_teaching(decided_by=None, reason="Not yet.")
        client.force_login(admin_user)
        content = client.get(reverse("classes:admin_overview")).content.decode()
        assert "Interested in Teaching" not in content
        assert member.display_name not in content

    def it_drops_an_applicant_who_stopped_being_active(admin_user, client, db):
        member = _applicant("former-out@example.com")
        member.status = Member.Status.FORMER
        member.save(update_fields=["status"])
        client.force_login(admin_user)
        content = client.get(reverse("classes:admin_overview")).content.decode()
        assert "Interested in Teaching" not in content
        assert member.display_name not in content


def describe_admin_teaching_approve():
    def it_grants_the_instructor_permission_and_redirects(admin_user, client, db):
        member = _applicant("approve-me@example.com")
        client.force_login(admin_user)
        response = client.post(reverse("classes:admin_teaching_approve", kwargs={"pk": member.pk}))
        assert response.status_code == 302
        assert response["Location"] == reverse("classes:admin_overview")
        member.refresh_from_db()
        assert member.can_create_classes is True
        assert member.instructor_slug  # grant_instructor mints the public page too

    def it_says_it_let_them_know_when_it_answered_their_note(admin_user, client, db):
        member = _applicant("approve-told@example.com")
        client.force_login(admin_user)
        response = client.post(reverse("classes:admin_teaching_approve", kwargs={"pk": member.pk}), follow=True)
        content = response.content.decode()
        assert f"Teaching is on for {member.display_name}. We let them know." in content
        assert Notification.objects.filter(trigger="instructor_application_approved", user=member.user).count() == 1

    def it_does_not_claim_an_email_for_someone_who_never_asked(admin_user, client, db):
        """grant_teaching sends nothing to a member with no note, so the message must not say it did."""
        plan, _ = MembershipPlan.objects.get_or_create(name="Standard", defaults={"monthly_price": "50.00"})
        user, _ = get_user_model().objects.get_or_create(username="approve-unasked@example.com")
        member = Member.objects.get(user=user)
        member.status = Member.Status.ACTIVE
        member.membership_plan = plan
        member.full_legal_name = "Quiet Member"
        member.save(update_fields=["status", "membership_plan", "full_legal_name"])
        client.force_login(admin_user)
        response = client.post(reverse("classes:admin_teaching_approve", kwargs={"pk": member.pk}), follow=True)
        content = response.content.decode()
        assert f"Teaching is on for {member.display_name}." in content
        assert "We let them know." not in content
        member.refresh_from_db()
        assert member.can_create_classes is True
        assert Notification.objects.filter(trigger="instructor_application_approved").count() == 0

    def it_does_not_claim_an_email_when_teaching_was_already_on(admin_user, client, db):
        """A second click after the grant is a no-op in the model and sends no second email."""
        member = _applicant("approve-again@example.com")
        member.grant_teaching(granted_by=None)
        assert Notification.objects.filter(trigger="instructor_application_approved").count() == 1
        client.force_login(admin_user)
        response = client.post(reverse("classes:admin_teaching_approve", kwargs={"pk": member.pk}), follow=True)
        content = response.content.decode()
        assert f"Teaching is on for {member.display_name}." in content
        assert "We let them know." not in content
        assert Notification.objects.filter(trigger="instructor_application_approved").count() == 1

    def it_404s_an_unknown_member(admin_user, client, db):
        client.force_login(admin_user)
        assert client.post(reverse("classes:admin_teaching_approve", kwargs={"pk": 999999})).status_code == 404

    def it_405s_a_get(admin_user, client, db):
        member = _applicant("approve-get@example.com")
        client.force_login(admin_user)
        url = reverse("classes:admin_teaching_approve", kwargs={"pk": member.pk})
        assert client.get(url).status_code == 405

    def it_403s_a_plain_member_posting_directly(member_user, client, db):
        member = _applicant("approve-hostile@example.com")
        client.force_login(member_user)
        response = client.post(reverse("classes:admin_teaching_approve", kwargs={"pk": member.pk}))
        assert response.status_code == 403
        member.refresh_from_db()
        assert member.can_create_classes is False


def describe_admin_teaching_decline():
    def it_records_the_reason_and_redirects(admin_user, client, db):
        member = _applicant("decline-me@example.com")
        client.force_login(admin_user)
        response = client.post(
            reverse("classes:admin_teaching_decline", kwargs={"pk": member.pk}),
            {"reason": "Do the wheel orientation first."},
        )
        assert response.status_code == 302
        assert response["Location"] == reverse("classes:admin_overview")
        member.refresh_from_db()
        assert member.teaching_decline_reason == "Do the wheel orientation first."
        assert member.teaching_application_state == Member.TeachingApplicationState.DECLINED

    def it_refuses_a_blank_reason_with_a_message(admin_user, client, db):
        member = _applicant("decline-blank@example.com")
        client.force_login(admin_user)
        response = client.post(reverse("classes:admin_teaching_decline", kwargs={"pk": member.pk}), {"reason": "  "})
        assert response.status_code == 302
        member.refresh_from_db()
        assert member.teaching_decided_at is None
        assert member.teaching_application_state == Member.TeachingApplicationState.PENDING

    def it_405s_a_get(admin_user, client, db):
        member = _applicant("decline-get@example.com")
        client.force_login(admin_user)
        url = reverse("classes:admin_teaching_decline", kwargs={"pk": member.pk})
        assert client.get(url).status_code == 405

    def it_403s_a_plain_member_posting_directly(member_user, client, db):
        member = _applicant("decline-hostile@example.com")
        client.force_login(member_user)
        response = client.post(reverse("classes:admin_teaching_decline", kwargs={"pk": member.pk}), {"reason": "No."})
        assert response.status_code == 403
        member.refresh_from_db()
        assert member.teaching_decided_at is None
