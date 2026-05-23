"""Specs for classes/managers.py re-export."""

from __future__ import annotations


def describe_managers_reexport():
    def it_exports_class_offering_queryset():
        from classes.managers import ClassOfferingQuerySet

        assert ClassOfferingQuerySet is not None

    def it_is_the_same_class_as_the_one_on_the_model():
        from classes.managers import ClassOfferingQuerySet as from_managers
        from classes.models import ClassOfferingQuerySet as from_models

        assert from_managers is from_models
