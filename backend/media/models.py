from __future__ import annotations

from typing import Any
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, RegexValidator
from django.db import models
from django.utils import timezone


class Asset(models.Model):
    asset_id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    source_key = models.CharField(max_length=512, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("created_at", "asset_id")


class MediaVariant(models.Model):
    class Format(models.TextChoices):
        JPEG = "jpeg", "JPEG"
        PNG = "png", "PNG"

    class Role(models.TextChoices):
        COMPRESSED = "compressed", "Compressed"
        HIGH_RESOLUTION = "high_resolution", "High resolution"

    class CoordinateMapping(models.TextChoices):
        NORMALIZED_IDENTITY = "normalized_identity", "Normalized identity"

    IMMUTABLE_AFTER_PUBLICATION = (
        "asset_id",
        "content_crc64ecma",
        "content_length",
        "content_sha256",
        "coordinate_mapping",
        "format",
        "height",
        "object_version",
        "published_at",
        "role",
        "source_key",
        "width",
    )

    media_variant_id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    asset = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name="media_variants")
    source_key = models.CharField(max_length=512, unique=True)
    content_sha256 = models.CharField(
        max_length=64,
        validators=[
            RegexValidator(
                regex=r"^[0-9a-f]{64}$",
                message="content_sha256 must be a lowercase SHA-256 hex digest.",
            )
        ],
    )
    object_version = models.CharField(max_length=256)
    content_length = models.PositiveBigIntegerField(validators=[MinValueValidator(1)])
    content_crc64ecma = models.CharField(
        max_length=20,
        validators=[RegexValidator(regex=r"^[0-9]{1,20}$")],
    )
    width = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    height = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    format = models.CharField(max_length=16, choices=Format.choices)
    role = models.CharField(max_length=32, choices=Role.choices)
    coordinate_mapping = models.CharField(
        max_length=32,
        choices=CoordinateMapping.choices,
    )
    published_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(content_length__gt=0, width__gt=0, height__gt=0),
                name="media_variant_positive_dimensions",
            ),
        ]
        ordering = ("created_at", "media_variant_id")

    def clean(self) -> None:
        super().clean()
        self._validate_normalized_identity_mapping()
        if self._state.adding:
            return

        original = (
            type(self).objects.filter(pk=self.pk).values(*self.IMMUTABLE_AFTER_PUBLICATION).first()
        )
        if original is None or original["published_at"] is None:
            return
        if any(
            getattr(self, field) != original[field] for field in self.IMMUTABLE_AFTER_PUBLICATION
        ):
            raise ValidationError("Published media variants are immutable.")

    def _validate_normalized_identity_mapping(self) -> None:
        if self.asset_id is None:
            return

        reference_variant = (
            type(self)
            .objects.filter(asset_id=self.asset_id)
            .exclude(pk=self.pk)
            .order_by("created_at", "media_variant_id")
            .first()
        )
        if reference_variant is None:
            return
        if self.width * reference_variant.height != self.height * reference_variant.width:
            raise ValidationError(
                {
                    "coordinate_mapping": (
                        "Normalized identity media variants must retain proportional dimensions."
                    )
                }
            )

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.full_clean()
        super().save(*args, **kwargs)

    def publish(self) -> None:
        if self._state.adding:
            raise ValidationError("Media variants must be saved before publication.")
        self.published_at = timezone.now()
        self.save(update_fields=["published_at"])


class MediaObjectRegistration(models.Model):
    source_key = models.CharField(max_length=512, unique=True)
    version_id = models.CharField(max_length=256)
    content_sha256 = models.CharField(
        max_length=64,
        validators=[RegexValidator(regex=r"^[0-9a-f]{64}$")],
    )
    content_length = models.PositiveBigIntegerField(validators=[MinValueValidator(1)])
    crc64ecma = models.CharField(
        max_length=20,
        validators=[RegexValidator(regex=r"^[0-9]{1,20}$")],
    )
    width = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    height = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    format = models.CharField(max_length=16, choices=MediaVariant.Format.choices)
    manifest_sha256 = models.CharField(
        max_length=64,
        validators=[RegexValidator(regex=r"^[0-9a-f]{64}$")],
    )
    registered_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(content_length__gt=0, width__gt=0, height__gt=0),
                name="media_registration_positive_values",
            )
        ]
        ordering = ("source_key",)

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise ValidationError("Registered COS object metadata is immutable.")
        self.full_clean()
        super().save(*args, **kwargs)


class MediaImportPreview(models.Model):
    preview_id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    plan = models.JSONField()
    plan_sha256 = models.CharField(
        max_length=64,
        validators=[RegexValidator(regex=r"^[0-9a-f]{64}$")],
    )
    expires_at = models.DateTimeField()
    cancelled_at = models.DateTimeField(blank=True, null=True)
    published_at = models.DateTimeField(blank=True, null=True)
    published_asset = models.ForeignKey(
        Asset,
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    publication_created_asset = models.BooleanField(blank=True, null=True)
    publication_created_media_variant_ids = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(
                        publication_created_asset__isnull=True,
                        published_asset__isnull=True,
                        published_at__isnull=True,
                    )
                    | models.Q(
                        publication_created_asset__isnull=False,
                        published_asset__isnull=False,
                        published_at__isnull=False,
                    )
                ),
                name="media_preview_publication_fields_coherent",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(cancelled_at__isnull=True) | models.Q(published_at__isnull=True)
                ),
                name="media_preview_not_cancelled_and_published",
            ),
        ]
        ordering = ("created_at", "preview_id")
