import uuid

import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("work", "0012_prediction_import_preview")]

    operations = [
        migrations.CreateModel(
            name="AnalysisJob",
            fields=[
                (
                    "job_id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("kind", models.CharField(max_length=64)),
                ("rule_version", models.CharField(max_length=64)),
                ("input_sha256", models.CharField(max_length=64)),
                ("code_version", models.CharField(max_length=64)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("running", "Running"),
                            ("succeeded", "Succeeded"),
                            ("failed", "Failed"),
                        ],
                        default="pending",
                        max_length=16,
                    ),
                ),
                ("attempt_count", models.PositiveIntegerField(default=0)),
                ("available_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("locked_at", models.DateTimeField(blank=True, null=True)),
                ("locked_by", models.CharField(blank=True, max_length=128)),
                ("last_error_code", models.CharField(blank=True, max_length=128)),
                ("last_error_message", models.CharField(blank=True, max_length=500)),
                ("output_manifest", models.JSONField(default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "revision",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="analysis_jobs",
                        to="work.annotationrevision",
                    ),
                ),
            ],
            options={
                "ordering": ("available_at", "created_at", "job_id"),
                "indexes": [
                    models.Index(fields=["status", "available_at"], name="work_job_status_ready")
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("revision", "kind", "rule_version", "input_sha256"),
                        name="work_analysis_job_input_unique",
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="SubmissionAssessment",
            fields=[
                (
                    "assessment_id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("rule_version", models.CharField(max_length=64)),
                ("input_sha256", models.CharField(max_length=64)),
                ("structure_valid", models.BooleanField()),
                ("component_manifest", models.JSONField()),
                ("assessment_sha256", models.CharField(max_length=64)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "revision",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="submission_assessments",
                        to="work.annotationrevision",
                    ),
                ),
            ],
            options={
                "ordering": ("revision_id", "created_at", "assessment_id"),
                "constraints": [
                    models.UniqueConstraint(
                        fields=("revision", "rule_version", "input_sha256"),
                        name="work_submission_assessment_input_unique",
                    )
                ],
            },
        ),
    ]
