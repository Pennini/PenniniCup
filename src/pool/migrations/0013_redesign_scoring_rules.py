# Generated manually for redesign-scoring-rules branch

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('football', '0007_assignthird'),
        ('pool', '0012_add_show_supporter_stars_to_pool'),
    ]

    operations = [
        # ── PoolScoringConfig: remove 6 old fields ──────────────────────────
        migrations.RemoveField(
            model_name='poolscoringconfig',
            name='group_winner_or_draw_points',
        ),
        migrations.RemoveField(
            model_name='poolscoringconfig',
            name='group_exact_score_points',
        ),
        migrations.RemoveField(
            model_name='poolscoringconfig',
            name='group_one_team_score_points',
        ),
        migrations.RemoveField(
            model_name='poolscoringconfig',
            name='knockout_winner_advancing_points',
        ),
        migrations.RemoveField(
            model_name='poolscoringconfig',
            name='knockout_exact_score_points',
        ),
        migrations.RemoveField(
            model_name='poolscoringconfig',
            name='knockout_one_team_score_points',
        ),
        # ── PoolScoringConfig: add 11 new fields ─────────────────────────────
        migrations.AddField(
            model_name='poolscoringconfig',
            name='group_exact_score',
            field=models.PositiveSmallIntegerField(default=25),
        ),
        migrations.AddField(
            model_name='poolscoringconfig',
            name='group_winner_and_winner_goals',
            field=models.PositiveSmallIntegerField(default=18),
        ),
        migrations.AddField(
            model_name='poolscoringconfig',
            name='group_winner_and_diff',
            field=models.PositiveSmallIntegerField(default=15),
        ),
        migrations.AddField(
            model_name='poolscoringconfig',
            name='group_winner_and_loser_goals',
            field=models.PositiveSmallIntegerField(default=12),
        ),
        migrations.AddField(
            model_name='poolscoringconfig',
            name='group_winner_only',
            field=models.PositiveSmallIntegerField(default=10),
        ),
        migrations.AddField(
            model_name='poolscoringconfig',
            name='knockout_exact_and_advancing',
            field=models.PositiveSmallIntegerField(default=35),
        ),
        migrations.AddField(
            model_name='poolscoringconfig',
            name='knockout_advancing_and_winner_goals',
            field=models.PositiveSmallIntegerField(default=25),
        ),
        migrations.AddField(
            model_name='poolscoringconfig',
            name='knockout_advancing_and_diff',
            field=models.PositiveSmallIntegerField(default=21),
        ),
        migrations.AddField(
            model_name='poolscoringconfig',
            name='knockout_advancing_and_loser_goals',
            field=models.PositiveSmallIntegerField(default=17),
        ),
        migrations.AddField(
            model_name='poolscoringconfig',
            name='knockout_advancing_only',
            field=models.PositiveSmallIntegerField(default=14),
        ),
        migrations.AddField(
            model_name='poolscoringconfig',
            name='knockout_exact_wrong_advancing',
            field=models.PositiveSmallIntegerField(default=10),
        ),
        # ── PoolBetScore: remove 3 old fields ────────────────────────────────
        migrations.RemoveField(
            model_name='poolbetscore',
            name='winner_or_draw',
        ),
        migrations.RemoveField(
            model_name='poolbetscore',
            name='winner_advancing',
        ),
        migrations.RemoveField(
            model_name='poolbetscore',
            name='one_team_score',
        ),
        # ── PoolBetScore: add 4 new fields ───────────────────────────────────
        migrations.AddField(
            model_name='poolbetscore',
            name='advancing_correct',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='poolbetscore',
            name='advancing_goals_correct',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='poolbetscore',
            name='diff_correct',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='poolbetscore',
            name='eliminated_goals_correct',
            field=models.BooleanField(default=False),
        ),
        # ── PoolParticipant: rename winner_or_draw_hits → advancing_hits ─────
        migrations.RenameField(
            model_name='poolparticipant',
            old_name='winner_or_draw_hits',
            new_name='advancing_hits',
        ),
        # ── PoolOfficialResult: add top_scorers M2M ──────────────────────────
        migrations.AddField(
            model_name='poolofficialresult',
            name='top_scorers',
            field=models.ManyToManyField(blank=True, related_name='official_top_scorer_tied_pools', to='football.player'),
        ),
        # ── PoolScoringConfig: update bonus field defaults ────────────────────
        migrations.AlterField(
            model_name='poolscoringconfig',
            name='bonus_champion_points',
            field=models.PositiveSmallIntegerField(default=120),
        ),
        migrations.AlterField(
            model_name='poolscoringconfig',
            name='bonus_runner_up_points',
            field=models.PositiveSmallIntegerField(default=60),
        ),
        migrations.AlterField(
            model_name='poolscoringconfig',
            name='bonus_third_place_points',
            field=models.PositiveSmallIntegerField(default=30),
        ),
        migrations.AlterField(
            model_name='poolscoringconfig',
            name='bonus_top_scorer_points',
            field=models.PositiveSmallIntegerField(default=100),
        ),
    ]
