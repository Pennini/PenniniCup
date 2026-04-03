from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("football", "0006_official"),
        ("pool", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="PoolParticipantStanding",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("position", models.PositiveSmallIntegerField()),
                ("played", models.PositiveSmallIntegerField(default=0)),
                ("won", models.PositiveSmallIntegerField(default=0)),
                ("drawn", models.PositiveSmallIntegerField(default=0)),
                ("lost", models.PositiveSmallIntegerField(default=0)),
                ("goals_for", models.PositiveSmallIntegerField(default=0)),
                ("goals_against", models.PositiveSmallIntegerField(default=0)),
                ("goal_difference", models.SmallIntegerField(default=0)),
                ("points", models.PositiveSmallIntegerField(default=0)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "group",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="participant_standings",
                        to="football.group",
                    ),
                ),
                (
                    "participant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="projected_standings",
                        to="pool.poolparticipant",
                    ),
                ),
                (
                    "team",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="participant_standings",
                        to="football.team",
                    ),
                ),
            ],
            options={
                "ordering": ["group__name", "position", "team__code"],
                "unique_together": {("participant", "group", "team")},
            },
        ),
    ]
