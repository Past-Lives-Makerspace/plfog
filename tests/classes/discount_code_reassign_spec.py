"""The Re-assign modal for an admin discount code (#764, part 1): the class picker, the form and the view."""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from classes.factories import ClassOfferingFactory, ClassSessionFactory, DiscountCodeFactory, InstructorFactory
from classes.forms import DiscountCodeReassignForm
from classes.models import ClassOffering, DiscountCode
from membership.models import Member
from tests.membership.factories import MembershipPlanFactory

pytestmark = pytest.mark.django_db

HX = {"HTTP_HX_REQUEST": "true"}


def _user(username: str, *, fog_role: str = Member.FogRole.MEMBER, **member_fields: Any):
    """A signed-in-ready user whose Member (provisioned by the signal) carries ``fog_role`` and ``member_fields``."""
    MembershipPlanFactory()
    user = get_user_model().objects.create_user(username=username, email=f"{username}@example.com")
    member = user.member
    member.status = Member.Status.ACTIVE
    member.fog_role = fog_role
    for name, value in member_fields.items():
        setattr(member, name, value)
    member.save()
    member.sync_user_permissions()
    return user


def _starting(offering: ClassOffering, delta: timedelta) -> None:
    ClassSessionFactory(class_offering=offering, starts_at=timezone.now() + delta)


def _url(code: DiscountCode) -> str:
    return reverse("classes:admin_discount_code_reassign", kwargs={"pk": code.pk})


def describe_discount_code_targets():
    def it_lists_upcoming_classes_by_title_and_leaves_out_cancelled_archived_and_started():
        zebra = ClassOfferingFactory(title="Zebra Weaving")
        _starting(zebra, timedelta(days=3))
        apple = ClassOfferingFactory(title="Apple Carving")
        _starting(apple, timedelta(days=5))
        ClassOfferingFactory(title="Cancelled", status=ClassOffering.Status.CANCELLED)
        ClassOfferingFactory(title="Archived", status=ClassOffering.Status.ARCHIVED)
        started = ClassOfferingFactory(title="Started")
        _starting(started, -timedelta(days=1))
        assert list(ClassOffering.objects.discount_code_targets()) == [apple, zebra]

    def it_keeps_the_codes_current_class_even_once_it_has_started():
        started = ClassOfferingFactory(title="Started")
        _starting(started, -timedelta(days=1))
        assert list(ClassOffering.objects.discount_code_targets()) == []
        assert list(ClassOffering.objects.discount_code_targets(keep_pk=started.pk)) == [started]


def describe_DiscountCodeReassignForm():
    def it_starts_on_the_codes_current_scope():
        offering = ClassOfferingFactory()
        scoped = DiscountCodeReassignForm(code=DiscountCodeFactory(class_offering=offering))
        assert scoped.initial == {
            "scope": DiscountCode.Scope.ONE_CLASS,
            "class_offering": offering.pk,
            "instructor": None,
        }
        everywhere = DiscountCodeReassignForm(code=DiscountCodeFactory(class_offering=None))
        assert everywhere.initial == {
            "scope": DiscountCode.Scope.EVERYWHERE,
            "class_offering": None,
            "instructor": None,
        }

    def it_offers_the_picker_classes_and_keeps_the_current_one():
        started = ClassOfferingFactory(title="Started")
        _starting(started, -timedelta(days=1))
        soon = ClassOfferingFactory(title="Soon")
        form = DiscountCodeReassignForm(code=DiscountCodeFactory(class_offering=started))
        assert list(form.fields["class_offering"].queryset) == [soon, started]

    def it_requires_a_class_for_one_class():
        form = DiscountCodeReassignForm({"scope": "class"}, code=DiscountCodeFactory())
        assert not form.is_valid()
        assert form.errors["class_offering"] == ["Pick the class this code should work on."]

    def it_rejects_a_class_the_picker_does_not_offer():
        cancelled = ClassOfferingFactory(status=ClassOffering.Status.CANCELLED)
        form = DiscountCodeReassignForm(
            {"scope": "class", "class_offering": str(cancelled.pk)}, code=DiscountCodeFactory()
        )
        assert not form.is_valid()
        assert "class_offering" in form.errors

    def it_drops_a_class_posted_with_everywhere():
        offering = ClassOfferingFactory()
        code = DiscountCodeFactory(class_offering=offering)
        form = DiscountCodeReassignForm({"scope": "everywhere", "class_offering": str(offering.pk)}, code=code)
        assert form.is_valid(), form.errors
        assert form.cleaned_data["class_offering"] is None
        form.save()
        code.refresh_from_db()
        assert code.class_offering is None

    def it_saves_through_reassign():
        offering = ClassOfferingFactory()
        code = DiscountCodeFactory(class_offering=None)
        form = DiscountCodeReassignForm({"scope": "class", "class_offering": str(offering.pk)}, code=code)
        assert form.is_valid(), form.errors
        assert form.save() is code
        code.refresh_from_db()
        assert code.class_offering == offering


def describe_admin_discount_code_reassign():
    @pytest.fixture
    def admin_client() -> Client:
        client = Client()
        client.force_login(_user("reassign-admin", fog_role=Member.FogRole.ADMIN))
        return client

    def describe_when_an_admin_opens_the_modal():
        def it_renders_the_form_partial_for_htmx(admin_client):
            offering = ClassOfferingFactory(title="Bat Carving")
            code = DiscountCodeFactory(code="BAT30", class_offering=offering)
            response = admin_client.get(_url(code), **HX)
            assert response.status_code == 200
            html = response.content.decode()
            assert "<html" not in html
            assert "data-reassign-form" in html
            assert f'hx-post="{_url(code)}"' in html
            assert re.search(r'<input[^>]*value="class"[^>]*checked', html)
            assert (
                f'<option value="{offering.pk}" selected data-name="Bat Carving">Bat Carving · No dates yet · Draft (current)</option>'
                in html
            )
            assert "BAT30 will work only on Bat Carving." in html

        def it_sends_a_plain_get_back_to_the_list(admin_client):
            response = admin_client.get(_url(DiscountCodeFactory()))
            assert response.status_code == 302
            assert response.url == reverse("classes:admin_discount_codes")

        def it_treats_a_history_restore_as_a_plain_get(admin_client):
            response = admin_client.get(
                _url(DiscountCodeFactory()), HTTP_HX_REQUEST="true", HTTP_HX_HISTORY_RESTORE_REQUEST="true"
            )
            assert response.status_code == 302

    def describe_when_an_admin_saves():
        def it_moves_the_code_and_redirects_the_modal_to_the_list(admin_client):
            offering = ClassOfferingFactory(title="Bat Carving")
            code = DiscountCodeFactory(code="BAT30", class_offering=None)
            response = admin_client.post(_url(code), {"scope": "class", "class_offering": offering.pk}, **HX)
            assert response.status_code == 204
            assert response["HX-Redirect"] == reverse("classes:admin_discount_codes")
            code.refresh_from_db()
            assert code.class_offering == offering
            assert [str(m) for m in get_messages(response.wsgi_request)] == ["BAT30 now works only on Bat Carving."]

        def it_sets_a_class_code_back_to_everywhere_and_keeps_its_approval(admin_client):
            code = DiscountCodeFactory(code="BAT30", class_offering=ClassOfferingFactory(), is_approved=True)
            response = admin_client.post(_url(code), {"scope": "everywhere"}, **HX)
            assert response.status_code == 204
            code.refresh_from_db()
            assert code.class_offering is None
            assert code.is_approved is True
            assert [str(m) for m in get_messages(response.wsgi_request)] == ["BAT30 now works on every class."]

        def it_redirects_outright_without_htmx(admin_client):
            code = DiscountCodeFactory(class_offering=ClassOfferingFactory())
            response = admin_client.post(_url(code), {"scope": "everywhere"})
            assert response.status_code == 302
            assert response.url == reverse("classes:admin_discount_codes")
            code.refresh_from_db()
            assert code.class_offering is None

        def it_re_renders_the_form_with_the_error_in_the_modal(admin_client):
            code = DiscountCodeFactory(class_offering=None)
            response = admin_client.post(_url(code), {"scope": "class"}, **HX)
            assert response.status_code == 200
            html = response.content.decode()
            assert "data-reassign-form" in html
            assert "Pick the class this code should work on." in html
            code.refresh_from_db()
            assert code.class_offering is None

    def describe_who_may_re_assign():
        def _forbidden(user) -> None:
            client = Client()
            client.force_login(user)
            code = DiscountCodeFactory(class_offering=None, created_by=user)
            assert client.get(_url(code), **HX).status_code == 403
            assert client.post(_url(code), {"scope": "everywhere"}, **HX).status_code == 403

        def it_forbids_a_plain_member():
            _forbidden(_user("reassign-member"))

        def it_forbids_a_self_approver_on_their_own_code():
            _forbidden(_user("reassign-selfapprover", can_self_approve_discounts=True))

        def it_forbids_an_instructor():
            user = _user("reassign-teacher", instructor_slug="reassign-teacher", instructor_oriented_at=timezone.now())
            ClassOfferingFactory(instructor=user.member)
            _forbidden(user)

        def it_sends_an_anonymous_visitor_to_sign_in():
            response = Client().get(_url(DiscountCodeFactory()), **HX)
            assert response.status_code == 302
            assert "next=" in response.url


def describe_DiscountCodeReassignForm_for_an_instructor():
    """Part 2 of #764: the third answer, every class one member teaches."""

    def it_starts_on_the_instructor_scope_with_the_instructor_picked():
        teacher = InstructorFactory()
        form = DiscountCodeReassignForm(code=DiscountCodeFactory(class_offering=None, instructor=teacher))
        assert form.initial == {
            "scope": DiscountCode.Scope.INSTRUCTOR,
            "class_offering": None,
            "instructor": teacher.pk,
        }

    def it_offers_the_members_who_teach_and_keeps_the_current_one():
        idle = InstructorFactory(full_legal_name="Idle Teacher")
        busy = InstructorFactory(full_legal_name="Busy Teacher")
        ClassOfferingFactory(instructor=busy)
        form = DiscountCodeReassignForm(code=DiscountCodeFactory(class_offering=None, instructor=idle))
        assert list(form.fields["instructor"].queryset) == [busy, idle]

    def it_requires_an_instructor_for_an_instructors_classes():
        form = DiscountCodeReassignForm({"scope": "instructor"}, code=DiscountCodeFactory())
        assert not form.is_valid()
        assert form.errors["instructor"] == ["Pick the instructor whose classes this code should work on."]

    def it_keeps_only_the_target_the_scope_names():
        teacher = InstructorFactory()
        offering = ClassOfferingFactory(instructor=teacher)
        code = DiscountCodeFactory(class_offering=offering)
        form = DiscountCodeReassignForm(
            {"scope": "instructor", "class_offering": str(offering.pk), "instructor": str(teacher.pk)}, code=code
        )
        assert form.is_valid(), form.errors
        form.save()
        code.refresh_from_db()
        assert (code.class_offering, code.instructor) == (None, teacher)


def describe_admin_discount_code_reassign_to_an_instructor():
    def it_moves_the_code_and_says_whose_classes_it_covers():
        client = Client()
        client.force_login(_user("reassign-admin-2", fog_role=Member.FogRole.ADMIN))
        teacher = InstructorFactory(full_legal_name="Jacqueline Sowell")
        ClassOfferingFactory(instructor=teacher)
        code = DiscountCodeFactory(code="JACQUELINE30", class_offering=None, is_approved=True)
        response = client.post(_url(code), {"scope": "instructor", "instructor": teacher.pk}, **HX)
        assert response.status_code == 204
        code.refresh_from_db()
        assert (code.instructor, code.class_offering, code.is_approved) == (teacher, None, True)
        assert [str(m) for m in get_messages(response.wsgi_request)] == [
            "JACQUELINE30 now works on every class Jacqueline Sowell teaches, now and later."
        ]

    def it_opens_an_instructor_code_on_its_instructor():
        client = Client()
        client.force_login(_user("reassign-admin-3", fog_role=Member.FogRole.ADMIN))
        teacher = InstructorFactory(full_legal_name="Jacqueline Sowell")
        code = DiscountCodeFactory(code="JACQUELINE30", class_offering=None, instructor=teacher)
        html = client.get(_url(code), **HX).content.decode()
        assert re.search(r'<input[^>]*value="instructor"[^>]*checked', html)
        assert (
            f'<option value="{teacher.pk}" selected data-name="Jacqueline Sowell">Jacqueline Sowell (current)</option>'
            in html
        )
        assert "JACQUELINE30 will work on every class Jacqueline Sowell teaches, now and later." in html


def describe_the_class_discount_codes_tab():
    """AC6: a class's Discount Codes tab lists its instructor's code with its scope."""

    def _tab(client: Client, offering: ClassOffering) -> str:
        return client.get(reverse("classes:teach_class_discount_codes", kwargs={"pk": offering.pk})).content.decode()

    def it_lists_the_instructors_code_labelled_with_their_name_on_their_classes_only():
        client = Client()
        client.force_login(_user("tab-admin", fog_role=Member.FogRole.ADMIN))
        teacher = InstructorFactory(full_legal_name="Jacqueline Sowell")
        theirs = ClassOfferingFactory(instructor=teacher)
        later = ClassOfferingFactory(instructor=teacher)  # made after the code was assigned
        someone_elses = ClassOfferingFactory()
        legacy = ClassOfferingFactory(instructor=None)
        DiscountCodeFactory(code="JACQUELINE30", class_offering=None, instructor=teacher)
        for offering in (theirs, later):
            html = _tab(client, offering)
            assert "JACQUELINE30" in html
            assert '<span class="hub-pill hub-pill--neutral" data-scope-instructor>Jacqueline Sowell</span>' in html
        for offering in (someone_elses, legacy):
            assert "JACQUELINE30" not in _tab(client, offering)


def _choice_labels(form: DiscountCodeReassignForm, field: str) -> list[str]:
    return [str(label) for value, label in form.fields[field].choices if value != ""]


def describe_the_pickers_labels():
    """#818: an admin can tell look-alike runs apart, and only current instructors are offered."""

    def it_labels_each_class_with_its_dates_and_any_unpublished_status():
        first = ClassOfferingFactory(
            title="Picture Framing", slug="picture-framing-a", status=ClassOffering.Status.PUBLISHED
        )
        when = timezone.now() + timedelta(days=10)
        ClassSessionFactory(class_offering=first, starts_at=when)
        ClassSessionFactory(class_offering=first, starts_at=when + timedelta(days=7))
        ClassSessionFactory(class_offering=first, starts_at=when + timedelta(days=14))
        second = ClassOfferingFactory(
            title="Picture Framing", slug="picture-framing-b", status=ClassOffering.Status.DRAFT
        )
        ClassSessionFactory(class_offering=second, starts_at=when + timedelta(days=30))
        ClassOfferingFactory(title="Undated", status=ClassOffering.Status.PENDING)
        ClassOfferingFactory(
            title="Anytime",
            status=ClassOffering.Status.PUBLISHED,
            scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE,
        )
        labels = _choice_labels(
            DiscountCodeReassignForm(code=DiscountCodeFactory(class_offering=None)), "class_offering"
        )
        day = timezone.localtime(when).strftime("%a %b %-d")
        later = timezone.localtime(when + timedelta(days=30)).strftime("%a %b %-d")
        assert f"Picture Framing · {day} (and 2 more dates)" in labels
        assert f"Picture Framing · {later} · Draft" in labels
        assert "Undated · No dates yet · Pending Review" in labels
        assert "Anytime · Flexible" in labels

    def it_marks_the_codes_current_class():
        offering = ClassOfferingFactory(title="Kept", status=ClassOffering.Status.PUBLISHED)
        labels = _choice_labels(
            DiscountCodeReassignForm(code=DiscountCodeFactory(class_offering=offering)), "class_offering"
        )
        assert "Kept · No dates yet (current)" in labels

    def it_builds_the_class_labels_without_a_query_per_class(django_assert_max_num_queries):
        for n in range(6):
            offering = ClassOfferingFactory(title=f"Run {n}")
            ClassSessionFactory(class_offering=offering, starts_at=timezone.now() + timedelta(days=n + 1))
        form = DiscountCodeReassignForm(code=DiscountCodeFactory(class_offering=None))
        with django_assert_max_num_queries(2):
            _choice_labels(form, "class_offering")

    def it_offers_only_active_instructors_who_still_hold_instructor_access():
        current = InstructorFactory(full_legal_name="Current Teacher")
        former = InstructorFactory(full_legal_name="Former Teacher", status=Member.Status.FORMER)
        no_access = InstructorFactory(full_legal_name="Lapsed Teacher", instructor_slug="")
        deleted = InstructorFactory(full_legal_name="Deleted Member", instructor_slug="", status=Member.Status.FORMER)
        for teacher in (current, former, no_access, deleted):
            ClassOfferingFactory(instructor=teacher)
        labels = _choice_labels(DiscountCodeReassignForm(code=DiscountCodeFactory(class_offering=None)), "instructor")
        assert labels == ["Current Teacher"]

    def it_keeps_and_marks_the_codes_current_instructor_even_once_they_no_longer_qualify():
        former = InstructorFactory(full_legal_name="Former Teacher", status=Member.Status.FORMER)
        ClassOfferingFactory(instructor=former)
        code = DiscountCodeFactory(class_offering=None, instructor=former)
        assert _choice_labels(DiscountCodeReassignForm(code=code), "instructor") == ["Former Teacher (current)"]

    def it_says_a_kept_class_has_ended_instead_of_showing_an_old_date_as_next():
        offering = ClassOfferingFactory(title="Glen 101", status=ClassOffering.Status.PUBLISHED)
        last = timezone.now() - timedelta(days=3)
        ClassSessionFactory(class_offering=offering, starts_at=last - timedelta(days=7))
        ClassSessionFactory(class_offering=offering, starts_at=last)
        labels = _choice_labels(
            DiscountCodeReassignForm(code=DiscountCodeFactory(class_offering=offering)), "class_offering"
        )
        assert f"Glen 101 · Ended {timezone.localtime(last).strftime('%a %b %-d')} (current)" in labels

    def it_gives_every_option_its_bare_name_for_the_live_sentence():
        offering = ClassOfferingFactory(title="Clay · Wheel Intro")
        teacher = InstructorFactory(full_legal_name="Jacqueline Sowell")
        ClassOfferingFactory(instructor=teacher)
        html = str(DiscountCodeReassignForm(code=DiscountCodeFactory(class_offering=offering)))
        assert 'data-name="Clay · Wheel Intro"' in html
        assert 'data-name="Jacqueline Sowell"' in html
