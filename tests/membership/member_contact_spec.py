"""BDD specs for the MemberContact model — auto-linkify, ordering, and per-surface accessors."""

from __future__ import annotations

import pytest

from membership.models import MemberContact
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

    def describe_kind():
        def it_defaults_to_other():
            member = MemberFactory()
            contact = MemberContact.objects.create(member=member, label="Signal", value="@quiet")
            assert contact.kind == MemberContact.Kind.OTHER

    def describe_social_icon():
        def it_maps_an_instagram_label_to_the_instagram_icon():
            assert MemberContactFactory(label="Instagram").social_icon == "instagram"

        def it_maps_a_youtube_label_to_the_youtube_icon():
            assert MemberContactFactory(label="My YouTube channel").social_icon == "youtube"

        def it_maps_a_facebook_label_to_the_facebook_icon():
            assert MemberContactFactory(label="Facebook").social_icon == "facebook"

        def it_maps_a_tiktok_label_to_the_tiktok_icon():
            assert MemberContactFactory(label="TikTok").social_icon == "tiktok"

        def it_maps_a_linkedin_label_to_the_linkedin_icon():
            assert MemberContactFactory(label="LinkedIn profile").social_icon == "linkedin"

        def it_maps_a_twitter_label_to_the_x_icon():
            assert MemberContactFactory(label="Twitter").social_icon == "x"

        def it_falls_back_to_the_link_icon_for_an_unrecognized_label():
            assert MemberContactFactory(label="Mastodon").social_icon == "link"
