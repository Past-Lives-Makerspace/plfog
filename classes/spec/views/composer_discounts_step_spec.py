"""BDD specs for the composer's Discounts step (#428, part 2).

The step carries links and read only tables, never a control, so the per step check
(``composer_step_validation_spec.py``) has nothing to gate and Next always advances. Which
variant renders follows the viewer and the two site settings, and the pane says which through
``data-composer-discounts``: ``admin`` (the per class section with New Code and Edit), ``off``
(the master setting is off, so admins manage codes), ``request`` (approval mode: the site wide
codes, this class's codes read only, the requests and their status, and Request a Code) or
``direct`` (the approval setting off: instructors still make their own codes). The request form
stays on its own page, so the step never nests a form inside the composer's. Every hardcoded
step count in the template now reads ``classes.composer.STEP_COUNT``; the first spec pins that.
"""

from __future__ import annotations

from datetime import date
from typing import cast

import pytest
from django.urls import reverse

from classes.composer import STEP_COUNT
from classes.factories import (
    ClassOfferingFactory,
    DiscountCodeFactory,
    DiscountCodeRequestFactory,
    InstructorFactory,
    UserFactory,
)
from classes.models import ClassOffering, DiscountCodeRequest
from classes.spec.views.composer_step_validation_spec import TEMPLATE_PATH, _PaneParser
from classes.views import _composer_discounts_context
from core.models import SiteConfiguration

pytestmark = pytest.mark.django_db

CREATE = "classes:teach_class_create"
EDIT = "classes:teach_class_edit"
ADMIN_CREATE = "classes:admin_class_create"
REQUEST = "classes:teach_discount_code_request"
DIRECT_CREATE = "classes:teach_discount_code_create"
ADMIN_CODE_CREATE = "classes:admin_discount_code_create"
DISCOUNTS = 5


def _flags(*, master: bool, approval: bool) -> None:
    """The conftest fixture leaves the master setting on and the approval one off; each spec says its own."""
    config = SiteConfiguration.load()
    config.instructor_discount_codes_enabled = master
    config.instructor_discount_codes_need_approval = approval
    config.save(update_fields=["instructor_discount_codes_enabled", "instructor_discount_codes_need_approval"])


def _pane(html: str, n: int) -> str:
    """One step pane, from its own stamp to the next pane's; the last pane runs to the action bar."""
    start = html.index(f'data-composer-step="{n}"')
    end = html.index(f'data-composer-step="{n + 1}"') if n < STEP_COUNT else html.index("pl-composer-bar")
    return html[start:end]


def _stamp(pane: str) -> str:
    marker = 'data-composer-discounts="'
    at = pane.index(marker) + len(marker)
    return pane[at : pane.index('"', at)]


def _controls_on(html: str, n: int) -> list[str]:
    parser = _PaneParser()
    parser.feed(html)
    return [control.tag for control in parser.controls[n]]


@pytest.fixture
def instructor(db):
    user = UserFactory(username="discounts-step-teacher@example.com")
    return InstructorFactory(user=user, full_legal_name="Teacher D", instructor_slug="teacher-d")


def _draft(instructor) -> ClassOffering:
    return cast(ClassOffering, ClassOfferingFactory(instructor=instructor, status=ClassOffering.Status.DRAFT))


def _edit_page(client, offering: ClassOffering) -> str:
    return client.get(reverse(EDIT, kwargs={"pk": offering.pk})).content.decode()


def describe_the_step_count():
    def it_reads_the_step_count_from_the_map(client, instructor):
        client.force_login(instructor.user)
        html = client.get(reverse(CREATE)).content.decode()
        assert f"of {STEP_COUNT}</p>" in html
        assert f"{STEP_COUNT} steps." in html
        assert f'data-step-tab="{STEP_COUNT}"' in html
        assert f'data-composer-step="{STEP_COUNT}" x-show="phase === {STEP_COUNT}"' in html
        assert f'x-show="phase < {STEP_COUNT}"' in html
        # The Review pane, and the one action bar primary this viewer gets (Submit for Review on a new class).
        assert html.count(f'x-show="phase === {STEP_COUNT}"') == 2

        # The template carries the count nowhere: no spelled out total, no "last step" number, and the
        # one `phase === 5` left is the Discounts pane's own, numbered like every other pane.
        source = TEMPLATE_PATH.read_text(encoding="utf-8")
        assert "Five steps" not in source and "of 5" not in source and 'x-show="phase < 5"' not in source
        assert source.count('x-show="phase === 5"') == 1
        assert 'data-composer-step="5" x-show="phase === 5"' in source
        for literal in ('data-composer-step="6"', "phase === 6", "phase < 6", "of 6", "6 steps", "Six steps"):
            assert literal not in source, literal
        for read in (
            "{{ step_count }} steps.",
            "of {{ step_count }}</p>",
            'data-composer-step="{{ step_count }}" x-show="phase === {{ step_count }}"',
            'x-show="phase < {{ step_count }}"',
        ):
            assert read in source, read
        assert source.count('x-show="phase === {{ step_count }}"') == 4


def describe_the_admin_variant():
    def it_shows_the_admin_section_on_the_discounts_step_and_not_on_step_three(client, admin_user, instructor):
        client.force_login(admin_user)
        html = _edit_page(client, _draft(instructor))
        pane = _pane(html, DISCOUNTS)
        assert _stamp(pane) == "admin"
        assert reverse(ADMIN_CODE_CREATE) in pane
        assert reverse(ADMIN_CODE_CREATE) not in _pane(html, 3)
        assert reverse(REQUEST) not in html

    def it_tells_an_admin_to_save_first_on_a_new_class(client, admin_user):
        client.force_login(admin_user)
        html = client.get(reverse(ADMIN_CREATE)).content.decode()
        pane = _pane(html, DISCOUNTS)
        assert _stamp(pane) == "admin"
        # The section renders nothing for a class with no pk, so the pane says why it is empty.
        assert reverse(ADMIN_CODE_CREATE) not in pane
        assert "<table" not in pane


def describe_the_master_setting_off():
    def it_tells_an_instructor_that_admins_manage_codes(client, instructor):
        _flags(master=False, approval=True)
        client.force_login(instructor.user)
        html = _edit_page(client, _draft(instructor))
        pane = _pane(html, DISCOUNTS)
        assert _stamp(pane) == "off"
        assert reverse(REQUEST) not in pane
        assert reverse(DIRECT_CREATE) not in pane
        assert "<table" not in pane


def describe_approval_mode():
    def it_shows_global_codes_this_classs_codes_read_only_the_requests_and_the_request_link(client, instructor):
        _flags(master=True, approval=True)
        mine = _draft(instructor)
        DiscountCodeFactory(class_offering=None, code="MEMBER10", discount_pct=10)
        DiscountCodeFactory(
            class_offering=None,
            code="FALL5",
            discount_pct=None,
            discount_fixed_cents=500,
            valid_from=date(2026, 9, 1),
            valid_until=date(2026, 11, 30),
            max_uses=50,
            use_count=3,
        )
        DiscountCodeFactory(class_offering=None, code="OLDCODE", is_active=False)
        DiscountCodeFactory(class_offering=None, code="NOTYET", is_approved=False)
        DiscountCodeFactory(class_offering=ClassOfferingFactory(), code="OTHERS")
        mine_code = DiscountCodeFactory(class_offering=mine, code="EARLYBIRD", created_by=instructor.user)
        DiscountCodeRequestFactory(class_offering=mine, code="SPRING15")
        DiscountCodeRequestFactory(class_offering=ClassOfferingFactory(), code="ELSEWHERE")
        client.force_login(instructor.user)

        html = _edit_page(client, mine)

        pane = _pane(html, DISCOUNTS)
        assert _stamp(pane) == "request"
        for shown in ("MEMBER10", "10% off", "FALL5", "$5.00 off", "From Sep 1, 2026 until Nov 30, 2026", "3 / 50"):
            assert shown in pane, shown
        assert "EARLYBIRD" in pane and "SPRING15" in pane
        for hidden in ("OLDCODE", "NOTYET", "OTHERS", "ELSEWHERE"):
            assert hidden not in html, hidden
        assert f"{reverse(REQUEST)}?class={mine.pk}" in pane
        assert reverse(REQUEST) not in _pane(html, 3)
        assert reverse("classes:teach_discount_code_edit", kwargs={"pk": mine_code.pk}) not in html
        assert reverse(DIRECT_CREATE) not in html
        # Two read only tables of codes and the requests list: never a form inside the composer's form.
        assert "<form" not in pane

    def it_shows_the_empty_rows_when_there_is_nothing_to_list(client, instructor):
        _flags(master=True, approval=True)
        client.force_login(instructor.user)
        pane = _pane(_edit_page(client, _draft(instructor)), DISCOUNTS)
        assert pane.count('class="pl-table-empty"') == 2  # the site wide codes and the requests

    def it_shows_the_save_first_line_on_an_unsaved_class(client, instructor):
        _flags(master=True, approval=True)
        DiscountCodeFactory(class_offering=None, code="MEMBER10")
        client.force_login(instructor.user)
        html = client.get(reverse(CREATE)).content.decode()
        pane = _pane(html, DISCOUNTS)
        assert _stamp(pane) == "request"
        assert "MEMBER10" in pane
        # No class to request for yet: no request link, and only the site wide codes table.
        assert reverse(REQUEST) not in pane
        assert pane.count("<table") == 1


def describe_the_direct_flow():
    def it_keeps_the_direct_flow_section_when_the_approval_setting_is_off(client, instructor):
        _flags(master=True, approval=False)
        mine = _draft(instructor)
        code = DiscountCodeFactory(class_offering=mine, code="EARLYBIRD", created_by=instructor.user)
        client.force_login(instructor.user)
        html = _edit_page(client, mine)
        pane = _pane(html, DISCOUNTS)
        assert _stamp(pane) == "direct"
        assert f"{reverse(DIRECT_CREATE)}?class={mine.pk}" in pane
        assert reverse("classes:teach_discount_code_edit", kwargs={"pk": code.pk}) in pane
        assert reverse(DIRECT_CREATE) not in _pane(html, 3)
        assert reverse(REQUEST) not in html

    def it_tells_an_instructor_to_save_first_on_a_new_class(client, instructor):
        _flags(master=True, approval=False)
        client.force_login(instructor.user)
        pane = _pane(client.get(reverse(CREATE)).content.decode(), DISCOUNTS)
        assert _stamp(pane) == "direct"
        assert reverse(DIRECT_CREATE) not in pane
        assert pane.count("<table") == 1


def describe_no_control_on_the_step():
    def it_puts_no_control_on_the_discounts_step_in_any_variant(client, instructor, admin_user):
        # The per step check reads controls; a pane with none can never refuse Next.
        mine = _draft(instructor)
        DiscountCodeFactory(class_offering=None, code="MEMBER10")
        DiscountCodeFactory(class_offering=mine, code="EARLYBIRD", created_by=instructor.user)
        DiscountCodeRequestFactory(class_offering=mine, code="SPRING15")
        pages: list[str] = []
        for master, approval in ((False, True), (True, True), (True, False)):
            _flags(master=master, approval=approval)
            client.force_login(instructor.user)
            pages.append(_edit_page(client, mine))
            pages.append(client.get(reverse(CREATE)).content.decode())
        client.force_login(admin_user)
        pages.append(_edit_page(client, mine))
        pages.append(client.get(reverse(ADMIN_CREATE)).content.decode())
        assert len(pages) == 8
        for html in pages:
            assert _controls_on(html, DISCOUNTS) == []
            # And the pane is where the map says: right before the Review pane.
            assert html.index('data-composer-step="5"') < html.index(f'data-composer-step="{STEP_COUNT}"')


def describe_composer_discounts_context():
    def it_gives_an_admin_nothing_extra(instructor):
        _flags(master=True, approval=True)
        assert _composer_discounts_context(_draft(instructor), True) == {}

    def it_gives_nothing_with_the_master_setting_off(instructor):
        _flags(master=False, approval=True)
        assert _composer_discounts_context(_draft(instructor), False) == {}

    def it_lists_the_active_approved_site_wide_codes_and_the_class_requests_for_a_saved_class(instructor):
        _flags(master=True, approval=True)
        mine = _draft(instructor)
        member10 = DiscountCodeFactory(class_offering=None, code="MEMBER10")
        all5 = DiscountCodeFactory(class_offering=None, code="ALL5")
        DiscountCodeFactory(class_offering=None, code="OLDCODE", is_active=False)
        DiscountCodeFactory(class_offering=None, code="NOTYET", is_approved=False)
        DiscountCodeFactory(class_offering=mine, code="EARLYBIRD")
        request = DiscountCodeRequestFactory(class_offering=mine, code="SPRING15")
        DiscountCodeRequestFactory(class_offering=ClassOfferingFactory(), code="ELSEWHERE")

        context = _composer_discounts_context(mine, False)

        assert list(context["composer_global_codes"]) == [all5, member10]
        assert list(context["composer_discount_requests"]) == [request]

    def it_lists_no_requests_for_an_unsaved_class():
        _flags(master=True, approval=True)
        DiscountCodeRequestFactory(code="SPRING15")
        context = _composer_discounts_context(None, False)
        assert list(context["composer_discount_requests"]) == []
        assert context["composer_discount_requests"].model is DiscountCodeRequest
        assert list(context["composer_global_codes"]) == []
