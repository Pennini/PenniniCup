# Tipo 2 — pontuação de mata-mata por fase (chuva de pontos)

**Data:** 2026-06-25
**Branch base:** feat/palpites-carrossel
**Escopo:** só `POOL_TYPE_2`. Tipo 1 intacto.

## Objetivo

Tornar o mata-mata do bolão Tipo 2 mais competitivo: a pontuação por placar
**escala por fase** (R32 < R16 < QF < SF < FINAL), com a final valendo mais. A
decisão de 3º lugar (THIRD) tem faixa própria. Sem bônus de classificado
separado — a magnitude vai embutida nas faixas.

## O que NÃO muda (decisão validada)

A **semântica do gate** permanece idêntica à atual:

- O mata-mata Tipo 2 pontua **por jogo, pela identidade do classificado**:
  `predicted_advancing_id == match.winner_id`.
- `predicted_advancing_id` = o time que o participante marcou para avançar
  **naquele jogo** (lado vencedor do placar palpitado, ou `winner_pred` no
  empate), resolvido pelo walk do bracket projetado (`_walk_knockout_bracket`)
  para R16+ e pelos times reais no R32.
- Classificado errado → **0**, mesmo com placar exato. Sem consolação.
- O time **eliminado** do confronto é irrelevante. Acertar quem avança e errar
  o adversário (ex.: real Marrocos 1×2 Holanda, palpite Brasil 1×2 Holanda)
  pontua cheio, porque Holanda == Holanda e o placar 1×2 == 1×2.
- **Não há cascata**: cada jogo é avaliado isoladamente pela identidade do seu
  classificado. Não existe regra "seu time caiu antes → zera os próximos".

`_walk_knockout_bracket`, `resolve_knockout_advancing_by_match` e os call sites
(ranking, asof, diagnose) **não mudam de comportamento** — continuam passando
`predicted_advancing_id` por jogo. A única novidade no scoring é **qual tabela
de pontos** se aplica, escolhida pela fase do jogo.

## O que muda

### 1. Faixas de placar por fase

Substituem os campos flat `knockout_*` (que o Tipo 2 usava igual em todo o
mata-mata) por uma tabela por fase. Tipo 1 segue usando os campos flat.

| Fase  | exato | g-classif | dif | g-elim | só-classif |
| ----- | ----- | --------- | --- | ------ | ---------- |
| R32   | 40    | 30        | 25  | 22     | 20         |
| R16   | 50    | 38        | 32  | 28     | 26         |
| QF    | 62    | 47        | 40  | 35     | 32         |
| SF    | 78    | 59        | 50  | 44     | 40         |
| FINAL | 95    | 72        | 60  | 53     | 48         |
| THIRD | 55    | 41        | 35  | 30     | 27         |

- Monotônica R32→FINAL. Final exato = 95 = 3,8× jogo de grupo exato (25).
- `só-classif` (`advancing_only`) é o piso e **a** recompensa por acertar quem
  avança — sobe 20→48 nas fases altas. Sem campo de bônus.

### 2. Sem bônus de classificado

Descartado por redundância: somar um bônus fixo por cima de **todas** as faixas
de uma fase é só um offset constante — não distingue resultados. A magnitude já
está embutida nas faixas. Acertar o classificado da FINAL = acertar o campeão →
já dispara `bonus_champion_points` (120), mecanismo separado que acumula.

### 3. Empate real (pênaltis)

Sem campo novo. Reusa as faixas da própria fase, como hoje:

- placar exato → `exact` da fase
- mesma diferença (0) → `diff` da fase
- senão → `advancing_only` da fase

## Modelo de dados

Novo modelo filho, uma linha por fase de mata-mata por config:

```
class PoolKnockoutPhaseScoring(models.Model):
    config        = FK(PoolScoringConfig, related_name="knockout_phases")
    phase_key     = CharField(choices=["R32","R16","QF","SF","FINAL","THIRD"])
    exact            = PositiveSmallInteger   # placar exato + classificado
    advancing_goals  = PositiveSmallInteger   # classificado + gols do classificado
    diff             = PositiveSmallInteger   # classificado + diferença
    loser_goals      = PositiveSmallInteger   # classificado + gols do eliminado
    advancing_only   = PositiveSmallInteger   # só o classificado
    unique_together = (config, phase_key)
```

- 6 linhas por config. Admin: `TabularInline` em `PoolScoringConfig`.
- **Migração de dados**: para cada `PoolScoringConfig` existente, cria as 6
  linhas com os defaults da tabela acima.
- Defaults do modelo (campo-a-campo) = linha R32 da tabela; a migração de dados
  sobrescreve por fase. (Pools novas: criar as 6 linhas no mesmo ponto onde a
  config é criada hoje.)

### Por que modelo filho e não ~30 campos flat

5 faixas × 6 fases = 30 campos novos em `PoolScoringConfig` — migração e admin
ingovernáveis. Modelo filho: 6 linhas, inline limpo, tunar = editar linha.

## Mudança no scoring

`src/pool/services/scoring.py`, branch `pool_type == POOL_TYPE_2`:

1. Resolver a fase do jogo: `stage_key = normalize_stage_key(match.stage)`
   (já importável de `rules`; hoje só importa `phase_for_match`).
1. Pegar a linha `PoolKnockoutPhaseScoring` daquela fase.
1. `_knockout_points_by_score(...)` passa a ler `exact/advancing_goals/diff/ loser_goals/advancing_only` **da linha da fase** em vez dos campos flat de
   `scoring_config`.

Assinatura: passar a linha da fase (ou um dict `{phase_key: row}`) para
`calculate_bet_points`. Preferência: resolver `{phase_key: row}` uma vez por
config e passar adiante, evitando query por palpite.

### Call sites (ranking / asof / diagnose)

- Já resolvem `advancing_map` e passam `predicted_advancing_id` por palpite —
  **inalterado**.
- Precisam disponibilizar as linhas de fase da config. Carregar
  `config.knockout_phases.all()` uma vez (fora do loop de participantes, como já
  é feito com `knockout_matches`) e montar `{phase_key: row}`. Prefetch para
  evitar N+1.
- `scoring.py` resolve a fase a partir do `match` — os call sites não precisam
  saber a fase de cada jogo.

### Tipo 1

Branch posicional (`pool_type != POOL_TYPE_2`) segue lendo os campos flat
`knockout_*` de `scoring_config`. Nenhuma mudança.

## Recálculo

Mudar os valores recalcula as pools Tipo 2 existentes (pontuação muda). Fluxo de
recálculo atual (ranking/asof) já cobre — só passa a ler a tabela por fase.

## Docs

Atualizar `src/SCORE.md`:

- Tabela de mata-mata do Tipo 2 vira **por fase** (a tabela acima).
- Remover a frase confusa "ou o time projetado foi eliminado antes deste jogo"
  — a regra real é **só identidade do classificado por jogo**, sem cascata.
- Explicitar: sem bônus de classificado; campeão/3º são bônus de torneio à parte.

## Testes

- Unit (`SimpleTestCase`): para cada fase, as 5 faixas + empate-pênaltis +
  classificado errado = 0. Helper que monta a linha de fase.
- Confirmar escala: mesmo palpite/real pontua mais na FINAL que no R32.
- Caso do exemplo: real Marrocos 1×2 Holanda, palpite Brasil 1×2 Holanda
  (classificado Holanda) → exato da fase.
- Integração (DB): `recalculate_participant_scores` e `compute_asof_standings`
  com pool Tipo 2 multi-fase, conferindo valores por fase.
- Migração de dados: config existente ganha as 6 linhas corretas.

## Fora de escopo

- Tipo 1 (posicional, flat).
- Mudar a resolução de classificado / bracket walk.
- Consolação por placar ao errar o classificado (gate segue duro).
