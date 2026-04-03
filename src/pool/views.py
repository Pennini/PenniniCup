import logging
from types import SimpleNamespace

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from src.football.models import Match
from src.pool.forms import PoolBetForm
from src.pool.models import Pool, PoolBet, PoolParticipant
from src.pool.services.projection import (
    load_persisted_group_standings,
    projected_group_top2_from_groups,
    resolve_knockout_placeholder_team,
    sync_persisted_group_standings,
)
from src.pool.services.rules import PHASE_GROUP, PHASE_KNOCKOUT, phase_for_match

logger = logging.getLogger(__name__)

STAGE_R32 = "R32"
STAGE_R16 = "R16"
STAGE_QF = "QF"
STAGE_SF = "SF"
STAGE_FINAL = "FINAL"
STAGE_THIRD = "THIRD"

KNOCKOUT_STAGE_ORDER = [STAGE_R32, STAGE_R16, STAGE_QF, STAGE_SF]
KNOCKOUT_LABELS = {
    STAGE_R32: "32 Avos",
    STAGE_R16: "Oitavas",
    STAGE_QF: "Quartas",
    STAGE_SF: "Semifinal",
}


def _make_pairs(items):
    return [items[index : index + 2] for index in range(0, len(items), 2)]


def _normalize_stage_key(stage):
    if not stage:
        return ""

    stage_name = (stage.name or "").upper().replace("-", " ").strip()

    if "SEMI" in stage_name or "SF" in stage_name:
        return STAGE_SF
    if "QUART" in stage_name or "QF" in stage_name:
        return STAGE_QF
    if "R16" in stage_name or "OITAV" in stage_name or "ROUND OF 16" in stage_name:
        return STAGE_R16
    if "R32" in stage_name or "32 AVOS" in stage_name or "SEGUNDAS DE FINAL" in stage_name:
        return STAGE_R32
    if "DECIS" in stage_name and "3" in stage_name:
        return STAGE_THIRD
    if "TERCE" in stage_name and "LUGAR" in stage_name:
        return STAGE_THIRD
    if stage_name == "FINAL":
        return STAGE_FINAL
    if "FINAL" in stage_name and "SEMI" not in stage_name and "QUART" not in stage_name and "OITAV" not in stage_name:
        return STAGE_FINAL

    return ""


def _build_projected_knockout_payload(knockout_rows):
    grouped_matches = {stage_key: [] for stage_key in KNOCKOUT_STAGE_ORDER}
    final_match = None
    third_place_match = None

    for row in knockout_rows:
        stage_key = _normalize_stage_key(row["match"].stage)
        projected_match = SimpleNamespace(
            home_team=row["home_team"],
            away_team=row["away_team"],
            home_team_id=row["home_team"].id if row["home_team"] else None,
            away_team_id=row["away_team"].id if row["away_team"] else None,
            home_score=row["bet"].home_score_pred if row["bet"] else None,
            away_score=row["bet"].away_score_pred if row["bet"] else None,
            winner_id=row["bet"].winner_pred_id if row["bet"] else None,
            home_penalty_score=None,
            away_penalty_score=None,
        )

        if stage_key in grouped_matches:
            grouped_matches[stage_key].append(projected_match)
        elif stage_key == STAGE_FINAL and final_match is None:
            final_match = projected_match
        elif stage_key == STAGE_THIRD and third_place_match is None:
            third_place_match = projected_match

    active_stages = [stage_key for stage_key in KNOCKOUT_STAGE_ORDER if grouped_matches[stage_key]]
    bracket_left = []
    bracket_right = []

    for stage_key in active_stages:
        stage_matches = grouped_matches[stage_key]
        half = len(stage_matches) // 2
        left_matches = stage_matches[:half]
        right_matches = stage_matches[half:]

        bracket_left.append(
            {
                "stage": stage_key,
                "label": KNOCKOUT_LABELS[stage_key],
                "pairs": _make_pairs(left_matches),
                "is_outermost": False,
            }
        )
        bracket_right.append(
            {
                "stage": stage_key,
                "label": KNOCKOUT_LABELS[stage_key],
                "pairs": _make_pairs(right_matches),
                "is_outermost": False,
            }
        )

    if bracket_left:
        bracket_left[0]["is_outermost"] = True

    bracket_right = list(reversed(bracket_right))
    if bracket_right:
        bracket_right[-1]["is_outermost"] = True

    max_matches_side = max((sum(len(pair) for pair in round_data["pairs"]) for round_data in bracket_left), default=2)
    bracket_height = max(max_matches_side * 78, 280)

    return {
        "bracket_left": bracket_left,
        "bracket_right": bracket_right,
        "final_match": final_match,
        "third_place_match": third_place_match,
        "bracket_height": bracket_height,
    }


def _join_pool_with_token(request, pool, invite_token_value):
    token_obj, token_error = pool.validate_invite_token(invite_token_value)
    if token_error:
        messages.error(request, token_error)
        return None

    with transaction.atomic():
        participant, created = PoolParticipant.objects.get_or_create(
            pool=pool,
            user=request.user,
            defaults={"is_active": True},
        )

        if created:
            consumed = pool.consume_invite_token(token_obj)
            if not consumed:
                messages.error(request, "Nao foi possivel consumir o token. Tente novamente.")
                transaction.set_rollback(True)
                return None

    if created:
        messages.success(request, "Voce entrou no bolao com sucesso.")
    else:
        messages.info(request, "Voce ja participa deste bolao.")

    return participant


@login_required
def pool_list(request):
    participations = (
        PoolParticipant.objects.filter(user=request.user, is_active=True)
        .select_related("pool", "pool__season")
        .order_by("pool__name")
    )

    rows = []
    for participant in participations:
        pool = participant.pool
        rows.append(
            {
                "pool": pool,
                "can_bet": participant.can_bet(),
                "group_locked": pool.is_phase_locked(PHASE_GROUP),
                "knockout_locked": pool.is_phase_locked(PHASE_KNOCKOUT),
            }
        )

    return render(request, "pool/list.html", {"rows": rows})


@login_required
@require_http_methods(["POST"])
def open_pool(request):
    pool_slug = (request.POST.get("pool_slug") or "").strip()
    if not pool_slug:
        messages.error(request, "Selecione um bolao para abrir os palpites.")
        return redirect("pool:list")

    participant_exists = PoolParticipant.objects.filter(
        user=request.user, is_active=True, pool__slug=pool_slug
    ).exists()
    if not participant_exists:
        messages.error(request, "Voce nao esta inscrito neste bolao.")
        return redirect("pool:list")

    return redirect("pool:detail", slug=pool_slug)


@login_required
def pool_detail(request, slug):
    pool = get_object_or_404(Pool.objects.select_related("season"), slug=slug, is_active=True)
    participant = get_object_or_404(PoolParticipant, pool=pool, user=request.user)
    active_tab = (request.GET.get("tab") or "bets").strip()
    if active_tab not in ("bets", "classification", "knockout"):
        return redirect(f"{request.path}?tab=bets")

    matches = list(
        Match.objects.filter(season=pool.season)
        .select_related("stage", "group", "home_team", "away_team")
        .order_by("match_date_brasilia", "match_number")
    )
    bets_by_match_id = {bet.match_id: bet for bet in PoolBet.objects.filter(participant=participant).all()}

    projected_groups = load_persisted_group_standings(participant=participant)
    if not projected_groups and participant.bets.exists():
        projected_groups = sync_persisted_group_standings(participant=participant)
    projected = projected_group_top2_from_groups(projected_groups=projected_groups)

    match_rows = []
    group_rows = []
    knockout_rows = []
    for match in matches:
        phase = phase_for_match(match)
        home_team = match.home_team
        away_team = match.away_team

        if phase == PHASE_KNOCKOUT:
            if home_team is None:
                home_team = resolve_knockout_placeholder_team(match.home_placeholder, projected)
            if away_team is None:
                away_team = resolve_knockout_placeholder_team(match.away_placeholder, projected)

        row = {
            "match": match,
            "phase": phase,
            "home_team": home_team,
            "away_team": away_team,
            "bet": bets_by_match_id.get(match.id),
            "locked": pool.is_phase_locked(phase),
        }
        match_rows.append(row)

        if phase == PHASE_GROUP:
            group_rows.append(row)
        else:
            knockout_rows.append(row)

    projected_knockout = _build_projected_knockout_payload(knockout_rows=knockout_rows)

    context = {
        "pool": pool,
        "participant": participant,
        "active_tab": active_tab,
        "match_rows": match_rows,
        "group_rows": group_rows,
        "knockout_rows": knockout_rows,
        "projected_groups": projected_groups,
        "can_bet": participant.can_bet(),
        "group_locked": pool.is_phase_locked(PHASE_GROUP),
        "knockout_locked": pool.is_phase_locked(PHASE_KNOCKOUT),
        "page_mode": "result",
        **projected_knockout,
    }
    return render(request, "pool/detail.html", context)


@login_required
@require_http_methods(["POST"])
def join_pool(request, slug):
    pool = get_object_or_404(Pool, slug=slug, is_active=True)
    invite_token_value = (request.POST.get("invite_token") or "").strip()

    if not invite_token_value:
        messages.error(request, "Informe um token de convite para entrar neste bolao.")
        return redirect("pool:list")

    participant = _join_pool_with_token(request, pool, invite_token_value)
    if participant is None:
        return redirect("pool:list")

    return redirect("pool:detail", slug=pool.slug)


@login_required
@require_http_methods(["POST"])
def join_pool_by_token(request):
    invite_token_value = (request.POST.get("invite_token") or "").strip()
    if not invite_token_value:
        messages.error(request, "Informe um token de convite para entrar em um bolao.")
        return redirect("penninicup:index")

    from src.accounts.models import InviteToken

    token_obj = InviteToken.objects.filter(token=invite_token_value).select_related("pool").first()
    if not token_obj or token_obj.pool_id is None:
        messages.error(request, "Token invalido ou sem bolao associado.")
        return redirect("penninicup:index")

    pool = token_obj.pool
    if not pool.is_active:
        messages.error(request, "O bolao deste token esta inativo.")
        return redirect("penninicup:index")

    participant = _join_pool_with_token(request, pool, invite_token_value)
    if participant is None:
        return redirect("penninicup:index")

    return redirect("pool:detail", slug=pool.slug)


@login_required
@require_http_methods(["POST"])
def save_bet(request, slug, match_id):
    pool = get_object_or_404(Pool, slug=slug, is_active=True)
    participant = get_object_or_404(PoolParticipant, pool=pool, user=request.user, is_active=True)
    match = get_object_or_404(Match.objects.select_related("stage"), id=match_id, season=pool.season)

    existing_bet = PoolBet.objects.filter(participant=participant, match=match).first()
    if existing_bet is None:
        existing_bet = PoolBet(participant=participant, match=match)

    form = PoolBetForm(request.POST, instance=existing_bet, match=match)

    if not form.is_valid():
        for _, errors in form.errors.items():
            for error in errors:
                messages.error(request, error)
        return redirect("pool:detail", slug=pool.slug)

    bet = form.save(commit=False)
    bet.participant = participant
    bet.match = match

    try:
        with transaction.atomic():
            bet.full_clean()
            bet.save()
            if phase_for_match(match) == PHASE_GROUP:
                sync_persisted_group_standings(participant=participant)
    except Exception as exc:
        logger.warning("Falha ao salvar palpite: participant=%s match=%s error=%s", participant.id, match.id, str(exc))
        messages.error(request, str(exc))
        return redirect("pool:detail", slug=pool.slug)

    messages.success(request, "Palpite salvo com sucesso.")
    return redirect("pool:detail", slug=pool.slug)


@login_required
@require_http_methods(["POST"])
def save_bets_bulk(request, slug):
    pool = get_object_or_404(Pool, slug=slug, is_active=True)
    participant = get_object_or_404(PoolParticipant, pool=pool, user=request.user, is_active=True)
    submit_action = (request.POST.get("submit_action") or "save_all").strip()

    if not participant.can_bet():
        messages.error(request, "Participante sem permissao para palpitar.")
        return redirect("pool:detail", slug=pool.slug)

    matches = list(
        Match.objects.filter(season=pool.season)
        .select_related("stage")
        .order_by("match_date_brasilia", "match_number")
    )
    existing_bets = {bet.match_id: bet for bet in PoolBet.objects.filter(participant=participant).all()}

    saved_count = 0
    saved_group_count = 0
    error_count = 0

    for match in matches:
        phase = phase_for_match(match)
        if pool.is_phase_locked(phase):
            continue

        if submit_action == "calculate_knockout" and phase != PHASE_GROUP:
            continue

        home_key = f"match_{match.id}_home_score_pred"
        away_key = f"match_{match.id}_away_score_pred"
        winner_key = f"match_{match.id}_winner_pred"

        home_value = (request.POST.get(home_key) or "").strip()
        away_value = (request.POST.get(away_key) or "").strip()
        winner_value = (request.POST.get(winner_key) or "").strip()

        has_any_value = bool(home_value or away_value or winner_value)
        if not has_any_value:
            continue

        payload = {
            "home_score_pred": home_value,
            "away_score_pred": away_value,
            "winner_pred": winner_value,
        }

        bet = existing_bets.get(match.id)
        if bet is None:
            bet = PoolBet(participant=participant, match=match)

        form = PoolBetForm(payload, instance=bet, match=match)

        if not form.is_valid():
            for _, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f"Jogo {match.match_number}: {error}")
            error_count += 1
            continue

        bet_obj = form.save(commit=False)
        bet_obj.participant = participant
        bet_obj.match = match

        try:
            with transaction.atomic():
                bet_obj.full_clean()
                bet_obj.save()
            saved_count += 1
            if phase == PHASE_GROUP:
                saved_group_count += 1
        except Exception as exc:
            logger.warning(
                "Falha ao salvar palpite em lote: participant=%s match=%s error=%s",
                participant.id,
                match.id,
                str(exc),
            )
            messages.error(request, f"Jogo {match.match_number}: {str(exc)}")
            error_count += 1

    if saved_group_count:
        sync_persisted_group_standings(participant=participant)

    if saved_count:
        if submit_action == "calculate_knockout":
            messages.success(request, "Palpites da fase de grupos salvos. Projecao do mata-mata atualizada.")
        else:
            messages.success(request, f"{saved_count} palpite(s) salvo(s) com sucesso.")
    if not saved_count and not error_count:
        if submit_action == "calculate_knockout":
            messages.info(request, "Nenhum palpite de grupos foi alterado.")
        else:
            messages.info(request, "Nenhuma alteracao para salvar.")

    return redirect("pool:detail", slug=pool.slug)
