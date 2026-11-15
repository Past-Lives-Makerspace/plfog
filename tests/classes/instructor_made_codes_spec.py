"""BDD specs for #1184: instructors make their own discount codes, live at once.

The New Code form holds a code to one class the instructor teaches or to all their classes
(never site wide, never another instructor's class, never Members only), saves it approved and
active, and tells the Discount Code Administrators in the 8 AM digest. Admins filter the list
by who made a code. The scope probes are crafted POSTs: the form's own choices are the guard.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from classes.factories import (
    ClassOfferingFactory,
    ClassSessionFactory,
    DiscountCodeFactory,
    InstructorFactory,
    UserFactory,
)
from classes.forms import ALL_MY_CLASSES, InstructorDiscountCodeForm, RegistrationForm
from classes.models import ClassOffering, ClassSettings, DiscountCode
from core.events.channels import digest_context
from core.events.scheduler import flush_digests
from core.models import EventDelivery, SiteConfiguration
from membership.models import AdminCapability, Member
from tests.core.events.digest_support import next_8am

pytestmark = pytest.mark.django_db

CREATE = "classes:teach_discount_code_create"
CODES = "classes:teach_discount_codes"
MADE = "discount_code.instructor_made"


@pytest.fixture(autouse=True)
def _direct_mode(db: None) -> None:
    """The setting production flips to after deploy: instructor codes on."""
    config = SiteConfiguration.load()
    config.instructor_discount_codes_enabled = True
    config.save(update_fields=["instructor_discount_codes_enabled"])
    mail.outbox = []


def _teacher(slug: str = "ana", name: str = "Ana Ruiz", preferred: str = "Ana") -> tuple[User, Member]:
    user = UserFactory(username=f"{slug}@example.com", email=f"{slug}@example.com")
    member = InstructorFactory(user=user, instructor_slug=slug, full_legal_name=name, preferred_name=preferred)
    return user, member


def _class(teacher: Member, title: str = "Intro to Forging", price_cents: int = 10000) -> ClassOffering:
    offering = ClassOfferingFactory(
        title=title, instructor=teacher, status=ClassOffering.Status.PUBLISHED, price_cents=price_cents, capacity=6
    )
    starts = timezone.now() + timedelta(days=7)
    ClassSessionFactory(class_offering=offering, starts_at=starts, ends_at=starts + timedelta(hours=2))
    return offering


def _discount_admin() -> User:
    user = UserFactory(username="lee@example.com", email="lee@example.com")
    Member.objects.get(user=user).admin_capabilities.create(capability=AdminCapability.Capability.DISCOUNT_APPROVER)
    return user


def _post(client: Client, data: dict[str, object], query: str = "") -> object:
    return client.post(reverse(CREATE) + query, {"discount_pct": "", "discount_fixed_cents": "", **data})


def _checkout(offering: ClassOffering, code: str) -> RegistrationForm:
    data = {
        "first_name": "Sam",
        "last_name": "Smith",
        "email": "sam@example.com",
        "discount_code": code,
        "liability_signature": "Sam Smith",
        "accepts_liability": "on",
    }
    return RegistrationForm(data=data, offering=offering, settings_obj=ClassSettings.load())


def describe_an_instructor_making_a_code():
    def it_saves_a_percent_code_on_their_class_approved_and_active_and_checkout_takes_it(client: Client) -> None:
        user, teacher = _teacher()
        offering = _class(teacher)
        client.force_login(user)
        resp = _post(client, {"code": "spring20", "applies_to": str(offering.pk), "discount_pct": "20"})
        assert resp.status_code == 302
        code = DiscountCode.objects.get(code="SPRING20")
        assert (code.is_approved, code.is_active) == (True, True)
        assert (code.class_offering_id, code.instructor_id, code.created_by_id) == (offering.pk, None, user.pk)
        assert not code.members_only
        form = _checkout(offering, "SPRING20")
        assert form.is_valid(), form.errors
        assert form.compute_final_price_cents() == 8000

    def it_says_the_code_is_live(client: Client) -> None:
        user, teacher = _teacher()
        offering = _class(teacher)
        client.force_login(user)
        resp = _post(client, {"code": "LIVE5", "applies_to": str(offering.pk), "discount_pct": "5"})
        page = client.get(resp.url).content.decode()
        assert "Code is live." in page

    def it_makes_a_code_for_all_their_classes(client: Client) -> None:
        user, teacher = _teacher()
        first, second = _class(teacher), _class(teacher, title="Forge Welding")
        client.force_login(user)
        _post(client, {"code": "ALLMINE", "applies_to": ALL_MY_CLASSES, "discount_pct": "15"})
        code = DiscountCode.objects.get(code="ALLMINE")
        assert (code.class_offering_id, code.instructor_id, code.is_personal) == (None, teacher.pk, False)
        assert {first.pk, second.pk} == {
            o.pk for o in (first, second) if DiscountCode.objects.honoured_on(o).filter(pk=code.pk).exists()
        }

    def it_saves_a_100_percent_code_and_checkout_charges_nothing(client: Client) -> None:
        user, teacher = _teacher()
        offering = _class(teacher)
        client.force_login(user)
        _post(client, {"code": "FREEBIE", "applies_to": str(offering.pk), "discount_pct": "100"})
        form = _checkout(offering, "FREEBIE")
        assert form.is_valid(), form.errors
        assert form.compute_final_price_cents() == 0

    def it_saves_a_large_dollar_code_and_checkout_keeps_its_existing_floor(client: Client) -> None:
        """No cap on the amount. Checkout is unchanged: a code never takes the price below $0, and
        a total between $0 and $0.50 is refused (Stripe's minimum charge, RegistrationForm.clean)."""
        user, teacher = _teacher()
        offering = _class(teacher, price_cents=10000)
        client.force_login(user)
        _post(client, {"code": "HUGE", "applies_to": str(offering.pk), "discount_fixed_cents": "5000.00"})
        _post(client, {"code": "ALMOST", "applies_to": str(offering.pk), "discount_fixed_cents": "99.75"})
        assert DiscountCode.objects.get(code="HUGE").discount_fixed_cents == 500000
        huge = _checkout(offering, "HUGE")
        assert huge.is_valid(), huge.errors
        assert huge.compute_final_price_cents() == 0
        almost = _checkout(offering, "ALMOST")
        assert not almost.is_valid()
        assert "less than $0.50" in str(almost.errors)

    def it_preselects_the_class_from_its_discounts_tab_and_goes_back_there(client: Client) -> None:
        user, teacher = _teacher()
        offering = _class(teacher)
        client.force_login(user)
        form = client.get(reverse(CREATE) + f"?class={offering.pk}").context["form"]
        assert form["applies_to"].initial == str(offering.pk)
        resp = _post(
            client, {"code": "TABCODE", "applies_to": str(offering.pk), "discount_pct": "10"}, f"?class={offering.pk}"
        )
        assert resp.url == reverse("classes:teach_class_discount_codes", kwargs={"pk": offering.pk})

    def it_goes_back_to_the_codes_page_without_a_class(client: Client) -> None:
        user, teacher = _teacher()
        offering = _class(teacher)
        client.force_login(user)
        resp = _post(client, {"code": "PAGECODE", "applies_to": str(offering.pk), "discount_pct": "10"})
        assert resp.url == reverse(CODES)


def describe_the_scope_probes():
    def it_refuses_a_post_with_no_class_and_no_self_scope(client: Client) -> None:
        user, teacher = _teacher()
        _class(teacher)
        client.force_login(user)
        for data in (
            {"code": "NOSCOPE", "discount_pct": "10"},
            {"code": "NOSCOPE", "discount_pct": "10", "applies_to": ""},
        ):
            resp = _post(client, data)
            assert resp.status_code == 200
            assert resp.context["form"].errors["applies_to"]
        assert not DiscountCode.objects.filter(code="NOSCOPE").exists()
        assert not DiscountCode.objects.everywhere().filter(created_by=user).exists()

    def it_refuses_another_instructors_class(client: Client) -> None:
        user, teacher = _teacher()
        _class(teacher)
        _other_user, other = _teacher("bo", "Bo Lane", "Bo")
        theirs = _class(other, title="Glass Fusing")
        client.force_login(user)
        resp = _post(client, {"code": "STEAL", "applies_to": str(theirs.pk), "discount_pct": "50"})
        assert resp.status_code == 200
        assert "Select a valid choice" in str(resp.context["form"].errors["applies_to"])
        # The old ?class= route is no back door either: the scope comes from Class alone.
        resp = _post(client, {"code": "STEAL", "class": str(theirs.pk), "discount_pct": "50"}, f"?class={theirs.pk}")
        assert resp.status_code == 200
        assert not DiscountCode.objects.filter(code="STEAL").exists()

    def it_ignores_a_posted_instructor_or_class_offering(client: Client) -> None:
        user, teacher = _teacher()
        offering = _class(teacher)
        _other_user, other = _teacher("bo", "Bo Lane", "Bo")
        theirs = _class(other, title="Glass Fusing")
        client.force_login(user)
        _post(
            client,
            {
                "code": "SNEAKY",
                "applies_to": str(offering.pk),
                "instructor": str(other.pk),
                "class_offering": str(theirs.pk),
                "discount_pct": "5",
            },
        )
        code = DiscountCode.objects.get(code="SNEAKY")
        assert (code.class_offering_id, code.instructor_id) == (offering.pk, None)

    def it_ignores_members_only(client: Client) -> None:
        user, teacher = _teacher()
        offering = _class(teacher)
        client.force_login(user)
        _post(client, {"code": "MEMBERSY", "applies_to": str(offering.pk), "discount_pct": "10", "members_only": "on"})
        assert DiscountCode.objects.get(code="MEMBERSY").members_only is False

    def it_ignores_a_posted_approval_or_off_switch(client: Client) -> None:
        user, teacher = _teacher()
        offering = _class(teacher)
        client.force_login(user)
        _post(
            client,
            {
                "code": "SWITCHY",
                "applies_to": str(offering.pk),
                "discount_pct": "10",
                "is_approved": "",
                "is_active": "",
            },
        )
        code = DiscountCode.objects.get(code="SWITCHY")
        assert (code.is_approved, code.is_active) == (True, True)

    def it_does_not_offer_a_cancelled_class(client: Client) -> None:
        user, teacher = _teacher()
        gone = _class(teacher, title="Old Class")
        gone.status = ClassOffering.Status.CANCELLED
        gone.save(update_fields=["status"])
        client.force_login(user)
        resp = _post(client, {"code": "GONE", "applies_to": str(gone.pk), "discount_pct": "10"})
        assert resp.status_code == 200
        assert not DiscountCode.objects.filter(code="GONE").exists()


def describe_the_form_rules():
    def _form(teacher: Member, **data: str) -> InstructorDiscountCodeForm:
        return InstructorDiscountCodeForm(
            {"applies_to": ALL_MY_CLASSES, "discount_pct": "", "discount_fixed_cents": "", **data},
            teaching_member=teacher,
        )

    def it_refuses_a_percent_outside_1_to_100() -> None:
        _user, teacher = _teacher()
        for pct in ("0", "101"):
            form = _form(teacher, code="PCT", discount_pct=pct)
            assert not form.is_valid()
            assert form.errors["discount_pct"] == ["Percent off must be from 1 to 100."]

    def it_refuses_both_a_percent_and_a_dollar_amount() -> None:
        _user, teacher = _teacher()
        form = _form(teacher, code="BOTH", discount_pct="10", discount_fixed_cents="5.00")
        assert not form.is_valid()
        assert form.non_field_errors() == ["Pick a percent or a dollar amount off, not both."]

    def it_refuses_neither() -> None:
        _user, teacher = _teacher()
        form = _form(teacher, code="NONE")
        assert not form.is_valid()
        assert form.non_field_errors()

    def it_refuses_a_taken_code_in_any_case() -> None:
        _user, teacher = _teacher()
        DiscountCodeFactory(code="TAKEN10")
        form = _form(teacher, code="taken10", discount_pct="10")
        assert not form.is_valid()
        assert form.errors["code"] == ["That code is already taken. Pick another."]

    def it_saves_in_one_step_only() -> None:
        _user, teacher = _teacher()
        form = _form(teacher, code="ONESTEP", discount_pct="10")
        assert form.is_valid(), form.errors
        with pytest.raises(ValueError, match="one step"):
            form.save(commit=False)


def describe_editing_an_instructors_code():
    def it_starts_on_where_the_code_works(client: Client) -> None:
        user, teacher = _teacher()
        offering = _class(teacher)
        _other_user, other = _teacher("bo", "Bo Lane", "Bo")
        on_class = DiscountCodeFactory(code="ONCLASS", class_offering=offering, created_by=user)
        on_me = DiscountCodeFactory(code="ONME", class_offering=None, instructor=teacher, created_by=user)
        everywhere = DiscountCodeFactory(code="OLDWIDE", class_offering=None, created_by=user)
        foreign = DiscountCodeFactory(code="FOREIGN", class_offering=_class(other, title="Glass"), created_by=user)
        client.force_login(user)

        def initial(code: DiscountCode) -> object:
            url = reverse("classes:teach_discount_code_edit", kwargs={"pk": code.pk})
            return client.get(url).context["form"]["applies_to"].initial

        assert initial(on_class) == str(offering.pk)
        assert initial(on_me) == ALL_MY_CLASSES
        assert initial(everywhere) is None
        assert initial(foreign) is None

    def it_moves_the_code_and_keeps_an_admins_off_switch(client: Client) -> None:
        user, teacher = _teacher()
        offering = _class(teacher)
        code = DiscountCodeFactory(code="MOVEME", class_offering=offering, created_by=user, is_active=False)
        client.force_login(user)
        resp = client.post(
            reverse("classes:teach_discount_code_edit", kwargs={"pk": code.pk}),
            {"code": "MOVEME", "applies_to": ALL_MY_CLASSES, "discount_pct": "30", "discount_fixed_cents": ""},
        )
        assert resp.status_code == 302
        code.refresh_from_db()
        assert (code.class_offering_id, code.instructor_id, code.discount_pct) == (None, teacher.pk, 30)
        assert code.is_active is False

    def _edit(client: Client, target: DiscountCode, **data: str) -> None:
        post = {"code": target.code, "discount_pct": "", "discount_fixed_cents": "", "max_uses": "", **data}
        resp = client.post(reverse("classes:teach_discount_code_edit", kwargs={"pk": target.pk}), post)
        assert resp.status_code == 302, resp.context["form"].errors if resp.context else resp

    def _lines() -> list[str]:
        return [row.title for row in EventDelivery.objects.filter(event_key=MADE, channel="digest").order_by("pk")]

    def it_tells_staff_when_the_discount_or_scope_changes(client: Client) -> None:
        _discount_admin()
        user, teacher = _teacher()
        offering = _class(teacher)
        code = DiscountCodeFactory(code="SPRING5", discount_pct=5, class_offering=offering, created_by=user)
        client.force_login(user)
        _edit(client, code, applies_to=ALL_MY_CLASSES, discount_pct="100")
        assert _lines() == ["Ana changed SPRING5: now 100% off, all their classes"]
        flush_digests(next_8am())
        assert "Ana changed SPRING5: now 100% off, all their classes" in mail.outbox[-1].body

    def it_tells_staff_when_the_uses_limit_changes(client: Client) -> None:
        _discount_admin()
        user, teacher = _teacher()
        offering = _class(teacher)
        code = DiscountCodeFactory(code="USES5", discount_pct=5, class_offering=offering, created_by=user, max_uses=10)
        client.force_login(user)
        _edit(client, code, applies_to=str(offering.pk), discount_pct="5", max_uses="")
        _edit(client, code, applies_to=str(offering.pk), discount_pct="5", max_uses="50")
        assert _lines() == [
            "Ana changed USES5: now 5% off, Intro to Forging, unlimited uses",
            "Ana changed USES5: now 5% off, Intro to Forging, 50 uses",
        ]

    def it_sends_nothing_when_only_the_dates_or_spelling_change(client: Client) -> None:
        _discount_admin()
        user, teacher = _teacher()
        offering = _class(teacher)
        code = DiscountCodeFactory(code="SAME5", discount_pct=5, class_offering=offering, created_by=user)
        client.force_login(user)
        _edit(client, code, code="SAME6", applies_to=str(offering.pk), discount_pct="5", valid_until="2027-01-31")
        assert _lines() == []


def _adopted_personal_code(teacher: Member, user: User) -> DiscountCode:
    """The instructor's personal code, made by them before it was adopted (ensure_personal_for), so they can edit it."""
    code = DiscountCode.objects.ensure_personal_for(teacher)
    DiscountCode.objects.filter(pk=code.pk).update(created_by=user, code="ANA10", discount_pct=10)
    code.refresh_from_db()
    return code


def describe_editing_a_personal_code():
    def it_moves_onto_one_class_and_stops_being_the_personal_code(client: Client) -> None:
        user, teacher = _teacher()
        offering = _class(teacher)
        adopted = _adopted_personal_code(teacher, user)
        client.force_login(user)
        resp = client.post(
            reverse("classes:teach_discount_code_edit", kwargs={"pk": adopted.pk}),
            {"code": "ANA10", "applies_to": str(offering.pk), "discount_pct": "10", "discount_fixed_cents": ""},
        )
        assert resp.status_code == 302
        adopted.refresh_from_db()
        assert (adopted.class_offering_id, adopted.instructor_id, adopted.is_personal) == (offering.pk, None, False)
        # Their next visit makes them a new personal code, as after a reassign.
        assert client.get(reverse(CODES)).status_code == 200
        assert DiscountCode.objects.personal_for(teacher) not in (None, adopted)

    def it_stays_the_personal_code_on_all_their_classes(client: Client) -> None:
        user, teacher = _teacher()
        adopted = _adopted_personal_code(teacher, user)
        client.force_login(user)
        client.post(
            reverse("classes:teach_discount_code_edit", kwargs={"pk": adopted.pk}),
            {"code": "ANA10", "applies_to": ALL_MY_CLASSES, "discount_pct": "12", "discount_fixed_cents": ""},
        )
        adopted.refresh_from_db()
        assert (adopted.is_personal, adopted.discount_pct) == (True, 12)


def describe_the_instructor_pages():
    def it_reads_make_a_code_any_time_and_marks_new_code_for_guidance(client: Client) -> None:
        user, teacher = _teacher()
        offering = _class(teacher)
        client.force_login(user)
        page = client.get(reverse(CODES)).content.decode()
        assert (
            '<p class="pl-instructor-codes__note" data-make-any-time>Make a code for your own classes any time.</p>'
            in page
        )
        assert "Admins manage them." not in page
        assert 'data-guidance-target="classes.new-code"' in page
        tab = client.get(reverse("classes:teach_class_discount_codes", kwargs={"pk": offering.pk})).content.decode()
        assert 'data-guidance-target="classes.new-code"' in tab

    def it_shows_no_approval_on_a_class_tab_and_edit_only_on_their_own(client: Client) -> None:
        user, teacher = _teacher()
        offering = _class(teacher)
        admin = UserFactory(username="admin.k@example.com")
        mine = DiscountCodeFactory(code="MINE1", class_offering=offering, created_by=user, is_approved=False)
        theirs = DiscountCodeFactory(code="ADMIN1", class_offering=offering, created_by=admin)
        client.force_login(user)
        tab = client.get(reverse("classes:teach_class_discount_codes", kwargs={"pk": offering.pk})).content.decode()
        assert "<th>Approved</th>" not in tab
        assert f"/classes/teach/discount-codes/{mine.pk}/approve/" not in tab
        assert "<td data-code-active>no</td>" in tab
        assert "<td data-code-active>yes</td>" in tab
        assert reverse("classes:teach_discount_code_edit", kwargs={"pk": mine.pk}) in tab
        assert reverse("classes:teach_discount_code_edit", kwargs={"pk": theirs.pk}) not in tab

    def it_keeps_the_approved_column_for_an_admin(client: Client) -> None:
        _user, teacher = _teacher()
        offering = _class(teacher)
        admin = UserFactory(username="root@example.com", is_superuser=True, is_staff=True)
        Member.objects.filter(user=admin).update(fog_role=Member.FogRole.ADMIN)
        DiscountCodeFactory(code="ADMINVIEW", class_offering=offering)
        client.force_login(admin)
        tab = client.get(reverse("classes:teach_class_discount_codes", kwargs={"pk": offering.pk})).content.decode()
        assert "<th>Approved</th>" in tab

    def it_renders_the_form_with_its_fields_in_order(client: Client) -> None:
        user, teacher = _teacher()
        offering = _class(teacher)
        client.force_login(user)
        page = client.get(reverse(CREATE) + f"?class={offering.pk}").content.decode()
        names = ["code", "applies_to", "discount_pct", "discount_fixed_cents", "valid_from", "valid_until", "max_uses"]
        positions = [page.index(f'name="{name}"') for name in names]
        assert positions == sorted(positions)
        assert 'name="members_only"' not in page
        assert 'name="description"' not in page
        assert reverse("classes:teach_class_discount_codes", kwargs={"pk": offering.pk}) in page


def describe_the_staff_digest():
    def it_carries_one_line_per_instructor_code(client: Client) -> None:
        admin = _discount_admin()
        user, teacher = _teacher()
        offering = _class(teacher)
        client.force_login(user)
        _post(client, {"code": "SPRING20", "applies_to": str(offering.pk), "discount_pct": "20"})
        _post(client, {"code": "MINE15", "applies_to": ALL_MY_CLASSES, "discount_fixed_cents": "15.00"})
        flush_digests(next_8am())
        sent = [m for m in mail.outbox if m.to == [admin.email]]
        assert len(sent) == 1
        assert sent[0].subject == "2 Discount Codes Made or Changed by Instructors"
        body = sent[0].body
        assert "Ana made SPRING20, 20% off, Intro to Forging" in body
        assert "Ana made MINE15, $15.00 off, all their classes" in body
        assert body.count("Ana made ") == 2

    def it_sends_nothing_when_no_instructor_made_a_code() -> None:
        admin = _discount_admin()
        DiscountCodeFactory(code="ADMINMADE", is_approved=True)
        flush_digests(next_8am())
        assert not [m for m in mail.outbox if m.to == [admin.email]]
        assert not EventDelivery.objects.filter(event_key=MADE).exists()

    def it_keeps_action_needed_when_a_request_shares_the_morning() -> None:
        rows = [
            EventDelivery(event_key=MADE, title="Ana made A1", url="/a", period="discount:1:made"),
            EventDelivery(event_key="class_change_requested", title="Change", url="/b", period="x:1"),
        ]
        assert digest_context(rows)["subject"] == "Action Needed: 1 Request Awaiting You, plus 1 Update"
        assert digest_context(rows[:1])["subject"] == "1 Discount Code Made or Changed by Instructors"


def describe_the_admin_made_by_filter():
    def _admin_client() -> Client:
        admin = UserFactory(username="root2@example.com", is_superuser=True, is_staff=True)
        Member.objects.filter(user=admin).update(fog_role=Member.FogRole.ADMIN)
        client = Client()
        client.force_login(admin)
        return client

    def it_lists_only_the_codes_one_person_made() -> None:
        ana_user, _ana = _teacher()
        bo_user, _bo = _teacher("bo", "Bo Lane", "Bo")
        DiscountCodeFactory(code="ANACODE", created_by=ana_user)
        DiscountCodeFactory(code="BOCODE", created_by=bo_user)
        DiscountCodeFactory(code="NOBODY", created_by=None)
        client = _admin_client()
        url = reverse("classes:admin_discount_codes")
        everyone = client.get(url).content.decode()
        assert all(code in everyone for code in ("ANACODE", "BOCODE", "NOBODY"))
        assert f'<option value="{ana_user.pk}">Ana</option>' in everyone
        filtered = client.get(url, {"made_by": ana_user.pk}).content.decode()
        assert "ANACODE" in filtered
        assert "BOCODE" not in filtered and "NOBODY" not in filtered
        assert f'<option value="{ana_user.pk}" selected>Ana</option>' in filtered
        assert f'<input type="hidden" name="made_by" value="{ana_user.pk}">' in filtered

    def it_lists_everything_for_a_made_by_that_is_not_a_number() -> None:
        ana_user, _ana = _teacher()
        DiscountCodeFactory(code="ANACODE", created_by=ana_user)
        DiscountCodeFactory(code="OTHER1")
        client = _admin_client()
        # "²" is a digit to str.isdigit but not a number int() takes: it once crashed the page.
        for raw in ("x", "\u00b2", "1\u00b2"):
            resp = client.get(reverse("classes:admin_discount_codes"), {"made_by": raw})
            assert resp.status_code == 200
            page = resp.content.decode()
            assert "ANACODE" in page and "OTHER1" in page
        assert client.get(reverse("classes:admin_discount_codes") + "?made_by=%C2%B2").status_code == 200

    def it_hides_the_filter_when_nobody_made_a_code() -> None:
        DiscountCodeFactory(code="ORPHAN", created_by=None)
        page = _admin_client().get(reverse("classes:admin_discount_codes")).content.decode()
        assert "data-made-by-filter" not in page
