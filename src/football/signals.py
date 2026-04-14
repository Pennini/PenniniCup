import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from src.football.models import Match
from src.pool.services.projection_queue import enqueue_projection_recalc_for_season
from src.pool.services.ranking import recalculate_match_scores

logger = logging.getLogger(__name__)

_PROJECTION_RELEVANT_FIELDS = {
    "season",
    "season_id",
    "stage",
    "stage_id",
    "group",
    "group_id",
    "match_number",
    "home_team",
    "home_team_id",
    "away_team",
    "away_team_id",
    "home_placeholder",
    "away_placeholder",
}

_SCORE_RELEVANT_FIELDS = {
    "home_score",
    "away_score",
    "home_penalty_score",
    "away_penalty_score",
    "winner",
    "winner_id",
    "status",
}


@receiver(post_save, sender=Match)
def recalculate_pool_data_after_match_save(sender, instance, created, raw=False, update_fields=None, **kwargs):
    if raw or instance.season_id is None:
        return

    changed_fields = set(update_fields or ())
    projection_should_recalc = created or not changed_fields or bool(changed_fields & _PROJECTION_RELEVANT_FIELDS)
    score_should_recalc = (not created) and (not changed_fields or bool(changed_fields & _SCORE_RELEVANT_FIELDS))

    if score_should_recalc:
        try:
            recalculate_match_scores(match=instance)
        except Exception:
            logger.exception("Falha ao recalcular pontuacoes do bolao apos salvar partida: match_id=%s", instance.id)

    if projection_should_recalc:
        try:
            enqueue_projection_recalc_for_season(season=instance.season)
        except Exception:
            logger.exception("Falha ao enfileirar recalc de projecoes apos salvar partida: match_id=%s", instance.id)
