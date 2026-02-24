"""
Auto-register all project models in Django admin with sensible defaults.

Import and call register_all_models() in your admin.py or AppConfig.ready():
    from plfog.auto_admin import register_all_models
    register_all_models()
"""

from allauth.socialaccount.models import SocialAccount, SocialApp, SocialToken
from django.apps import apps
from django.contrib import admin
from django.contrib.sites.models import Site
from django.db import models
from unfold.admin import ModelAdmin as UnfoldModelAdmin

EXCLUDED_APPS = {
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sites",
    "unfold",
    "unfold.contrib.forms",
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "allauth.socialaccount.providers.google",
    "allauth.socialaccount.providers.github",
    "allauth.socialaccount.providers.discord",
    "django_extensions",
}


HIDDEN_MODELS = {Site, SocialApp, SocialToken, SocialAccount}


def unregister_hidden_models():
    """Unregister third-party models we don't want visible in admin."""
    count = 0
    for model in HIDDEN_MODELS:
        if is_model_registered(model):
            admin.site.unregister(model)
            count += 1
    return count


def get_list_display_fields(model, max_fields=6):
    fields = []
    pk_field = None
    for field in model._meta.get_fields():
        if not field.concrete:
            continue
        if field.primary_key:
            pk_field = field.name
            continue
        if getattr(field, "auto_created", False):
            continue
        fields.append(field.name)
    result = []
    if pk_field:
        result.append(pk_field)
    result.extend(fields[: max_fields - len(result)])
    return tuple(result) if result else ("__str__",)


def get_search_fields(model):
    search_fields = []
    for field in model._meta.get_fields():
        if not field.concrete or getattr(field, "auto_created", False):
            continue
        if isinstance(field, (models.CharField, models.TextField)):
            if not getattr(field, "choices", None):
                search_fields.append(field.name)
    return tuple(search_fields)


def get_list_filter_fields(model):
    filter_fields = []
    for field in model._meta.get_fields():
        if not field.concrete or getattr(field, "auto_created", False) or field.primary_key:
            continue
        if getattr(field, "choices", None):
            filter_fields.append(field.name)
        elif isinstance(field, models.BooleanField):
            filter_fields.append(field.name)
        elif isinstance(field, (models.DateField, models.DateTimeField)):
            filter_fields.append(field.name)
        elif isinstance(field, models.ForeignKey):
            filter_fields.append(field.name)
    return tuple(filter_fields)


def create_model_admin(model):
    list_display = get_list_display_fields(model)
    search_fields = get_search_fields(model)
    list_filter = get_list_filter_fields(model)
    admin_attrs = {"list_display": list_display}
    if search_fields:
        admin_attrs["search_fields"] = search_fields
    if list_filter:
        admin_attrs["list_filter"] = list_filter
    return type(f"{model.__name__}AutoAdmin", (UnfoldModelAdmin,), admin_attrs)


def is_model_registered(model):
    return model in admin.site._registry


def register_all_models():
    registered_count = 0
    skipped_count = 0
    for model in apps.get_models():
        app_config = apps.get_app_config(model._meta.app_label)
        if app_config.name in EXCLUDED_APPS or model._meta.abstract or is_model_registered(model):
            skipped_count += 1
            continue
        admin.site.register(model, create_model_admin(model))
        registered_count += 1
    return registered_count, skipped_count
