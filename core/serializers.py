"""REST serializers for the FOG API's ``/api/v1/feedback/`` (#1220)."""

from __future__ import annotations

from typing import ClassVar

from django.db.models.fields.files import FieldFile
from rest_framework import serializers

from core.models import FeedbackComment, FeedbackRequest


def _photo_urls(serializer: serializers.Serializer, images: list[FieldFile]) -> list[str]:
    """Absolute URLs for ``images``; storage that already gives absolute ones is left alone."""
    request = serializer.context["request"]
    return [request.build_absolute_uri(image.url) for image in images]


class FeedbackCommentSerializer(serializers.ModelSerializer):
    """One message on a request's thread, as the inbox shows it: who, when, what, photos."""

    author_name = serializers.CharField(read_only=True)
    from_staff = serializers.BooleanField(source="is_from_staff", read_only=True)
    photo_urls = serializers.SerializerMethodField()

    class Meta:
        model = FeedbackComment
        fields: ClassVar = [
            "id",
            "author",
            "author_name",
            "from_staff",
            "body",
            "created_at",
            "edited_at",
            "photo_urls",
        ]
        read_only_fields: ClassVar = fields

    def get_photo_urls(self, comment: FeedbackComment) -> list[str]:
        return _photo_urls(self, [photo.image for photo in comment.photos.all()])


class FeedbackRequestSerializer(serializers.ModelSerializer):
    """One feedback request with its photos and whole comment thread. Read only."""

    sender_name = serializers.CharField(source="sender_label", read_only=True)
    status_label = serializers.CharField(read_only=True)
    photo_urls = serializers.SerializerMethodField()
    comments = FeedbackCommentSerializer(many=True, read_only=True)

    class Meta:
        model = FeedbackRequest
        fields: ClassVar = [
            "id",
            "category",
            "status",
            "status_label",
            "subject",
            "message",
            "user",
            "sender_name",
            "created_at",
            "status_changed_at",
            "github_issue_url",
            "photo_urls",
            "comments",
        ]
        read_only_fields: ClassVar = fields

    def get_photo_urls(self, feedback_request: FeedbackRequest) -> list[str]:
        return _photo_urls(self, [photo.image for photo in feedback_request.photos.all()])


class FeedbackRequestUpdateSerializer(serializers.Serializer):
    """A PATCH body: any of status, comment and GitHub link. What is left out keeps its saved value.

    The rules on what may be saved live in :meth:`FeedbackRequest.update_refusal`; this only
    checks the shape of each value.
    """

    status = serializers.ChoiceField(choices=FeedbackRequest.Status.choices, required=False)
    comment = serializers.CharField(required=False, allow_blank=True, trim_whitespace=False)
    github_issue_url = serializers.URLField(required=False, allow_blank=True)


class FeedbackCommentCreateSerializer(serializers.Serializer):
    """A comment body posted to a request's thread. Text only: photos go through the inbox page."""

    body = serializers.CharField()
