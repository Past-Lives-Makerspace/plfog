"""Specs for classes/management/commands/regroup_classes.py."""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command

from classes.factories import CategoryFactory, ClassOfferingFactory

pytestmark = pytest.mark.django_db


def describe_regroup_classes_command():
    def it_reports_offering_and_group_counts():
        category = CategoryFactory()
        ClassOfferingFactory(title="Forging with Glen", slug="forging-a", category=category)
        ClassOfferingFactory(title="Forging with Glen", slug="forging-b", category=category)
        ClassOfferingFactory(title="Solo Class", slug="solo", category=category)

        out = StringIO()
        call_command("regroup_classes", stdout=out)

        output = out.getvalue()
        assert "3 offering(s)" in output
        assert "2 catalog group(s)" in output
