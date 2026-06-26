# Design — Pontuação do mata-mata para Tipo 2 (gate por classificado)

**Data:** 2026-06-23
**Branch base:** feat/palpites-carrossel

## Contexto

Hoje `calculate_bet_points` (`src/pool/services/scoring.py`) usa **scoring posicional**
no mata-mata para os dois tipos de bolão: o gate é a *direção do placar*
(`_winner_from_score`), e `winner_pred` é ignorado em palpites não-empate. Os campos de
pontos usados são os `knockout_*` (35/25/21/17/14).

O Tipo 2 ("palpite progressivo") deve passar a pontuar o mata-mata pelo **classificado**
(identidade do time que avança), não pela posição. O Tipo 1 e a fase de grupos ficam
inalterados.

## Escopo

- Altera **apenas** `pool_type == POOL_TYPE_2` na fase de mata-mata.
- Tipo 1 (mata-mata posicional) e fase de grupos (ambos os tipos): **sem mudança**.
- Sem bônus de classificado (`knockout_team_advancement_bonus`) — Tipo 2 nunca teve, e
  continua sem.

## Regra (gate = classificado)

Para cada palpite de mata-mata:

- `predicted_advancing` = time que **o seu chaveamento** classifica nesta partida:
  - palpite empate → `winner_pred` explícito;
  - palpite não-empate → o time que **você projetou no lado vencedor** do seu chaveamento.
- `real_advancing` = `match.winner` (quem avançou de fato, mesmo via prorrogação/pênaltis).
- Se `predicted_advancing != real_advancing` → **0 pontos**. Cobre:
  - errar o classificado (ex.2, ex.4);
  - placar exato com classificado errado (ex.4 → 0, sem consolação);
  - o time que você classificou para esta partida foi eliminado numa fase anterior e nem
    chegou ao jogo real → impossível bater → 0.
- Se `predicted_advancing == real_advancing` → pontua pelo placar (posicional, valores de
  mata-mata).

## Tabela de pontos (campos `knockout_*`, com gate em classificado correto)

Resultado real **não** empate — ordem de prioridade (primeiro que bater vale):

| Critério (placar posicional) | Campo de config                       | Default |
| ---------------------------- | ------------------------------------- | ------- |
| Placar exato                 | `knockout_exact_and_advancing`        | 35      |
| Gols do classificado         | `knockout_advancing_and_winner_goals` | 25      |
| Diferença de gols            | `knockout_advancing_and_diff`         | 21      |
| Gols do eliminado            | `knockout_advancing_and_loser_goals`  | 17      |
| Só o classificado            | `knockout_advancing_only`             | 14      |

Resultado real **é** empate (decidido nos pênaltis, classificado correto):

| Critério                                   | Campo                          | Default |
| ------------------------------------------ | ------------------------------ | ------- |
| Placar exato                               | `knockout_exact_and_advancing` | 35      |
| Mesma diferença (ambos empate, ex 0×0/1×1) | `knockout_advancing_and_diff`  | 21      |
| Senão                                      | `knockout_advancing_only`      | 14      |

Espelha a semântica de empate da fase de grupos (palpite de empate com diferença batendo
cai na faixa de "diferença"). Aprovado na revisão de design.

`knockout_exact_wrong_advancing` (10) **não** é usado no Tipo 2 — placar exato com
classificado errado é 0 (ex.4).

### Exemplos de validação (real: Brasil 2×1 Holanda, Brasil classifica)

| Palpite                       | predicted_advancing | Pontos | Motivo                                         |
| ----------------------------- | ------------------- | ------ | ---------------------------------------------- |
| Brasil 3×1 Holanda            | Brasil              | 17     | classificado certo + gols do eliminado (1)     |
| Brasil 0×1 Holanda            | Holanda             | 0      | classificado errado                            |
| Brasil 2×1 Japão              | Brasil              | 35     | classificado certo + placar exato (posicional) |
| Marrocos 2×1 Holanda          | Marrocos            | 0      | classificado errado (apesar do placar exato)   |
| Brasil 0×0 Japão (Brasil cls) | Brasil              | 14     | classificado certo, placar não bate            |

## Mecanismo

### `scoring.py` — mantém dependências leves

```
calculate_bet_points(bet, scoring_config, pool_type=None, predicted_advancing_id=None)
```

- Novo branch para `pool_type == POOL_TYPE_2` + fase de mata-mata.
- O scoring **não** resolve projeção — recebe `predicted_advancing_id` pronto do chamador.
  Mantém `scoring.py` sem importar `context_builder` (evita ciclo de import).
- Tipo 1 e grupos: caminho atual, `predicted_advancing_id` ignorado.
- Flags de retorno (`exact_score`, `advancing_correct`, `advancing_goals_correct`,
  `diff_correct`, `eliminated_goals_correct`) preenchidas coerentes com a faixa batida,
  para alimentar `PoolBetScore` e os contratos de ranking existentes. Quando classificado
  errado: `advancing_correct=False` e demais flags `False` (mas `exact_score` reflete o
  placar de fato, como no comportamento atual).

### Resolver do classificado projetado

`resolve_knockout_match_teams` (`context_builder.py:427`) já faz o walk do chaveamento por
`match_number`, resolvendo os times de cada partida e inferindo o classificado
(`_infer_advancing_team` + cascata `winners_map`). Refatorar para **também** expor
`{match_id: advancing_team_id}` — sem duplicar a lógica do walk.

Opção preferida: a função passa a devolver os dois mapas (times por partida + classificado
por partida), ou um helper irmão `resolve_knockout_advancing_by_match` que reaproveita o
mesmo walk. A escolha exata fica para o plano de implementação; requisito é **não duplicar**
a cascata.

### Chamadores

- `ranking.recalculate_participant_scores` — scorer canônico (worker de projeção). Resolve
  o mapa de classificados uma vez por participante (apenas quando `pool_type == 2`) e passa
  `predicted_advancing_id = advancing_map.get(bet.match_id)` por palpite. Precisa da lista
  de partidas de mata-mata da temporada ordenada por `match_number`.
- `asof_standings` — recálculo histórico (gráficos/histórico de ranking). Mesmo tratamento,
  respeitando o corte temporal as-of. Sem isso, os pontos históricos de mata-mata do Tipo 2
  ficariam posicionais (errados).
- `diagnose_dashboard` (management command de diagnóstico) — passar
  `predicted_advancing_id` para o recálculo fresco bater com o armazenado.

## Testes (TDD)

Novos testes de mata-mata Tipo 2 cobrindo:

- Os 5 exemplos da tabela acima (17 / 0 / 35 / 0 / 14).
- Empate real decidido nos pênaltis: exato (35), diferença (21), só classificado (14).
- Classificado correto em fase R16+ via time projetado (não o time real do slot).
- Time projetado eliminado antes do jogo real → 0.

Dois testes existentes codificam o comportamento **antigo** do Tipo 2 e vão **inverter**:

- `test_knockout_non_draw_exact_score` (test_pool.py:1497) — hoje assume "winner_pred
  ignorado, placar exato implica classificado certo → 35". Com a nova regra, `winner_pred`
  (=2) ≠ real winner (=1) → **0**. Reescrever.
- `test_knockout_winner_pred_ignored_in_non_draw` (test_pool.py:1570) — r2 (Tipo 2) hoje
  espera 25 com winner_pred=2 e real winner=1. Nova regra → **0**. Reescrever só o ramo
  Tipo 2; o ramo Tipo 1 (r1) continua 25.

Os demais testes de mata-mata usam `pool_type` default (Tipo 1 posicional) e seguem verdes.

Novos testes passam `predicted_advancing_id` direto para `calculate_bet_points` (o helper
`_make_knockout_bet` cria `SimpleNamespace` sem projeção real), isolando o scoring do
resolver. A integração do resolver (`ranking`/`asof_standings`) ganha cobertura própria.

## Fora de escopo

- Nenhuma mudança em campos de config / migração (reusa os `knockout_*` existentes).
- Sem mudança em UI de palpite, fase de grupos, bônus, ou Tipo 1.
