"""BDD-style tests for guild content models."""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from membership.models import GuildAnnouncement, GuildFAQItem, GuildImage, GuildLink
from tests.membership.factories import GuildFactory

pytestmark = pytest.mark.django_db

_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def describe_GuildImage():
    def it_orders_by_sort_order_then_created():
        guild = GuildFactory()
        b = GuildImage.objects.create(guild=guild, image=SimpleUploadedFile("b.png", _PNG), sort_order=2)
        a = GuildImage.objects.create(guild=guild, image=SimpleUploadedFile("a.png", _PNG), sort_order=1)
        assert list(guild.gallery_images.all()) == [a, b]


def describe_add_gallery_images():
    def it_creates_rows_with_incrementing_sort_order():
        guild = GuildFactory()
        guild.add_gallery_images(
            [
                SimpleUploadedFile("1.png", _PNG),
                SimpleUploadedFile("2.png", _PNG),
            ]
        )
        assert guild.gallery_images.count() == 2
        assert [g.sort_order for g in guild.gallery_images.all()] == [0, 1]


def describe_GuildFAQItem():
    def it_orders_by_sort_order():
        guild = GuildFactory()
        q2 = GuildFAQItem.objects.create(guild=guild, question="Second?", answer="A", sort_order=2)
        q1 = GuildFAQItem.objects.create(guild=guild, question="First?", answer="A", sort_order=1)
        assert list(guild.faq_items.all()) == [q1, q2]

    def describe_document():
        def it_has_no_document_when_both_blank():
            faq = GuildFAQItem.objects.create(guild=GuildFactory(), question="Q?", answer="A")
            assert faq.has_document is False
            assert faq.document_display_name == ""

        def it_reports_an_uploaded_file():
            faq = GuildFAQItem.objects.create(
                guild=GuildFactory(),
                question="Q?",
                answer="A",
                document=SimpleUploadedFile("agenda.pdf", b"%PDF-1.4"),
            )
            assert faq.has_document is True
            assert faq.document_display_name.endswith(".pdf")
            assert faq.document_href == faq.document.url

        def it_reports_an_external_link():
            faq = GuildFAQItem.objects.create(
                guild=GuildFactory(), question="Q?", answer="A", document_url="https://docs.example/x"
            )
            assert faq.has_document is True
            assert faq.document_display_name == "https://docs.example/x"
            assert faq.document_href == "https://docs.example/x"

        def it_rejects_both_a_file_and_a_link():
            from django.db import IntegrityError, transaction

            with pytest.raises(IntegrityError), transaction.atomic():
                GuildFAQItem.objects.create(
                    guild=GuildFactory(),
                    question="Q?",
                    answer="A",
                    document=SimpleUploadedFile("a.pdf", b"%PDF-1.4"),
                    document_url="https://docs.example/x",
                )


def describe_GuildLink():
    def it_orders_by_sort_order():
        guild = GuildFactory()
        l2 = GuildLink.objects.create(guild=guild, label="Wiki", url="https://w.example", sort_order=2)
        l1 = GuildLink.objects.create(guild=guild, label="Discord", url="https://d.example", sort_order=1)
        assert list(guild.links.all()) == [l1, l2]


def describe_GuildAnnouncement():
    def it_orders_newest_first():
        guild = GuildFactory()
        a1 = GuildAnnouncement.objects.create(guild=guild, title="Old", body="b")
        a2 = GuildAnnouncement.objects.create(guild=guild, title="New", body="b")
        assert list(guild.announcements.all()) == [a2, a1]

    # NOTE: the publish()-notifies-members test is deferred until Plan 2's
    # core.notifications / core.models.Notification land (see DEFERRED.md).


def describe_guild_new_fields():
    def it_defaults_the_new_fields_blank():
        guild = GuildFactory()
        assert guild.youtube_url == ""
        assert guild.meeting_schedule == ""
        assert guild.contact_email == ""
        assert guild.show_members is False


def describe_str_methods():
    def it_renders_readable_strings():
        guild = GuildFactory(name="Painters")
        img = GuildImage.objects.create(guild=guild, image=SimpleUploadedFile("x.png", _PNG))
        assert str(img) == f"Image #{img.pk} for Painters"
        faq = GuildFAQItem.objects.create(guild=guild, question="Why?", answer="Because")
        assert str(faq) == "Why?"
        link = GuildLink.objects.create(guild=guild, label="Discord", url="https://d.example")
        assert str(link) == "Discord (Painters)"
        ann = GuildAnnouncement.objects.create(guild=guild, title="Hi", body="b")
        assert str(ann) == "Hi (Painters)"
