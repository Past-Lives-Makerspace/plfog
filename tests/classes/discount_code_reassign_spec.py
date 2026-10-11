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

from classes.factories import ClassOfferingFactory, ClassSessionFactory, DiscountCodeFactory
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
        assert scoped.initial == {"scope": DiscountCode.Scope.ONE_CLASS, "class_offering": offering.pk}
        everywhere = DiscountCodeReassignForm(code=DiscountCodeFactory(class_offering=None))
        assert everywhere.initial == {"scope": DiscountCode.Scope.EVERYWHERE, "class_offering": None}

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
            assert f'<option value="{offering.pk}" selected>Bat Carving</option>' in html
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
