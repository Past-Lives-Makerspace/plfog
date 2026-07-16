"""BDD specs for the admin Discount Codes tab."""

from __future__ import annotations

from django.urls import reverse


def describe_admin_discount_codes():
    def it_lists_codes(admin_user, client, db):
        from classes.factories import DiscountCodeFactory

        client.force_login(admin_user)
        DiscountCodeFactory(code="HOLIDAY20")
        response = client.get(reverse("classes:admin_discount_codes"))
        assert response.status_code == 200
        assert b"HOLIDAY20" in response.content

    def it_creates_percent_code(admin_user, client, db):
        client.force_login(admin_user)
        response = client.post(
            reverse("classes:admin_discount_code_create"),
            {
                "code": "SAVE20",
                "discount_pct": 20,
                "is_active": "on",
            },
        )
        assert response.status_code == 302
        from classes.models import DiscountCode

        assert DiscountCode.objects.filter(code="SAVE20").exists()

    def it_creates_code_scoped_to_a_class(admin_user, client, db):
        from classes.factories import ClassOfferingFactory
        from classes.models import ClassOffering, DiscountCode

        client.force_login(admin_user)
        offering = ClassOfferingFactory(status=ClassOffering.Status.DRAFT)
        response = client.post(
            reverse("classes:admin_discount_code_create") + f"?class={offering.pk}",
            {"code": "CLASSONLY", "discount_pct": 15, "is_active": "on"},
        )
        assert response.status_code == 302
        code = DiscountCode.objects.get(code="CLASSONLY")
        assert code.class_offering == offering
        # A class-scoped code returns to that class's Discount Codes tab (the Workspace).
        assert response.url == reverse("classes:admin_class_discount_codes", kwargs={"pk": offering.pk})

    def it_scopes_a_code_to_an_instructorless_class_without_error(admin_user, client, db):
        """Legacy-imported classes have no instructor; scoping a code to one must not 500."""
        from classes.factories import ClassOfferingFactory
        from classes.models import ClassOffering, DiscountCode

        client.force_login(admin_user)
        offering = ClassOfferingFactory(status=ClassOffering.Status.DRAFT, instructor=None)
        response = client.post(
            reverse("classes:admin_discount_code_create") + f"?class={offering.pk}",
            {"code": "NOINSTR", "discount_pct": 15, "is_active": "on"},
        )
        assert response.status_code == 302
        assert DiscountCode.objects.get(code="NOINSTR").class_offering == offering

    def it_ignores_invalid_class_param_gracefully(admin_user, client, db):
        client.force_login(admin_user)
        response = client.get(reverse("classes:admin_discount_code_create") + "?class=99999")
        assert response.status_code == 200

    def it_rejects_code_with_no_discount_value(admin_user, client, db):
        client.force_login(admin_user)
        response = client.post(
            reverse("classes:admin_discount_code_create"),
            {
                "code": "EMPTY",
                "is_active": "on",
            },
        )
        assert response.status_code == 200
        assert b"Set either a percent" in response.content

    def it_renders_the_edit_form_on_get(admin_user, client, db):
        from classes.factories import DiscountCodeFactory

        client.force_login(admin_user)
        code = DiscountCodeFactory(discount_pct=10)
        response = client.get(reverse("classes:admin_discount_code_edit", kwargs={"pk": code.pk}))
        assert response.status_code == 200

    def it_edits_a_code(admin_user, client, db):
        from classes.factories import DiscountCodeFactory

        client.force_login(admin_user)
        code = DiscountCodeFactory(discount_pct=10)
        response = client.post(
            reverse("classes:admin_discount_code_edit", kwargs={"pk": code.pk}),
            {"code": code.code, "discount_pct": 25, "is_active": "on"},
        )
        assert response.status_code == 302
        code.refresh_from_db()
        assert code.discount_pct == 25

    def it_deletes_a_code(admin_user, client, db):
        from classes.factories import DiscountCodeFactory
        from classes.models import DiscountCode

        client.force_login(admin_user)
        code = DiscountCodeFactory()
        response = client.post(reverse("classes:admin_discount_code_delete", kwargs={"pk": code.pk}))
        assert response.status_code == 302
        assert not DiscountCode.objects.filter(pk=code.pk).exists()

    def it_ignores_get_on_delete_and_redirects(admin_user, client, db):
        from classes.factories import DiscountCodeFactory
        from classes.models import DiscountCode

        client.force_login(admin_user)
        code = DiscountCodeFactory()
        response = client.get(reverse("classes:admin_discount_code_delete", kwargs={"pk": code.pk}))
        assert response.status_code == 302
        assert DiscountCode.objects.filter(pk=code.pk).exists()

    def describe_approve_toggle():
        def it_approves_an_unapproved_code(admin_user, client, db):
            from classes.factories import DiscountCodeFactory

            client.force_login(admin_user)
            code = DiscountCodeFactory(is_approved=False)
            response = client.post(reverse("classes:admin_discount_code_approve", kwargs={"pk": code.pk}))
            assert response.status_code == 302
            code.refresh_from_db()
            assert code.is_approved is True

        def it_unapproves_an_approved_code(admin_user, client, db):
            from classes.factories import DiscountCodeFactory

            client.force_login(admin_user)
            code = DiscountCodeFactory(is_approved=True)
            response = client.post(reverse("classes:admin_discount_code_approve", kwargs={"pk": code.pk}))
            assert response.status_code == 302
            code.refresh_from_db()
            assert code.is_approved is False

        def it_rejects_get_requests(admin_user, client, db):
            from classes.factories import DiscountCodeFactory

            client.force_login(admin_user)
            code = DiscountCodeFactory(is_approved=False)
            response = client.get(reverse("classes:admin_discount_code_approve", kwargs={"pk": code.pk}))
            assert response.status_code == 405

        def it_forbids_a_member_without_the_permission(member_user, client, db):
            from classes.factories import DiscountCodeFactory

            client.force_login(member_user)
            code = DiscountCodeFactory(is_approved=False)
            response = client.post(reverse("classes:admin_discount_code_approve", kwargs={"pk": code.pk}))
            assert response.status_code == 403
            code.refresh_from_db()
            assert code.is_approved is False

        def it_lets_a_self_approver_approve_their_own_code(member_user, client, db):
            from classes.factories import DiscountCodeFactory
            from membership.models import Member

            Member.objects.filter(user=member_user).update(can_self_approve_discounts=True)
            code = DiscountCodeFactory(is_approved=False, created_by=member_user)
            client.force_login(member_user)
            response = client.post(reverse("classes:admin_discount_code_approve", kwargs={"pk": code.pk}))
            assert response.status_code == 302
            code.refresh_from_db()
            assert code.is_approved is True

        def it_forbids_a_self_approver_on_someone_elses_code(member_user, admin_user, client, db):
            from classes.factories import DiscountCodeFactory
            from membership.models import Member

            Member.objects.filter(user=member_user).update(can_self_approve_discounts=True)
            code = DiscountCodeFactory(is_approved=False, created_by=admin_user)
            client.force_login(member_user)
            response = client.post(reverse("classes:admin_discount_code_approve", kwargs={"pk": code.pk}))
            assert response.status_code == 403
            code.refresh_from_db()
            assert code.is_approved is False

    def it_gates_behind_admin_role(member_user, client, db):
        client.force_login(member_user)
        response = client.get(reverse("classes:admin_discount_codes"))
        assert response.status_code == 403
