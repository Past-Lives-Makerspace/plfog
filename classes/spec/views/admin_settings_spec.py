"""BDD specs for the admin Settings tab."""

from __future__ import annotations

from django.urls import reverse


def describe_admin_settings():
    def it_shows_settings_form(admin_user, client, db):
        client.force_login(admin_user)
        response = client.get(reverse("classes:admin_settings"))
        assert response.status_code == 200
        assert b"Class Settings" in response.content

    def it_saves_settings(admin_user, client, db):
        client.force_login(admin_user)
        response = client.post(
            reverse("classes:admin_settings"),
            {
                "liability_waiver_text": "NEW LIABILITY TEXT",
                "model_release_waiver_text": "NEW MODEL RELEASE",
                "default_member_discount_pct": 15,
                "reminder_hours_before": 48,
                "confirmation_email_footer": "",
            },
        )
        assert response.status_code == 302
        from classes.models import ClassSettings

        settings_obj = ClassSettings.load()
        assert settings_obj.default_member_discount_pct == 15

    def it_gates_behind_admin_role(member_user, client, db):
        client.force_login(member_user)
        response = client.get(reverse("classes:admin_settings"))
        assert response.status_code == 403

    def describe_example_class():
        def it_offers_the_teach_page_example_picker(admin_user, client, db):
            client.force_login(admin_user)
            content = client.get(reverse("classes:admin_settings")).content.decode()
            assert 'name="example_class"' in content

        def it_saves_the_chosen_example(admin_user, client, db):
            from classes.factories import ClassOfferingFactory
            from classes.models import ClassOffering, ClassSettings

            offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)
            client.force_login(admin_user)
            response = client.post(
                reverse("classes:admin_settings"),
                {
                    "liability_waiver_text": "LIABILITY",
                    "model_release_waiver_text": "MODEL RELEASE",
                    "default_member_discount_pct": 10,
                    "reminder_hours_before": 24,
                    "example_class": offering.pk,
                    "confirmation_email_footer": "",
                },
            )
            assert response.status_code == 302
            assert ClassSettings.load().example_class_id == offering.pk

        def it_leaves_the_example_blank_when_none_is_chosen(admin_user, client, db):
            from classes.models import ClassSettings

            client.force_login(admin_user)
            client.post(
                reverse("classes:admin_settings"),
                {
                    "liability_waiver_text": "LIABILITY",
                    "model_release_waiver_text": "MODEL RELEASE",
                    "default_member_discount_pct": 10,
                    "reminder_hours_before": 24,
                    "example_class": "",
                    "confirmation_email_footer": "",
                },
            )
            assert ClassSettings.load().example_class_id is None
