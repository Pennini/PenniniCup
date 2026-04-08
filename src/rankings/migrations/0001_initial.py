from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("pool", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="RankingTieBreakOverride",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("manual_position", models.PositiveIntegerField()),
                ("reason", models.CharField(blank=True, max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "participant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="ranking_tie_break_overrides",
                        to="pool.poolparticipant",
                    ),
                ),
                (
                    "pool",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="ranking_tie_break_overrides",
                        to="pool.pool",
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="ranking_tie_break_overrides_updated",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["pool", "manual_position", "participant_id"],
                "unique_together": {("pool", "participant"), ("pool", "manual_position")},
            },
        ),
    ]
