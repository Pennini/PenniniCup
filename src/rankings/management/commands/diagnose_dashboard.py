"""DIAGNÓSTICO read-only da dashboard de visão geral.

Não grava nada no banco. Serve só para achar a causa dos números errados de
aproveitamento e dos troféus do Hall da Fama: compara os agregados/scores
*armazenados* com um recálculo *fresco* (calculate_bet_points), expõe scores
defasados (placar mudou e o score não foi recalculado), linhas de score
faltando em jogos finalizados e drift de exact_score_hits/advancing_hits.

Uso:
    poetry run python -m src.manage diagnose_dashboard --pool <slug>

Pode ser apagado depois de fechar o diagnóstico.
"""

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from src.football.models import Match
from src.pool.models import Pool
from src.pool.services.rules import PHASE_GROUP, phase_for_match
from src.pool.services.scoring import calculate_bet_points
from src.rankings.models import PoolDashboardSnapshot, PoolDashboardSnapshotJob, PoolRankingHistory
from src.rankings.services.dashboard import (
    _hall_of_fame,
    _normalize_payload,
    _utilization_pct,
    build_dashboard_pool_payload,
)
from src.rankings.services.leaderboard import build_pool_leaderboard


def _is_finished(match):
    return match.status == Match.STATUS_FINISHED or (match.home_score is not None and match.away_score is not None)


class Command(BaseCommand):
    help = "DIAGNÓSTICO read-only da dashboard (aproveitamento + troféus). Não grava nada."

    def add_arguments(self, parser):
        parser.add_argument("--pool", type=str, required=True, help="Slug do bolão.")
        parser.add_argument(
            "--participant",
            type=str,
            default="",
            help="Username: imprime o detalhe cru dos troféus desse participante.",
        )

    def handle(self, *args, **options):
        pool = Pool.objects.select_related("season").filter(slug=options["pool"]).first()
        if not pool:
            raise CommandError(f"Bolão '{options['pool']}' não encontrado.")

        cfg = pool.get_scoring_config()
        w = self.stdout.write

        matches = list(Match.objects.filter(season=pool.season).select_related("stage"))
        finished = [m for m in matches if _is_finished(m)]
        finished_ids = {m.id for m in finished}
        group_fin = [m for m in finished if phase_for_match(m) == PHASE_GROUP]
        ko_fin = [m for m in finished if phase_for_match(m) != PHASE_GROUP]
        denom = len(group_fin) * cfg.group_exact_score + len(ko_fin) * cfg.knockout_exact_and_advancing

        w("=" * 90)
        w(f"POOL {pool.slug}  type={pool.pool_type}  season={pool.season_id}")
        w(
            f"jogos finalizados={len(finished)} (grupo={len(group_fin)} mata-mata={len(ko_fin)})  "
            f"group_max={cfg.group_exact_score}  ko_max={cfg.knockout_exact_and_advancing}  "
            f"DENOMINADOR={denom}"
        )
        w("=" * 90)

        leaderboard = build_pool_leaderboard(pool)
        w(f"participantes elegíveis={len(leaderboard)}")
        w("")
        w(
            "user | pos | stored_total | recon_total | betΣ_stored/fresh | "
            "bonus+qual+teamadv | exact st/fr | adv st/fr | "
            "fin_bets/score/missing | stale | aprov_st%/fr% | FLAGS"
        )
        w("-" * 90)

        for row in leaderboard:
            p = row.participant
            bets = list(p.bets.select_related("match", "match__stage", "score").all())

            bet_sum_stored = bet_sum_fresh = 0
            exact_fresh = adv_fresh = 0
            team_adv_cnt = stale = 0
            fin_bets = score_rows = missing = 0
            num_stored = num_fresh = 0

            for bet in bets:
                sc = getattr(bet, "score", None)
                stored_pts = sc.points if sc else 0
                fresh = calculate_bet_points(bet, scoring_config=cfg, pool_type=pool.pool_type)

                if sc:
                    bet_sum_stored += stored_pts
                    if sc.team_advancement_bonus:
                        team_adv_cnt += 1
                    if stored_pts != fresh["points"]:
                        stale += 1
                bet_sum_fresh += fresh["points"]
                if fresh["exact_score"]:
                    exact_fresh += 1
                if fresh["advancing_correct"]:
                    adv_fresh += 1

                if bet.match_id in finished_ids:
                    fin_bets += 1
                    if sc:
                        score_rows += 1
                        num_stored += stored_pts
                    else:
                        missing += 1
                    num_fresh += fresh["points"]

            team_adv_pts = team_adv_cnt * cfg.knockout_team_advancement_bonus
            recon_total = bet_sum_stored + p.bonus_points + p.qualifier_bonus_points + team_adv_pts
            aprov_st = round(num_stored / denom * 100, 1) if denom else 0
            aprov_fr = round(num_fresh / denom * 100, 1) if denom else 0

            flags = []
            if recon_total != p.total_points:
                flags.append("TOTAL_DRIFT")
            if p.exact_score_hits != exact_fresh:
                flags.append("EXACT_DRIFT")
            if p.advancing_hits != adv_fresh:
                flags.append("ADV_DRIFT")
            if stale:
                flags.append("STALE")
            if missing:
                flags.append("MISSING")

            w(
                f"{p.user.username} | #{row.position} | {p.total_points} | {recon_total} | "
                f"{bet_sum_stored}/{bet_sum_fresh} | "
                f"{p.bonus_points}+{p.qualifier_bonus_points}+{team_adv_pts} | "
                f"{p.exact_score_hits}/{exact_fresh} | {p.advancing_hits}/{adv_fresh} | "
                f"{fin_bets}/{score_rows}/{missing} | {stale} | "
                f"{aprov_st}/{aprov_fr} | {' '.join(flags)}"
            )

        # Hall recalculado AGORA (fresh), direto dos dados — a "realidade".
        username_by_id = {r.participant.id: r.participant.user.username for r in leaderboard}
        eligible_ids = set(username_by_id)
        hall = _hall_of_fame(pool, eligible_ids, username_by_id, leaderboard, list(finished_ids))
        w("")
        w("== Hall da Fama recalculado AGORA (fresh / realidade) ==")
        for key, entry in hall.items():
            if entry:
                extra = {k: v for k, v in entry.items() if k not in ("username", "value")}
                w(f"  {key}: {entry['username']} = {entry['value']} {extra if extra else ''}")
            else:
                w(f"  {key}: (vazio)")

        # CACHE (o que a dashboard REALMENTE serve) vs fresh. Diferença = cache stale.
        w("")
        w("== Cache (PoolDashboardSnapshot, o que a dashboard serve) vs fresh ==")
        snap = PoolDashboardSnapshot.objects.filter(pool=pool).first()
        if not snap:
            w("  SEM cache ainda — dashboard constrói no 1o acesso (então mostraria fresh).")
        else:
            cached = _normalize_payload(snap.payload)
            fresh = build_dashboard_pool_payload(pool=pool)
            w(f"  computed_at={snap.computed_at}")

            cache_positions = cached.get("positions")
            if cache_positions is None:
                w("  (payload novo: positions/aproveitamento agora são ao vivo — comparando só hall + version)")
            else:
                fpos = fresh.get("positions", {})
                diff_pos = "" if cache_positions == fpos else f"DIFEREM cache={cache_positions} fresh={fpos}"
                w(f"  positions: {diff_pos if diff_pos else 'IGUAIS'}")

                cden, fden = cached.get("denominator") or 0, fresh.get("denominator") or 0
                if cden != fden:
                    w(f"  DENOMINADOR difere: cache={cden} fresh={fden}")

                cmax, fmax = cached.get("max_points_by_id", {}), fresh.get("max_points_by_id", {})
                for pid, uname in username_by_id.items():
                    ca, fa = _utilization_pct(cmax.get(pid, 0), cden), _utilization_pct(fmax.get(pid, 0), fden)
                    w(f"  aprov {uname}: cache={ca}% fresh={fa}%{'  <-- DIFERE' if ca != fa else ''}")

            w(f"  version cache={cached.get('version')} fresh={fresh.get('version')}")

            chall, fhall = cached.get("hall_of_fame", {}), fresh.get("hall_of_fame", {})
            w("  hall:")
            for key in fhall:
                cv, fv = chall.get(key), fhall.get(key)
                w(f"    {key}: cache={cv} fresh={fv}{'  <-- DIFERE' if cv != fv else ''}")

        # Estado da fila de recálculo da dashboard: job FAILED/preso = cache congelado.
        w("")
        w("== Fila de recálculo da dashboard (worker) ==")
        job = PoolDashboardSnapshotJob.objects.filter(pool=pool).first()
        if not job:
            w("  sem job — cache nunca foi enfileirado p/ recálculo (só o build do 1o acesso).")
        else:
            w(
                f"  status={job.status} attempts={job.attempts} "
                f"requested_at={job.requested_at} last_finished_at={job.last_finished_at}"
            )
            if job.last_error:
                w(f"  last_error={job.last_error}")

        # Detalhe cru por participante (--participant): mostra a realidade jogo-a-jogo
        # p/ comparar com o que a pessoa espera. Read-only.
        if options.get("participant"):
            uname = options["participant"]
            part = next((r.participant for r in leaderboard if r.participant.user.username == uname), None)
            pos = next((r.position for r in leaderboard if r.participant.user.username == uname), None)
            w("")
            w("=" * 90)
            if part is None:
                w(f"participante '{uname}' não encontrado entre os elegíveis.")
            else:
                w(f"DETALHE — {uname} (#{pos})")
                finished_sorted = sorted(finished, key=lambda m: (m.match_date_utc, m.match_number, m.id))
                bets_by_match = {b.match_id: b for b in part.bets.select_related("match", "score").all()}

                w("")
                w("Jogos finalizados (cronológico): dia | jogo | pred | real | pts | flag")
                streak = best_streak = zeroed_bet = zeroed_missing = 0
                day_points = {}
                for m in finished_sorted:
                    day = timezone.localtime(m.match_date_utc).date().isoformat() if m.match_date_utc else "?"
                    home = m.home_team.name if m.home_team else (m.home_placeholder or "?")
                    away = m.away_team.name if m.away_team else (m.away_placeholder or "?")
                    real = f"{m.home_score}-{m.away_score}"
                    b = bets_by_match.get(m.id)
                    if b is None:
                        streak = 0
                        zeroed_missing += 1
                        w(f"  {day} | {home} x {away} | (sem palpite) | {real} | 0 | MISS streak->0")
                        continue
                    pts = b.score.points if getattr(b, "score", None) else 0
                    pred = f"{b.home_score_pred}-{b.away_score_pred}" if b.home_score_pred is not None else "(vazio)"
                    if pts > 0:
                        streak += 1
                        best_streak = max(best_streak, streak)
                        day_points[day] = day_points.get(day, 0) + pts
                        flag = f"streak={streak}"
                    else:
                        streak = 0
                        zeroed_bet += 1
                        flag = "ZERO streak->0"
                    w(f"  {day} | {home} x {away} | {pred} | {real} | {pts} | {flag}")

                w("")
                w(f"Pegando Fogo (correto: quebra em sem-palpite e em zero) = {best_streak}")
                w(
                    f"Pé Frio: apostou e zerou={zeroed_bet}  não apostou={zeroed_missing}  "
                    f"total(incl. ausência)={zeroed_bet + zeroed_missing}"
                )
                if day_points:
                    bd = max(day_points.items(), key=lambda kv: kv[1])
                    w(
                        f"Dia Iluminado = {bd[1]} pts em {bd[0]}  (top dias: "
                        + ", ".join(f"{d}={p}" for d, p in sorted(day_points.items(), key=lambda kv: -kv[1])[:5])
                        + ")"
                    )

                hist = list(
                    PoolRankingHistory.objects.filter(pool=pool, participant_id=part.id)
                    .order_by("round_index")
                    .values("round_index", "position", "total_points")
                )
                w("")
                w(f"Posição por rodada (PoolRankingHistory, {len(hist)} rodadas) — round:pos(pts):")
                w("  " + "  ".join(f"{h['round_index']}:{h['position']}({h['total_points']})" for h in hist))
                if hist:
                    positions = [h["position"] for h in hist]
                    churn = sum(abs(positions[i] - positions[i - 1]) for i in range(1, len(positions)))
                    w(f"  Ioiô (churn Σ|Δpos|)={churn}  melhor(min)={min(positions)}  pior(max)={max(positions)}")
                    tps = [h["total_points"] for h in hist]
                    if len(tps) > 1 and len(set(tps)) == 1:
                        w("  >>> total_points IGUAL em todas as rodadas = snapshot grava agregado atual (bug #1)")
                    if hist[0]["round_index"] != 1 or any(
                        hist[i]["round_index"] != hist[i - 1]["round_index"] + 1 for i in range(1, len(hist))
                    ):
                        w("  >>> round_index não é 1..N contíguo = ordem de processamento, não cronologia (bug #1)")

        w("")
        w("Legenda: st=stored fr=fresh(recalc agora). DRIFT/STALE/MISSING ou cache!=fresh = bug.")
