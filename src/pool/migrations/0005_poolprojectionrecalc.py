from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("pool", "0004_poolbet_is_active_alter_poolbet_away_score_pred_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="PoolProjectionRecalc",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("PENDING", "Pending"),
                            ("PROCESSING", "Processing"),
                            ("FAILED", "Failed"),
                            ("IDLE", "Idle"),
                        ],
                        default="PENDING",
                        max_length=20,
                    ),
                ),
                ("requested_at", models.DateTimeField(auto_now=True)),
                ("last_started_at", models.DateTimeField(blank=True, null=True)),
                ("last_finished_at", models.DateTimeField(blank=True, null=True)),
                ("attempts", models.PositiveIntegerField(default=0)),
                ("last_error", models.TextField(blank=True)),
                (
                    "participant",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="projection_recalc",
                        to="pool.poolparticipant",
                    ),
                ),
            ],
            options={
                "ordering": ["-requested_at"],
            },
        ),
    ]
