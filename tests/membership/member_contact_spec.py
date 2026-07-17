"""BDD specs for the MemberContact model — auto-linkify, ordering, and per-surface accessors."""

from __future__ import annotations

import pytest

from tests.membership.factories import MemberContactFactory, MemberFactory


@pytest.mark.django_db
def describe_MemberContact():
    def describe_as_link():
        def it_renders_an_email_as_a_mailto_link():
            contact = MemberContactFactory(value="hello@example.com")
            assert contact.as_link == '<a href="mailto:hello@example.com">hello@example.com</a>'

        def it_renders_an_https_url_as_an_external_link():
            contact = MemberContactFactory(value="https://example.com/me")
            assert contact.as_link == (
                '<a href="https://example.com/me" target="_blank" rel="noopener">https://example.com/me</a>'
            )

        def it_renders_an_http_url_as_an_external_link():
            contact = MemberContactFactory(value="http://example.com")
            assert (
                contact.as_link == '<a href="http://example.com" target="_blank" rel="noopener">http://example.com</a>'
            )

        def it_promotes_a_www_prefix_to_https():
            contact = MemberContactFactory(value="www.example.com")
            assert contact.as_link == (
                '<a href="https://www.example.com" target="_blank" rel="noopener">www.example.com</a>'
            )

        def it_renders_a_social_handle_as_escaped_plain_text():
            contact = MemberContactFactory(value="@makerjane")
            assert contact.as_link == "@makerjane"

        def it_escapes_html_in_plain_text_values():
            contact = MemberContactFactory(value="<b>x</b>")
            assert contact.as_link == "&lt;b&gt;x&lt;/b&gt;"

        def it_trims_surrounding_whitespace_before_linkifying():
            contact = MemberContactFactory(value="  hi@example.com  ")
            assert contact.as_link == '<a href="mailto:hi@example.com">hi@example.com</a>'

    def describe_ordering():
        def it_orders_by_sort_order_then_id():
            member = MemberFactory()
            last = MemberContactFactory(member=member, sort_order=2, label="Two")
            first = MemberContactFactory(member=member, sort_order=0, label="Zero")
            second = MemberContactFactory(member=member, sort_order=0, label="Zero-later")
            assert list(member.contacts.all()) == [first, second, last]

    def describe_str():
        def it_names_the_label_value_and_member():
            member = MemberFactory(full_legal_name="Mara Q")
            contact = MemberContactFactory(member=member, label="Website", value="https://m.example")
            assert str(contact) == "Website: https://m.example (Mara Q)"

    def describe_directory_contacts():
        def it_returns_only_contacts_flagged_for_the_directory():
            member = MemberFactory()
            shown = MemberContactFactory(member=member, show_in_directory=True)
            MemberContactFactory(member=member, show_in_directory=False)
            assert list(member.directory_contacts) == [shown]

    def describe_instructor_page_contacts():
        def it_returns_only_contacts_flagged_for_the_instructor_page():
            member = MemberFactory()
            shown = MemberContactFactory(member=member, show_on_instructor_page=True)
            MemberContactFactory(member=member, show_on_instructor_page=False)
            assert list(member.instructor_page_contacts) == [shown]
