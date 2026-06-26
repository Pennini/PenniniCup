from django.db import migrations

KNOCKOUT_PHASE_DEFAULTS = {
    "R32": {"exact": 40, "advancing_goals": 30, "diff": 25, "loser_goals": 22, "advancing_only": 20},
    "R16": {"exact": 50, "advancing_goals": 38, "diff": 32, "loser_goals": 28, "advancing_only": 26},
    "QF": {"exact": 62, "advancing_goals": 47, "diff": 40, "loser_goals": 35, "advancing_only": 32},
    "SF": {"exact": 78, "advancing_goals": 59, "diff": 50, "loser_goals": 44, "advancing_only": 40},
    "FINAL": {"exact": 95, "advancing_goals": 72, "diff": 60, "loser_goals": 53, "advancing_only": 48},
    "THIRD": {"exact": 55, "advancing_goals": 41, "diff": 35, "loser_goals": 30, "advancing_only": 27},
}


def seed_phase_rows(apps, schema_editor):
    PoolScoringConfig = apps.get_model("pool", "PoolScoringConfig")
    PoolKnockoutPhaseScoring = apps.get_model("pool", "PoolKnockoutPhaseScoring")
    for config in PoolScoringConfig.objects.all():
        for phase_key, values in KNOCKOUT_PHASE_DEFAULTS.items():
            PoolKnockoutPhaseScoring.objects.get_or_create(config=config, phase_key=phase_key, defaults=values)


def unseed_phase_rows(apps, schema_editor):
    PoolKnockoutPhaseScoring = apps.get_model("pool", "PoolKnockoutPhaseScoring")
    PoolKnockoutPhaseScoring.objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ("pool", "0017_poolknockoutphasescoring"),
    ]

    operations = [
        migrations.RunPython(seed_phase_rows, unseed_phase_rows),
    ]
