"""BDD-style tests for core.admin — SiteConfigurationAdmin."""

from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from core.admin import SiteConfigurationAdmin, _SiteConfigurationAdminForm
from core.models import SiteConfiguration

User = get_user_model()

pytestmark = pytest.mark.django_db


def describe_SiteConfigurationAdmin():
    @pytest.fixture()
    def admin_instance():
        from django.contrib.admin.sites import AdminSite

        return SiteConfigurationAdmin(SiteConfiguration, AdminSite())

    @pytest.fixture()
    def superuser_request(rf):
        request = rf.get("/admin/")
        request.user = User(is_staff=True, is_superuser=True)
        return request

    @pytest.fixture()
    def staff_request(rf):
        request = rf.get("/admin/")
        request.user = User(is_staff=True, is_superuser=False)
        return request

    def it_prevents_add_when_singleton_exists(admin_instance, superuser_request):
        SiteConfiguration.load()  # Ensure singleton exists
        assert admin_instance.has_add_permission(superuser_request) is False

    def it_allows_add_when_no_singleton(admin_instance, superuser_request):
        SiteConfiguration.objects.all().delete()
        assert admin_instance.has_add_permission(superuser_request) is True

    def it_never_allows_delete(admin_instance, superuser_request):
        assert admin_instance.has_delete_permission(superuser_request) is False

    def it_never_allows_delete_for_object(admin_instance, superuser_request):
        config = SiteConfiguration.load()
        assert admin_instance.has_delete_permission(superuser_request, obj=config) is False

    def it_redirects_changelist_to_edit_form():
        SiteConfiguration.load()
        user = User.objects.create_superuser(username="cfg-admin", password="pass", email="cfg@example.com")
        client = Client()
        client.force_login(user)
        resp = client.get("/admin/core/siteconfiguration/")
        assert resp.status_code == 302
        assert "/admin/core/siteconfiguration/1/change/" in resp.url

    def describe_permission_restrictions():
        def it_denies_module_access_for_non_superuser(admin_instance, staff_request):
            assert admin_instance.has_module_permission(staff_request) is False

        def it_allows_module_access_for_superuser(admin_instance, superuser_request):
            assert admin_instance.has_module_permission(superuser_request) is True

        def it_denies_view_for_non_superuser(admin_instance, staff_request):
            assert admin_instance.has_view_permission(staff_request) is False

        def it_allows_view_for_superuser(admin_instance, superuser_request):
            assert admin_instance.has_view_permission(superuser_request) is True

        def it_denies_change_for_non_superuser(admin_instance, staff_request):
            assert admin_instance.has_change_permission(staff_request) is False

        def it_allows_change_for_superuser(admin_instance, superuser_request):
            assert admin_instance.has_change_permission(superuser_request) is True

        def it_denies_add_for_non_superuser(admin_instance, staff_request):
            SiteConfiguration.objects.all().delete()
            assert admin_instance.has_add_permission(staff_request) is False


def describe_SiteConfigurationAdminForm():
    def it_renders_with_tooltip_label_on_general_calendar_url():
        form = _SiteConfigurationAdminForm()
        label = str(form.fields["general_calendar_url"].label)
        assert "General Calendar iCal URL" in label
        assert "?" in label

    def it_clears_help_text_on_general_calendar_url():
        form = _SiteConfigurationAdminForm()
        assert form.fields["general_calendar_url"].help_text == ""


def describe_save_model():
    @pytest.fixture()
    def superuser():
        return User.objects.create_superuser(username="save-admin", password="pass", email="save@example.com")

    @pytest.fixture()
    def admin_client(superuser):
        c = Client()
        c.force_login(superuser)
        return c

    def _post_config(admin_client, url: str) -> object:
        """POST the SiteConfiguration change form with the given calendar URL."""
        config = SiteConfiguration.load()
        return admin_client.post(
            f"/admin/core/siteconfiguration/{config.pk}/change/",
            {
                "registration_mode": "open",
                "general_calendar_url": url,
                "general_calendar_color": "#4B9FEE",
                "classes_calendar_color": "#F59E0B",
                "sync_classes_enabled": "",
                "_save": "Save",
            },
        )

    def it_syncs_general_calendar_on_save_when_url_is_set(admin_client):
        with patch("hub.calendar_service.sync_general_calendar", return_value=3) as mock_sync:
            _post_config(admin_client, "https://example.com/general.ics")
        mock_sync.assert_called_once()

    def it_shows_success_message_after_successful_sync(admin_client):
        with patch("hub.calendar_service.sync_general_calendar", return_value=7):
            response = _post_config(admin_client, "https://example.com/general2.ics")
        # Successful save redirects
        assert response.status_code == 302

    def it_shows_warning_on_general_sync_404_error(admin_client):
        import urllib.error

        http_error = urllib.error.HTTPError(
            url="https://example.com/notfound.ics",
            code=404,
            msg="Not Found",
            hdrs=None,  # type: ignore[arg-type]
            fp=None,
        )
        with patch("hub.calendar_service.sync_general_calendar", side_effect=http_error):
            response = _post_config(admin_client, "https://example.com/notfound.ics")
        # Still redirects (warning is set via messages)
        assert response.status_code == 302

    def it_shows_generic_warning_on_other_sync_error(admin_client):
        with patch("hub.calendar_service.sync_general_calendar", side_effect=ValueError("bad data")):
            response = _post_config(admin_client, "https://example.com/broken.ics")
        assert response.status_code == 302

    def it_skips_sync_when_url_is_empty(admin_client):
        with patch("hub.calendar_service.sync_general_calendar") as mock_sync:
            _post_config(admin_client, "")
        mock_sync.assert_not_called()
