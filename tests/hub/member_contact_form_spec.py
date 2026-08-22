"""BDD specs for the MemberContact inline formset used on profile settings."""

from __future__ import annotations

import pytest

from hub.forms import MemberContactFormSet
from membership.models import MemberContact
from tests.membership.factories import MemberContactFactory, MemberFactory


@pytest.mark.django_db
def describe_member_contact_formset():
    def it_creates_a_contact_row():
        member = MemberFactory()
        formset = MemberContactFormSet(
            {
                "contacts-TOTAL_FORMS": "1",
                "contacts-INITIAL_FORMS": "0",
                "contacts-0-label": "Website",
                "contacts-0-value": "https://example.com",
                "contacts-0-show_in_directory": "on",
                "contacts-0-sort_order": "0",
                "contacts-0-kind": "website",
            },
            instance=member,
            prefix="contacts",
        )
        assert formset.is_valid(), formset.errors
        formset.save()

        contact = member.contacts.get()
        assert contact.label == "Website"
        assert contact.value == "https://example.com"
        assert contact.show_in_directory is True
        assert contact.show_on_instructor_page is False

    def it_requires_a_label_on_a_touched_row():
        member = MemberFactory()
        formset = MemberContactFormSet(
            {
                "contacts-TOTAL_FORMS": "1",
                "contacts-INITIAL_FORMS": "0",
                "contacts-0-label": "",
                "contacts-0-value": "https://example.com",
                "contacts-0-sort_order": "0",
            },
            instance=member,
            prefix="contacts",
        )
        assert not formset.is_valid()
        assert "label" in formset.forms[0].errors

    def it_ignores_a_wholly_blank_added_row():
        member = MemberFactory()
        formset = MemberContactFormSet(
            {
                "contacts-TOTAL_FORMS": "1",
                "contacts-INITIAL_FORMS": "0",
                "contacts-0-label": "",
                "contacts-0-value": "",
                # show_in_directory left at its default-checked state (matches initial), so the
                # untouched +Add row does not count as changed and never blocks the save.
                "contacts-0-show_in_directory": "on",
                "contacts-0-sort_order": "0",
            },
            instance=member,
            prefix="contacts",
        )
        assert formset.is_valid(), formset.errors
        formset.save()
        assert member.contacts.count() == 0

    def it_accepts_each_valid_kind():
        member = MemberFactory()
        for index, kind in enumerate(MemberContact.Kind):
            formset = MemberContactFormSet(
                {
                    "contacts-TOTAL_FORMS": "1",
                    "contacts-INITIAL_FORMS": "0",
                    "contacts-0-label": f"Row {kind}",
                    "contacts-0-value": f"https://example.com/{index}",
                    "contacts-0-show_in_directory": "on",
                    "contacts-0-sort_order": "0",
                    "contacts-0-kind": kind.value,
                },
                instance=member,
                prefix="contacts",
            )
            assert formset.is_valid(), formset.errors
            formset.save()
            assert member.contacts.get(label=f"Row {kind}").kind == kind

    def it_rejects_an_invalid_kind_value():
        member = MemberFactory()
        formset = MemberContactFormSet(
            {
                "contacts-TOTAL_FORMS": "1",
                "contacts-INITIAL_FORMS": "0",
                "contacts-0-label": "Bad",
                "contacts-0-value": "https://example.com",
                "contacts-0-show_in_directory": "on",
                "contacts-0-sort_order": "0",
                "contacts-0-kind": "carrier-pigeon",
            },
            instance=member,
            prefix="contacts",
        )
        assert not formset.is_valid()
        assert "kind" in formset.forms[0].errors

    def it_deletes_a_saved_row_flagged_for_deletion():
        member = MemberFactory()
        contact = MemberContactFactory(member=member, label="Old", value="https://old.example")
        formset = MemberContactFormSet(
            {
                "contacts-TOTAL_FORMS": "1",
                "contacts-INITIAL_FORMS": "1",
                "contacts-0-id": str(contact.pk),
                "contacts-0-label": "Old",
                "contacts-0-value": "https://old.example",
                "contacts-0-show_in_directory": "on",
                "contacts-0-sort_order": "0",
                "contacts-0-DELETE": "on",
            },
            instance=member,
            prefix="contacts",
        )
        assert formset.is_valid(), formset.errors
        formset.save()
        assert member.contacts.count() == 0
