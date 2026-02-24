from unittest.mock import MagicMock, patch

from allauth.socialaccount.models import SocialAccount, SocialApp, SocialToken
from django.contrib import admin
from django.contrib.sites.models import Site
from django.db import models
from unfold.admin import ModelAdmin as UnfoldModelAdmin

from plfog.auto_admin import (
    EXCLUDED_APPS,
    HIDDEN_MODELS,
    create_model_admin,
    get_list_display_fields,
    get_list_filter_fields,
    get_search_fields,
    is_model_registered,
    register_all_models,
    unregister_hidden_models,
)


def _make_model(name, fields, app_label="core", abstract=False):
    """Create an in-memory Django model for testing."""
    attrs = {"__module__": "tests.plfog.auto_admin_spec"}
    meta_attrs = {"app_label": app_label, "managed": False}
    if abstract:
        meta_attrs["abstract"] = True
    attrs["Meta"] = type("Meta", (), meta_attrs)
    for field_name, field in fields.items():
        attrs[field_name] = field
    return type(name, (models.Model,), attrs)


def describe_get_list_display_fields():
    def it_puts_pk_field_first():
        model = _make_model("PkFirst", {"title": models.CharField(max_length=100)})
        result = get_list_display_fields(model)
        assert result[0] == "id"

    def it_truncates_to_max_fields():
        fields = {f"f{i}": models.CharField(max_length=10) for i in range(10)}
        model = _make_model("Wide", fields)
        result = get_list_display_fields(model)
        assert len(result) <= 6

    def it_respects_custom_max_fields():
        fields = {f"f{i}": models.CharField(max_length=10) for i in range(10)}
        model = _make_model("WideCustom", fields)
        result = get_list_display_fields(model, max_fields=3)
        assert len(result) <= 3

    def it_falls_back_to_str_when_no_suitable_fields():
        model = _make_model("Empty", {})
        model._meta.get_fields = lambda: []
        result = get_list_display_fields(model)
        assert result == ("__str__",)

    def it_returns_tuple():
        model = _make_model("TupleCheck", {"name": models.CharField(max_length=50)})
        result = get_list_display_fields(model)
        assert isinstance(result, tuple)


def describe_get_list_display_fields_filtering():
    def it_excludes_non_concrete_fields():
        model = _make_model("ConcreteOnly", {"name": models.CharField(max_length=50)})
        fake_field = MagicMock()
        fake_field.concrete = False
        fake_field.name = "reverse_relation"
        original = model._meta.get_fields

        def patched_get_fields():
            return list(original()) + [fake_field]

        model._meta.get_fields = patched_get_fields
        result = get_list_display_fields(model)
        assert "reverse_relation" not in result
        assert set(result) == {"id", "name"}

    def it_continues_past_non_concrete_fields_to_collect_remaining():
        """Non-concrete field before multiple concrete fields must not stop iteration."""
        model = _make_model(
            "MultiAfterNonConcrete",
            {
                "title": models.CharField(max_length=100),
                "body": models.TextField(),
            },
        )
        non_concrete = MagicMock()
        non_concrete.concrete = False
        non_concrete.name = "reverse_rel"
        original = model._meta.get_fields

        def patched_get_fields():
            real_fields = list(original())
            # Insert non-concrete field before the concrete non-pk fields
            return [real_fields[0], non_concrete] + real_fields[1:]

        model._meta.get_fields = patched_get_fields
        result = get_list_display_fields(model)
        assert "reverse_rel" not in result
        assert "title" in result
        assert "body" in result

    def it_excludes_auto_created_fields():
        model = _make_model("NoAuto", {"visible": models.CharField(max_length=50)})
        fake_field = MagicMock()
        fake_field.concrete = True
        fake_field.primary_key = False
        fake_field.auto_created = True
        fake_field.name = "invisible"
        original = model._meta.get_fields

        def patched_get_fields():
            return list(original()) + [fake_field]

        model._meta.get_fields = patched_get_fields
        result = get_list_display_fields(model)
        assert "invisible" not in result

    def it_continues_past_auto_created_fields_to_collect_remaining():
        """Auto-created field before multiple concrete fields must not stop iteration."""
        model = _make_model(
            "MultiAfterAutoCreated",
            {
                "title": models.CharField(max_length=100),
                "body": models.TextField(),
            },
        )
        auto_field = MagicMock()
        auto_field.concrete = True
        auto_field.primary_key = False
        auto_field.auto_created = True
        auto_field.name = "auto_ptr"
        original = model._meta.get_fields

        def patched_get_fields():
            real_fields = list(original())
            # Insert auto-created field before the concrete non-pk fields
            return [real_fields[0], auto_field] + real_fields[1:]

        model._meta.get_fields = patched_get_fields
        result = get_list_display_fields(model)
        assert "auto_ptr" not in result
        assert "title" in result
        assert "body" in result


def describe_get_search_fields_inclusion():
    def it_includes_char_fields():
        model = _make_model("WithChar", {"name": models.CharField(max_length=100)})
        result = get_search_fields(model)
        assert "name" in result

    def it_includes_text_fields():
        model = _make_model("WithText", {"bio": models.TextField()})
        result = get_search_fields(model)
        assert "bio" in result

    def it_returns_tuple():
        model = _make_model("SearchTuple", {"name": models.CharField(max_length=50)})
        assert isinstance(get_search_fields(model), tuple)


def describe_get_search_fields_exclusion():
    def it_excludes_fields_with_choices():
        model = _make_model(
            "WithChoices",
            {"status": models.CharField(max_length=10, choices=[("a", "A"), ("b", "B")])},
        )
        result = get_search_fields(model)
        assert "status" not in result

    def it_excludes_non_concrete_fields():
        model = _make_model("SearchConcrete", {"name": models.CharField(max_length=50)})
        result = get_search_fields(model)
        for field_name in result:
            assert field_name in ("name",)

    def it_excludes_auto_created_fields():
        model = _make_model("SearchNoAuto", {"name": models.CharField(max_length=50)})
        fake_field = MagicMock(spec=models.CharField)
        fake_field.concrete = True
        fake_field.auto_created = True
        fake_field.name = "auto_char"
        fake_field.choices = None
        original = model._meta.get_fields

        def patched():
            return list(original()) + [fake_field]

        model._meta.get_fields = patched
        result = get_search_fields(model)
        assert "auto_char" not in result

    def it_excludes_non_text_fields():
        model = _make_model(
            "IntOnly",
            {"count": models.IntegerField(), "flag": models.BooleanField(default=False)},
        )
        result = get_search_fields(model)
        assert "count" not in result
        assert "flag" not in result

    def it_returns_empty_when_no_text_fields():
        model = _make_model("NoText", {"count": models.IntegerField()})
        assert get_search_fields(model) == ()


def describe_get_list_filter_fields_inclusion():
    def it_includes_fields_with_choices():
        model = _make_model(
            "FilterChoices",
            {"status": models.CharField(max_length=10, choices=[("a", "A")])},
        )
        result = get_list_filter_fields(model)
        assert "status" in result

    def it_includes_boolean_fields():
        model = _make_model("FilterBool", {"active": models.BooleanField(default=True)})
        result = get_list_filter_fields(model)
        assert "active" in result

    def it_includes_date_fields():
        model = _make_model("FilterDate", {"created": models.DateField(auto_now_add=True)})
        result = get_list_filter_fields(model)
        assert "created" in result

    def it_includes_datetime_fields():
        model = _make_model("FilterDateTime", {"updated": models.DateTimeField(auto_now=True)})
        result = get_list_filter_fields(model)
        assert "updated" in result

    def it_includes_foreign_key_fields():
        target = _make_model("Target", {"name": models.CharField(max_length=50)})
        model = _make_model(
            "FilterFK",
            {"related": models.ForeignKey(target, on_delete=models.CASCADE)},
        )
        result = get_list_filter_fields(model)
        assert "related" in result


def describe_get_list_filter_fields_exclusion():
    def it_excludes_pk_fields():
        model = _make_model("FilterPK", {"name": models.CharField(max_length=50)})
        result = get_list_filter_fields(model)
        assert "id" not in result

    def it_excludes_auto_created_fields():
        model = _make_model("FilterNoAuto", {"active": models.BooleanField(default=True)})
        fake_field = MagicMock(spec=models.BooleanField)
        fake_field.concrete = True
        fake_field.auto_created = True
        fake_field.primary_key = False
        fake_field.name = "auto_bool"
        fake_field.choices = None
        original = model._meta.get_fields

        def patched():
            return list(original()) + [fake_field]

        model._meta.get_fields = patched
        result = get_list_filter_fields(model)
        assert "auto_bool" not in result

    def it_excludes_plain_char_fields_without_choices():
        model = _make_model("FilterPlain", {"name": models.CharField(max_length=100)})
        result = get_list_filter_fields(model)
        assert "name" not in result

    def it_excludes_non_concrete_fields():
        model = _make_model("FilterConcrete", {"active": models.BooleanField(default=True)})
        result = get_list_filter_fields(model)
        for field_name in result:
            assert field_name in ("active",)

    def it_returns_tuple():
        model = _make_model("FilterTuple", {"active": models.BooleanField(default=True)})
        assert isinstance(get_list_filter_fields(model), tuple)

    def it_returns_empty_when_no_filterable_fields():
        model = _make_model("FilterEmpty", {"name": models.CharField(max_length=50)})
        assert get_list_filter_fields(model) == ()


def describe_create_model_admin():
    def it_returns_unfold_model_admin_subclass():
        model = _make_model("AdminTest", {"name": models.CharField(max_length=50)})
        admin_class = create_model_admin(model)
        assert issubclass(admin_class, UnfoldModelAdmin)

    def it_is_also_a_django_model_admin_subclass():
        model = _make_model("AdminTest2", {"name": models.CharField(max_length=50)})
        admin_class = create_model_admin(model)
        assert issubclass(admin_class, admin.ModelAdmin)

    def it_has_correct_class_name():
        model = _make_model("MyModel", {"name": models.CharField(max_length=50)})
        admin_class = create_model_admin(model)
        assert admin_class.__name__ == "MyModelAutoAdmin"

    def it_sets_list_display():
        model = _make_model("ListDisp", {"name": models.CharField(max_length=50)})
        admin_class = create_model_admin(model)
        assert hasattr(admin_class, "list_display")
        assert len(admin_class.list_display) > 0

    def it_sets_search_fields_when_text_fields_exist():
        model = _make_model("SearchAdmin", {"name": models.CharField(max_length=50)})
        admin_class = create_model_admin(model)
        assert hasattr(admin_class, "search_fields")
        assert "name" in admin_class.search_fields

    def it_omits_search_fields_when_no_text_fields():
        model = _make_model("NoSearchAdmin", {"count": models.IntegerField()})
        admin_class = create_model_admin(model)
        assert not hasattr(admin_class, "search_fields") or admin_class.search_fields == ()

    def it_sets_list_filter_when_filterable_fields_exist():
        model = _make_model("FilterAdmin", {"active": models.BooleanField(default=True)})
        admin_class = create_model_admin(model)
        assert hasattr(admin_class, "list_filter")
        assert "active" in admin_class.list_filter

    def it_omits_list_filter_when_no_filterable_fields():
        model = _make_model("NoFilterAdmin", {"name": models.CharField(max_length=50)})
        admin_class = create_model_admin(model)
        assert not hasattr(admin_class, "list_filter") or admin_class.list_filter == ()


def describe_is_model_registered():
    def it_returns_true_when_registered():
        model = _make_model("RegCheck", {"name": models.CharField(max_length=50)})
        admin_class = type("RegCheckAdmin", (admin.ModelAdmin,), {})
        admin.site.register(model, admin_class)
        try:
            assert is_model_registered(model) is True
        finally:
            admin.site.unregister(model)

    def it_returns_false_when_not_registered():
        model = _make_model("NotReg", {"name": models.CharField(max_length=50)})
        assert is_model_registered(model) is False


def describe_register_all_models_counting():
    def it_returns_registered_and_skipped_counts():
        mock_model = MagicMock()
        mock_model.__name__ = "Good"
        mock_model._meta.app_label = "myapp"
        mock_model._meta.abstract = False

        mock_config = MagicMock()
        mock_config.name = "myapp"

        with (
            patch("plfog.auto_admin.apps.get_models", return_value=[mock_model]),
            patch("plfog.auto_admin.apps.get_app_config", return_value=mock_config),
            patch("plfog.auto_admin.is_model_registered", return_value=False),
            patch("plfog.auto_admin.admin.site.register"),
        ):
            registered, skipped = register_all_models()
            assert registered == 1
            assert skipped == 0

    def it_handles_mixed_models():
        good_model = MagicMock()
        good_model.__name__ = "GoodModel"
        good_model._meta.app_label = "myapp"
        good_model._meta.abstract = False

        excluded_model = MagicMock()
        excluded_model.__name__ = "AuthModel"
        excluded_model._meta.app_label = "auth"
        excluded_model._meta.abstract = False

        registered_model = MagicMock()
        registered_model.__name__ = "AlreadyReg"
        registered_model._meta.app_label = "myapp"
        registered_model._meta.abstract = False

        def mock_get_config(label):
            config = MagicMock()
            if label == "auth":
                config.name = "django.contrib.auth"
            else:
                config.name = "myapp"
            return config

        def mock_is_registered(model):
            return model is registered_model

        with (
            patch(
                "plfog.auto_admin.apps.get_models",
                return_value=[good_model, excluded_model, registered_model],
            ),
            patch("plfog.auto_admin.apps.get_app_config", side_effect=mock_get_config),
            patch("plfog.auto_admin.is_model_registered", side_effect=mock_is_registered),
            patch("plfog.auto_admin.admin.site.register"),
            patch("plfog.auto_admin.create_model_admin", return_value=MagicMock()),
        ):
            registered, skipped = register_all_models()
            assert registered == 1
            assert skipped == 2


def describe_register_all_models_skipping():
    def it_skips_excluded_apps():
        mock_model = MagicMock()
        mock_model.__name__ = "AuthModel"
        mock_model._meta.app_label = "auth"
        mock_model._meta.abstract = False

        mock_config = MagicMock()
        mock_config.name = "django.contrib.auth"

        with (
            patch("plfog.auto_admin.apps.get_models", return_value=[mock_model]),
            patch("plfog.auto_admin.apps.get_app_config", return_value=mock_config),
            patch("plfog.auto_admin.is_model_registered", return_value=False),
            patch("plfog.auto_admin.admin.site.register") as mock_register,
        ):
            registered, skipped = register_all_models()
            assert registered == 0
            assert skipped == 1
            mock_register.assert_not_called()

    def it_skips_abstract_models():
        mock_model = MagicMock()
        mock_model.__name__ = "AbstractModel"
        mock_model._meta.app_label = "myapp"
        mock_model._meta.abstract = True

        mock_config = MagicMock()
        mock_config.name = "myapp"

        with (
            patch("plfog.auto_admin.apps.get_models", return_value=[mock_model]),
            patch("plfog.auto_admin.apps.get_app_config", return_value=mock_config),
            patch("plfog.auto_admin.is_model_registered", return_value=False),
            patch("plfog.auto_admin.admin.site.register") as mock_register,
        ):
            registered, skipped = register_all_models()
            assert registered == 0
            assert skipped == 1
            mock_register.assert_not_called()

    def it_skips_already_registered_models():
        mock_model = MagicMock()
        mock_model.__name__ = "Existing"
        mock_model._meta.app_label = "myapp"
        mock_model._meta.abstract = False

        mock_config = MagicMock()
        mock_config.name = "myapp"

        with (
            patch("plfog.auto_admin.apps.get_models", return_value=[mock_model]),
            patch("plfog.auto_admin.apps.get_app_config", return_value=mock_config),
            patch("plfog.auto_admin.is_model_registered", return_value=True),
            patch("plfog.auto_admin.admin.site.register") as mock_register,
        ):
            registered, skipped = register_all_models()
            assert registered == 0
            assert skipped == 1
            mock_register.assert_not_called()


def describe_register_all_models_registration():
    def it_registers_unregistered_models():
        mock_model = MagicMock()
        mock_model.__name__ = "Fresh"
        mock_model._meta.app_label = "myapp"
        mock_model._meta.abstract = False

        mock_config = MagicMock()
        mock_config.name = "myapp"

        with (
            patch("plfog.auto_admin.apps.get_models", return_value=[mock_model]),
            patch("plfog.auto_admin.apps.get_app_config", return_value=mock_config),
            patch("plfog.auto_admin.is_model_registered", return_value=False),
            patch("plfog.auto_admin.admin.site.register") as mock_register,
            patch("plfog.auto_admin.create_model_admin", return_value=MagicMock()) as mock_create,
        ):
            registered, skipped = register_all_models()
            assert registered == 1
            mock_register.assert_called_once_with(mock_model, mock_create.return_value)

    def it_skips_all_excluded_app_names():
        assert "django.contrib.admin" in EXCLUDED_APPS
        assert "django.contrib.auth" in EXCLUDED_APPS
        assert "django.contrib.contenttypes" in EXCLUDED_APPS
        assert "django.contrib.sessions" in EXCLUDED_APPS
        assert "django.contrib.messages" in EXCLUDED_APPS
        assert "django.contrib.staticfiles" in EXCLUDED_APPS
        assert "django.contrib.sites" in EXCLUDED_APPS
        assert "unfold" in EXCLUDED_APPS
        assert "unfold.contrib.forms" in EXCLUDED_APPS
        assert "allauth" in EXCLUDED_APPS
        assert "django_extensions" in EXCLUDED_APPS


def describe_hidden_models():
    def it_contains_site():
        assert Site in HIDDEN_MODELS

    def it_contains_social_app():
        assert SocialApp in HIDDEN_MODELS

    def it_contains_social_token():
        assert SocialToken in HIDDEN_MODELS

    def it_contains_social_account():
        assert SocialAccount in HIDDEN_MODELS

    def it_contains_exactly_the_four_expected_models():
        assert HIDDEN_MODELS == {Site, SocialApp, SocialToken, SocialAccount}


def describe_unregister_hidden_models():
    def it_unregisters_all_registered_hidden_models():
        with (
            patch("plfog.auto_admin.is_model_registered", return_value=True),
            patch("plfog.auto_admin.admin.site.unregister") as mock_unregister,
        ):
            unregister_hidden_models()
            assert mock_unregister.call_count == len(HIDDEN_MODELS)

    def it_skips_models_that_are_not_registered():
        with (
            patch("plfog.auto_admin.is_model_registered", return_value=False),
            patch("plfog.auto_admin.admin.site.unregister") as mock_unregister,
        ):
            unregister_hidden_models()
            mock_unregister.assert_not_called()

    def it_returns_count_of_unregistered_models():
        with (
            patch("plfog.auto_admin.is_model_registered", return_value=True),
            patch("plfog.auto_admin.admin.site.unregister"),
        ):
            count = unregister_hidden_models()
            assert count == len(HIDDEN_MODELS)

    def it_returns_zero_when_none_are_registered():
        with (
            patch("plfog.auto_admin.is_model_registered", return_value=False),
            patch("plfog.auto_admin.admin.site.unregister"),
        ):
            count = unregister_hidden_models()
            assert count == 0

    def it_returns_partial_count_when_some_are_registered():
        registered = {Site, SocialApp}

        def mock_is_registered(model):
            return model in registered

        with (
            patch("plfog.auto_admin.is_model_registered", side_effect=mock_is_registered),
            patch("plfog.auto_admin.admin.site.unregister"),
        ):
            count = unregister_hidden_models()
            assert count == 2

    def it_removes_models_from_admin_registry():
        admin_class = type("SiteAdmin", (admin.ModelAdmin,), {})
        admin.site.register(Site, admin_class)
        try:
            assert is_model_registered(Site)
            with patch("plfog.auto_admin.HIDDEN_MODELS", {Site}):
                unregister_hidden_models()
            assert not is_model_registered(Site)
        finally:
            if is_model_registered(Site):
                admin.site.unregister(Site)
