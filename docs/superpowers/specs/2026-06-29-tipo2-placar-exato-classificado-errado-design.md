# Tipo 2 — Placar exato com classificado errado (crédito parcial)

Data: 2026-06-29

## Contexto

No bolão **Tipo 2**, o mata-mata é pontuado por **identidade do classificado**, não por
posição. A regra atual (`src/pool/services/scoring.py`, bloco `POOL_TYPE_2`,
linhas 139-166): se o classificado palpitado não bate com o classificado real
(`match.winner_id`), o palpite zera — independente do placar.

Os times do jogo palpitado nem sempre são os times reais:

- **R32**: os dois times do palpite são os times reais (vêm da classificação da fase
  de grupos).
- **R16+**: os times são **projetados** pelos palpites do próprio usuário nos jogos
  feeders. O usuário pode palpitar "Brasil x Coreia" quando o jogo real é
  "Brasil x Japão".

No fluxo normal, o gate é só no classificado: se o classificado palpitado está
correto, o usuário pontua mesmo que o eliminado palpitado seja diferente do real.

## Objetivo

Adicionar uma exceção ao gate do Tipo 2:

> Se o usuário acerta o **placar EXATO**, **os dois times do jogo palpitado são
> exatamente os dois times reais** do jogo real, **mas erra o classificado**,
> então ele ganha um valor **configurável por fase** (`exact_wrong_advancing`).

Exemplo (32 avos): real Brasil 1×1 Japão (pênaltis → Brasil classifica). Usuário
palpitou Brasil 1×1 Japão com Japão classificando. Placar exato ✓, os dois times
são os reais ✓, classificado errado (Japão ≠ Brasil). Pontuação: o valor de
`exact_wrong_advancing` da fase R32 (ex.: 23, se assim configurado).

Observação: `exact_wrong_advancing` é um novo campo por fase em
`PoolKnockoutPhaseScoring` (mesma estrutura de `exact`/`advancing_only`), com
fallback para o campo flat `PoolScoringConfig.knockout_exact_wrong_advancing` (que
até hoje existia mas estava morto). O admin configura cada fase à vontade; o valor
default out-of-box é `exact − advancing_only` da fase.

### Quando a exceção dispara na prática

Só em jogos decididos nos pênaltis (empate no tempo regulamentar). Num placar
decisivo, `PoolBet.clean()` força `winner_pred` para o vencedor do placar, então o
classificado palpitado coincidiria com o real e o caso normal já se aplicaria. A
implementação não precisa checar "empate" explicitamente — a condição
`placar exato + classificado errado` já restringe a isso.

### Valor pago

A exceção paga diretamente `tier.exact_wrong_advancing` — valor configurável por
fase. Sem subtração e sem piso (é um número configurado, não computado).

## Arquitetura

### Novo campo por fase

Adicionar `exact_wrong_advancing` (PositiveSmallIntegerField, sem default no model)
em `PoolKnockoutPhaseScoring`, incluí-lo em `KNOCKOUT_PHASE_DEFAULTS` (default =
`exact − advancing_only` por fase), expor no admin inline e popular via migração
(`0020`, RunPython preenchendo linhas existentes com `max(exact − advancing_only, 0)`). `_tier_from_flat_config` passa a mapear
`exact_wrong_advancing=scoring_config.knockout_exact_wrong_advancing`.

### Mudança de interface

`calculate_bet_points` hoje recebe só `predicted_advancing_id` (um time). Para
testar "os dois times do palpite == os dois times reais" precisa também do **par de
times projetados** do palpite. Novo parâmetro opcional:

```
def calculate_bet_points(
    bet, scoring_config, pool_type=None, predicted_advancing_id=None,
    knockout_phase_scoring=None, predicted_team_ids=None,
):
```

`predicted_team_ids`: tupla/conjunto com os dois ids de time projetados para aquele
jogo (vem do `teams_by_match`). Default `None` → exceção não dispara
(retrocompatível com chamadas/fluxos não conectados e com o caminho flat).

### Lógica nova no bloco `POOL_TYPE_2`

Reordenar para calcular a `tier` antes do gate, depois aplicar a exceção:

```
if pool_type == POOL_TYPE_2:
    stage_key = normalize_stage_key(match.stage)
    tier = (knockout_phase_scoring or {}).get(stage_key) or _tier_from_flat_config(scoring_config)

    is_advancing_correct = bool(match.winner_id) and predicted_advancing_id == match.winner_id
    if not is_advancing_correct:
        real_pair = {match.home_team_id, match.away_team_id}
        teams_match_real = (
            None not in real_pair and predicted_team_ids is not None and set(predicted_team_ids) == real_pair
        )
        if is_exact_score and teams_match_real:
            return {
                "points": tier.exact_wrong_advancing,
                "exact_score": True,
                "advancing_correct": False,
                "advancing_goals_correct": False,
                "diff_correct": False,
                "eliminated_goals_correct": False,
            }
        return {
            "points": 0,
            "exact_score": is_exact_score,
            "advancing_correct": False,
            "advancing_goals_correct": False,
            "diff_correct": False,
            "eliminated_goals_correct": False,
        }

    points, is_exact, advancing_goals, diff_correct, eliminated_goals = _knockout_points_by_score(
        tier, home, away, guess_home, guess_away
    )
    return {...}  # inalterado
```

`is_exact_score` já é computado no topo da função (linha 101). `tier` passa a ser
computada uma vez no topo do bloco e reusada no caminho de acerto (substitui o
cálculo atual das linhas 151-155).

### Resolver combinado (evita walk duplicado)

`context_builder.py` já tem `_walk_knockout_bracket` retornando
`(teams_by_match, advancing_by_match)`, mas os callers só usam
`resolve_knockout_advancing_by_match` (um walk só para advancing). Adicionar:

```
def resolve_knockout_teams_and_advancing(*, participant, matches, season, bets_by_match_id=None):
    """(teams_by_match, advancing_by_match) num único walk do bracket."""
    return _walk_knockout_bracket(
        participant=participant,
        matches=matches,
        season=season,
        bets_by_match_id=bets_by_match_id,
    )
```

`teams_by_match`: `{match_id: (home_team, away_team)}` (objetos Team, podem ter
`None`).

### Wiring dos callers

`ranking.py` (`recalculate_participant_scores`) e `asof_standings.py` trocam
`resolve_knockout_advancing_by_match` por `resolve_knockout_teams_and_advancing` e
passam:

```
home_t, away_t = teams_by_match.get(bet.match_id, (None, None))
predicted_team_ids = (home_t.id, away_t.id) if home_t is not None and away_t is not None else None
score_data = calculate_bet_points(
    bet,
    scoring_config=scoring_config,
    pool_type=pool_type,
    predicted_advancing_id=advancing_map.get(bet.match_id),
    knockout_phase_scoring=knockout_phase_scoring,
    predicted_team_ids=predicted_team_ids,
)
```

Onde `advancing_map` for usado também para o bônus Tipo 1, manter o mesmo dict
(`advancing_by_match`) — comportamento Tipo 1 inalterado.

## Fluxo de dados

1. Caller resolve `(teams_by_match, advancing_by_match)` por participante (um walk).
1. Por palpite: deriva `predicted_team_ids` do `teams_by_match`.
1. `calculate_bet_points` aplica o gate Tipo 2; se classificado errado, testa a
   exceção (placar exato + par de times == par real).

## Tratamento de erros / bordas

- `predicted_team_ids is None` → exceção não dispara (retorna 0 como hoje).
- Algum id real `None` (jogo sem times resolvidos) → `None in real_pair` →
  exceção não dispara.
- Caminho flat (sem `knockout_phase_scoring`) → `tier` cai no
  `_tier_from_flat_config`; a exceção paga `knockout_exact_wrong_advancing` (flat)
  se `predicted_team_ids` vier preenchido.

## Impacto em exibição

Nenhum direto. `profile.html` e o dashboard derivam `advancing_correct`
independentemente nas views (`penninicup/views.py:86`), não dos flags de score.
Os flags retornados (`exact_score=True`, `advancing_correct=False`) são consistentes
com o caso e não exigem novo flag (YAGNI).

## Testes (`src/pool/tests/test_pool.py`)

1. **Seed do campo por fase**: `get_scoring_config` semeia `exact_wrong_advancing`
   (SF=38, FINAL=47, etc.).
1. **Exceção via faixa por fase**: paga `tier.exact_wrong_advancing` (ex.: 23),
   provando que lê o campo configurado.
1. **Exceção via fallback flat**: sem faixa por fase → paga
   `knockout_exact_wrong_advancing` (10).
1. **R16+ times projetados ≠ reais**: placar exato, classificado errado, mas o par
   projetado difere do par real → 0 (exceção não dispara).
1. **Retrocompat**: `predicted_team_ids=None` com classificado errado → 0.
1. **Integração end-to-end**: `recalculate_participant_scores` aplica a faixa SF.

## Não-objetivos (YAGNI)

- Nenhum novo flag de score para exibição.
- Nenhuma mudança no Tipo 1 nem na fase de grupos.
- Nenhuma mudança no caminho posicional do mata-mata Tipo 1.
