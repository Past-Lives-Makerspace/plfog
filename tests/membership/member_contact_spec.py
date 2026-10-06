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

        def it_renders_a_handle_under_an_unrecognized_label_as_plain_text():
            contact = MemberContactFactory(value="@makerjane")
            assert contact.as_link == "@makerjane"

        def it_renders_free_text_as_plain_text():
            contact = MemberContactFactory(label="Hours", value="Open Tue 5pm, studio 4B")
            assert contact.as_link == "Open Tue 5pm, studio 4B"

        def describe_a_platform_handle():
            @pytest.mark.parametrize(
                ("label", "value", "href"),
                [
                    ("Instagram", "@Dixie_Junius_Art", "https://instagram.com/Dixie_Junius_Art"),
                    ("Instagram", "threadfox", "https://instagram.com/threadfox"),
                    ("IG", "@threadfox", "https://instagram.com/threadfox"),
                    ("Instagrm", "@threadfox", "https://instagram.com/threadfox"),
                    ("Instagtram", "@threadfox", "https://instagram.com/threadfox"),
                    ("My IG", "jane.makes", "https://instagram.com/jane.makes"),
                    ("TikTok", "@jane", "https://www.tiktok.com/@jane"),
                    ("Twitter", "@jane", "https://x.com/jane"),
                    ("X", "jane", "https://x.com/jane"),
                    ("YouTube", "@jane", "https://www.youtube.com/@jane"),
                    ("Facebook", "jane.makes", "https://facebook.com/jane.makes"),
                    ("Threads", "@jane", "https://www.threads.net/@jane"),
                ],
            )
            def it_links_to_the_profile_and_shows_what_was_typed(label: str, value: str, href: str):
                contact = MemberContactFactory.build(label=label, value=value)
                assert contact.as_link == f'<a href="{href}" target="_blank" rel="noopener">{value}</a>'

            def it_links_whatever_kind_the_contact_is_saved_as():
                for kind in MemberContact.Kind:
                    contact = MemberContactFactory.build(label="Instagram", value="@jane", kind=kind)
                    assert 'href="https://instagram.com/jane"' in contact.as_link

            @pytest.mark.parametrize(
                "label", ["Installation art", "Instant replies", "Threads and yarn", "My threads", "Retwitter"]
            )
            def it_matches_platform_words_only_at_a_word_start(label: str):
                contact = MemberContactFactory.build(label=label, value="@jane")
                assert contact.as_link == "@jane"

            @pytest.mark.parametrize("label", ["Insta", "My Instagram", "instagram"])
            def it_matches_instagram_at_any_word_start_or_insta_alone(label: str):
                contact = MemberContactFactory.build(label=label, value="@jane")
                assert 'href="https://instagram.com/jane"' in contact.as_link

            def it_drops_one_trailing_dot_from_the_url_but_not_the_text():
                contact = MemberContactFactory.build(label="Instagram", value="@jane.makes.")
                assert contact.as_link == (
                    '<a href="https://instagram.com/jane.makes" target="_blank" rel="noopener">@jane.makes.</a>'
                )

            def it_leaves_a_handle_ending_in_two_dots_as_plain_text():
                contact = MemberContactFactory.build(label="Instagram", value="@jane..")
                assert contact.as_link == "@jane.."

            @pytest.mark.parametrize("label", ["Exhibits", "Signal", "Big", "Xbox", "Max"])
            def it_matches_ig_and_x_only_as_whole_words(label: str):
                contact = MemberContactFactory.build(label=label, value="@jane")
                assert contact.as_link == "@jane"

            def it_leaves_a_mastodon_address_as_plain_text():
                contact = MemberContactFactory.build(label="Mastodon", value="@me@server.social")
                assert contact.as_link == "@me@server.social"

            def it_leaves_a_value_that_is_not_a_handle_as_plain_text():
                contact = MemberContactFactory.build(label="Instagram", value="ask me for it")
                assert contact.as_link == "ask me for it"

            def it_makes_no_link_for_a_linkedin_handle():
                contact = MemberContactFactory.build(label="LinkedIn", value="jane")
                assert contact.as_link == "jane"

        def describe_a_platform_prefix_in_the_value():
            def it_links_the_handle_after_the_prefix_under_any_label():
                contact = MemberContactFactory.build(label="Other", value="Instagram: @name")
                assert contact.as_link == (
                    '<a href="https://instagram.com/name" target="_blank" rel="noopener">Instagram: @name</a>'
                )

            def it_reads_the_prefix_before_the_label():
                contact = MemberContactFactory.build(label="Instagram", value="TikTok: @name")
                assert 'href="https://www.tiktok.com/@name"' in contact.as_link

            @pytest.mark.parametrize(
                ("value", "href"),
                [
                    ("IG @jholtmanart", "https://instagram.com/jholtmanart"),
                    ("Instagram @x", "https://instagram.com/x"),
                    ("Threads: @jane", "https://www.threads.net/@jane"),
                ],
            )
            def it_links_a_platform_word_then_an_at_handle_without_a_colon(value: str, href: str):
                contact = MemberContactFactory.build(label="Other", value=value)
                assert contact.as_link == f'<a href="{href}" target="_blank" rel="noopener">{value}</a>'

            @pytest.mark.parametrize(
                "value", ["Instagram is where I post", "Instagram jane", "Threads and yarn: @jane"]
            )
            def it_leaves_free_text_after_a_platform_word_plain(value: str):
                contact = MemberContactFactory.build(label="Other", value=value)
                assert contact.as_link == value

            def it_falls_back_to_the_label_when_the_prefix_names_no_platform():
                contact = MemberContactFactory.build(label="Instagram", value="Booking: name")
                assert contact.as_link == "Booking: name"

        def describe_a_bare_domain():
            def it_links_over_https():
                contact = MemberContactFactory.build(label="Website", value="dixiejunius.com")
                assert contact.as_link == (
                    '<a href="https://dixiejunius.com" target="_blank" rel="noopener">dixiejunius.com</a>'
                )

            def it_keeps_a_path():
                contact = MemberContactFactory.build(label="Shop", value="ambercapwell.com/shop")
                assert 'href="https://ambercapwell.com/shop"' in contact.as_link

            def it_drops_one_trailing_dot_from_the_url_but_not_the_text():
                contact = MemberContactFactory.build(label="Website", value="mysite.com.")
                assert contact.as_link == (
                    '<a href="https://mysite.com" target="_blank" rel="noopener">mysite.com.</a>'
                )

            def it_drops_a_trailing_dot_after_a_path():
                contact = MemberContactFactory.build(label="Shop", value="mysite.com/shop.")
                assert 'href="https://mysite.com/shop"' in contact.as_link

            def it_leaves_a_dotless_word_as_plain_text():
                contact = MemberContactFactory.build(label="Website", value="coming soon")
                assert contact.as_link == "coming soon"

        def describe_a_phone_number():
            def it_becomes_a_tel_link_showing_what_was_typed():
                contact = MemberContactFactory.build(label="Office phone", value="(503) 555 0199")
                assert contact.as_link == '<a href="tel:5035550199">(503) 555 0199</a>'

            def it_carries_an_extension():
                contact = MemberContactFactory.build(label="Office phone", value="+1 503.555.0199 ext. 12")
                assert contact.as_link == '<a href="tel:+15035550199;ext=12">+1 503.555.0199 ext. 12</a>'

        def describe_escaping():
            def it_escapes_html_in_a_handle_prefix_value():
                contact = MemberContactFactory.build(label="Other", value='Instagram: <script>"x"</script>')
                assert contact.as_link == "Instagram: &lt;script&gt;&quot;x&quot;&lt;/script&gt;"

            def it_escapes_html_under_a_platform_label():
                contact = MemberContactFactory.build(label="Instagram", value="<b>jane</b>")
                assert contact.as_link == "&lt;b&gt;jane&lt;/b&gt;"

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

        @pytest.mark.parametrize("label", ["IG", "Instagrm", "Instagtram", "insta"])
        def it_maps_instagram_misspellings_and_ig_to_the_instagram_icon(label: str):
            assert MemberContactFactory.build(label=label).social_icon == "instagram"

        def it_maps_a_standalone_x_label_to_the_x_icon():
            assert MemberContactFactory.build(label="X").social_icon == "x"

        def it_falls_back_to_the_link_icon_for_threads_which_has_no_glyph():
            assert MemberContactFactory.build(label="Threads").social_icon == "link"

        @pytest.mark.parametrize("label", ["Installation art", "Instant replies", "Threads and yarn"])
        def it_does_not_match_a_platform_word_inside_another_word_or_phrase(label: str):
            assert MemberContactFactory.build(label=label).social_icon == "link"

        @pytest.mark.parametrize("label", ["Exhibits", "Signal"])
        def it_does_not_match_ig_or_x_inside_a_word(label: str):
            assert MemberContactFactory.build(label=label).social_icon == "link"

        def it_falls_back_to_the_link_icon_for_an_unrecognized_label():
            assert MemberContactFactory(label="Mastodon").social_icon == "link"
