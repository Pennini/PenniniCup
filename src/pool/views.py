import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from src.football.models import Match
from src.pool.forms import PoolBetForm
from src.pool.models import Pool, PoolBet, PoolParticipant
from src.pool.services.projection import projected_group_top2, resolve_knockout_placeholder_team
from src.pool.services.rules import PHASE_GROUP, PHASE_KNOCKOUT, phase_for_match

logger = logging.getLogger(__name__)


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

    matches = list(
        Match.objects.filter(season=pool.season)
        .select_related("stage", "group", "home_team", "away_team")
        .order_by("match_date_brasilia", "match_number")
    )
    bets_by_match_id = {bet.match_id: bet for bet in PoolBet.objects.filter(participant=participant).all()}

    projected = projected_group_top2(participant=participant, season=pool.season)

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

    context = {
        "pool": pool,
        "participant": participant,
        "match_rows": match_rows,
        "group_rows": group_rows,
        "knockout_rows": knockout_rows,
        "can_bet": participant.can_bet(),
        "group_locked": pool.is_phase_locked(PHASE_GROUP),
        "knockout_locked": pool.is_phase_locked(PHASE_KNOCKOUT),
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
        except Exception as exc:
            logger.warning(
                "Falha ao salvar palpite em lote: participant=%s match=%s error=%s",
                participant.id,
                match.id,
                str(exc),
            )
            messages.error(request, f"Jogo {match.match_number}: {str(exc)}")
            error_count += 1

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
