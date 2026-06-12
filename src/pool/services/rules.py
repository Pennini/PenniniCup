import contextlib
import re

PHASE_GROUP = "GROUP"
PHASE_KNOCKOUT = "KNOCKOUT"

POOL_TYPE_1 = 1  # Classificados travados, placar livre — scoring posicional
POOL_TYPE_2 = 2  # Palpite progressivo — scoring normal com unlock por partida


def normalize_stage_key(stage):
    if not stage:
        return ""

    stage_name = (stage.name or "").upper().replace("-", " ").strip()
    if "GROUP" in stage_name or "GRUPO" in stage_name or "PRIMEIRA FASE" in stage_name:
        return "GROUP"
    if "SEMI" in stage_name or "SF" in stage_name:
        return "SF"
    if "QUART" in stage_name or "QF" in stage_name:
        return "QF"
    if "R16" in stage_name or "OITAV" in stage_name or "ROUND OF 16" in stage_name:
        return "R16"
    if "R32" in stage_name or "32 AVOS" in stage_name or "SEGUNDAS DE FINAL" in stage_name:
        return "R32"
    if "DECIS" in stage_name and "3" in stage_name:
        return "THIRD"
    if "TERCE" in stage_name and "LUGAR" in stage_name:
        return "THIRD"
    if stage_name == "FINAL":
        return "FINAL"
    if "FINAL" in stage_name and "SEMI" not in stage_name and "QUART" not in stage_name and "OITAV" not in stage_name:
        return "FINAL"
    return ""


def phase_for_match(match):
    stage_key = normalize_stage_key(match.stage)
    if stage_key == "GROUP":
        return PHASE_GROUP
    return PHASE_KNOCKOUT


def _get_parent_match_number(placeholder):
    """Extract match_number from W<N> or RU<N> placeholder. Returns None for group placeholders."""
    m = re.match(r"^(?:W|RU)(\d+)$", (placeholder or "").strip().upper())
    return int(m.group(1)) if m else None


_UNSET = object()


def get_knockout_global_lock_time(season):
    """Tipo 2: deadline único de todo o mata-mata = kickoff do 1o jogo de mata-mata (R32).

    O calendário é dado estático; memoiza no objeto `season` para não repetir o scan
    full-season a cada bet num bulk save (clean() chama por palpite). O cache vive
    apenas enquanto a instância de season existir, ou seja, é request-scoped.
    """
    cached = getattr(season, "_knockout_global_lock_time", _UNSET)
    if cached is not _UNSET:
        return cached

    from src.football.models import Match as FootballMatch

    dates = [
        m.match_date_brasilia
        for m in FootballMatch.objects.filter(season=season).select_related("stage")
        if normalize_stage_key(m.stage) not in ("GROUP", "")
    ]
    result = min(dates) if dates else None
    with contextlib.suppress(AttributeError, TypeError):
        season._knockout_global_lock_time = result
    return result


def is_group_stage_finished(season):
    """True once the group stage is over: the calendar day (Brasília) of the
    last group-stage match has fully passed.

    The group-qualifier (classificados) bonus must only be awarded after the
    group stage ends — never while Standings are still provisional mid-stage.
    """
    from django.utils import timezone

    from src.football.models import Match as FootballMatch

    group_dates = [
        m.match_date_brasilia
        for m in FootballMatch.objects.filter(season=season).select_related("stage")
        if normalize_stage_key(m.stage) == "GROUP"
    ]
    if not group_dates:
        return False
    last_day = timezone.localtime(max(group_dates)).date()
    return timezone.localtime(timezone.now()).date() > last_day


def _bet_row_has_winner(winner_pred_id, home_score_pred, away_score_pred):
    """Versão de `_bet_has_winner` para linhas .values() (sem instância de modelo)."""
    if winner_pred_id is not None:
        return True
    if home_score_pred is None or away_score_pred is None:
        return False
    return home_score_pred != away_score_pred


def _participant_feeder_winner_map(participant, season):
    """Mapa match_number -> bool (palpite ativo define classificado?), memoizado na instância.

    Evita 2 queries por jogo R16+ no clean() (lookup de feeder bet). Usa .values() para
    não materializar PoolBet com campos deferidos (evita reloads de participant_id).
    Snapshot do estado já persistido — coerente com a validação atual, que lê o banco e
    roda antes do bulk_update salvar os palpites em voo. Request-scoped (vive com `participant`).
    """
    cached = getattr(participant, "_feeder_winner_map", None)
    if cached is not None and cached[0] == season.id:
        return cached[1]

    by_number = {
        row["match__match_number"]: _bet_row_has_winner(
            row["winner_pred_id"], row["home_score_pred"], row["away_score_pred"]
        )
        for row in participant.bets.filter(match__season=season, is_active=True).values(
            "match__match_number", "winner_pred_id", "home_score_pred", "away_score_pred"
        )
    }
    with contextlib.suppress(AttributeError, TypeError):
        participant._feeder_winner_map = (season.id, by_number)
    return by_number


def _bet_has_winner(bet):
    """Palpite define um classificado? (winner_pred explícito ou placar decisivo)."""
    if bet.winner_pred_id is not None:
        return True
    if bet.home_score_pred is None or bet.away_score_pred is None:
        return False
    return bet.home_score_pred != bet.away_score_pred


def is_type2_bet_open(match, season, participant=None):
    """Tipo 2: abertura progressiva por jogo, com trava global no 1o jogo de mata-mata.

    - Trava global: nada de mata-mata abre a partir do kickoff do 1o jogo de R32.
    - R32: abre quando os 2 times reais da fase de grupos estão definidos.
    - R16+: abre quando o participante já palpitou os 2 feeders imediatos (a projeção
      produz os 2 times do jogo). Sem participant não há projeção, logo fechado.
    """
    from django.utils import timezone

    stage_key = normalize_stage_key(match.stage)
    if stage_key in ("GROUP", ""):
        return False

    lock_time = get_knockout_global_lock_time(season)
    if lock_time is not None and timezone.now() >= lock_time:
        return False

    if stage_key == "R32":
        return match.home_team_id is not None and match.away_team_id is not None

    if participant is None:
        return False

    feeder_winner = _participant_feeder_winner_map(participant, season)
    for placeholder in (match.home_placeholder, match.away_placeholder):
        parent_num = _get_parent_match_number(placeholder)
        if parent_num is None:
            return False
        if not feeder_winner.get(parent_num, False):
            return False
    return True
