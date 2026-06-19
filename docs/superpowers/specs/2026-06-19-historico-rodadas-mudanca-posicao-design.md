# Histórico de rodadas e mudança de posição no ranking

Data: 2026-06-19
Branch: `feat/palpites-carrossel`

## Objetivo

Na aba de ranking do bolão (`src/rankings/templates/rankings/pool_dashboard.html`),
mostrar ao lado da posição de cada participante quantas posições ele subiu ou
desceu desde a rodada anterior.

Para sustentar isso (e usos futuros), criar uma **tabela de histórico** que guarda
os pontos e os dados de ranking de cada participante ao longo das rodadas do bolão.

## Contexto do código atual

- Não existe conceito de "rodada" no modelo `Match` (só `match_number`,
  `match_date_brasilia`, `stage`/fase e `status`).
- Não existe histórico de posição/pontos. O leaderboard é calculado **ao vivo**
  por `build_pool_leaderboard` (`src/rankings/services/leaderboard.py`),
  ordenando `PoolParticipant` por `total_points` + critérios de desempate.
- Pontuação agregada (`total_points`, `group_points`, etc.) é recalculada de forma
  **síncrona** por `recalculate_match_scores(match)`
  (`src/pool/services/ranking.py`), chamada pelo signal `post_save` de `Match`
  (`src/football/signals.py`) quando campos de placar mudam
  (`_SCORE_RELEVANT_FIELDS`).
- `Match.STATUS_FINISHED == 0` indica jogo encerrado.

## Decisões

- **Granularidade:** 1 rodada por jogo encerrado. Cada `Match` que entra em
  `STATUS_FINISHED` gera uma rodada (uma linha de histórico por participante dos
  bolões afetados).
- **Escopo do badge:** todas as linhas do ranking, mas o badge só aparece para
  quem **realmente mudou** de posição (delta ≠ 0). Sem mudança → nada.
- **Sem baseline** (primeira rodada / participante sem registro anterior): nada.
- **Formato:** `▲N` verde (subiu) / `▼N` vermelho (desceu). 0 ou None → nada.
- **Correção de placar** de um jogo já encerrado: faz upsert na rodada existente
  daquele match (mantém o `round_index` original, corrige pontos/posição). Não
  cria rodada duplicada.

## Abordagem escolhida

Tabela de histórico (`PoolRankingHistory`) gravando o estado **pós-recálculo** a
cada jogo encerrado. O badge compara as duas rodadas mais recentes do bolão.

Abordagens descartadas:

- **2 campos `previous_position` no `PoolParticipant`** — mínimo, mas guarda só o
  último delta e não serve para histórico/gráficos futuros.
- **Reconstruir o leaderboard anterior na hora** (excluindo o último jogo) —
  frágil (bônus, desempate) e caro por render.

## Design

### 1. Modelo `PoolRankingHistory` (`src/rankings/models.py`)

Campos:

- `pool` (FK `Pool`, `related_name="ranking_history"`)
- `participant` (FK `PoolParticipant`, `related_name="ranking_history"`)
- `match` (FK `football.Match`) — o jogo que fechou a rodada
- `round_index` (PositiveIntegerField) — sequência da rodada por bolão
- `position` (PositiveIntegerField) — posição do participante naquela rodada
- Dados de ranking (cópia do estado pós-recálculo):
  - `total_points`, `group_points`, `knockout_points`
  - `exact_score_hits`, `advancing_hits`
  - `champion_hit` (bool), `top_scorer_hit` (bool)
- `created_at` (auto_now_add)

Meta:

- `unique_together = ("pool", "participant", "match")` — garante upsert por
  correção.
- `indexes`: `("pool", "round_index")` para buscar a rodada anterior rápido.
- `ordering = ["pool", "round_index", "position"]`.

Migration nova em `src/rankings/migrations/`.

### 2. Serviço de snapshot (`src/rankings/services/position_snapshot.py`)

`snapshot_round_for_match(match)`:

1. Retorna cedo se `match.status != Match.STATUS_FINISHED`.
1. Bolões afetados = `Pool` ativos da `match.season` que tenham participante com
   aposta nesse jogo:
   `Pool.objects.filter(season=match.season, is_active=True, participants__bets__match=match).distinct()`.
1. Para cada bolão:
   - Determina `round_index`:
     - Se já existe histórico desse `match` no bolão → reusa o `round_index`
       daquela rodada (correção).
     - Senão → `max(round_index do bolão) + 1` (ou 1 se vazio).
   - Calcula o leaderboard atual via `build_pool_leaderboard(pool=pool)`.
   - Faz upsert de uma linha por participante com `position` + dados de ranking,
     usando `bulk_create(..., update_conflicts=True, unique_fields=["pool", "participant", "match"], update_fields=[...])`.

Função isolada e testável: entra um `match`, grava/atualiza rodadas. Depende só de
`build_pool_leaderboard` e dos modelos.

### 3. Hook no signal (`src/football/signals.py`)

Dentro de `recalculate_pool_data_after_match_save`, quando `score_should_recalc`:

1. `recalculate_match_scores(match=instance)` (já existe — atualiza os agregados).
1. **Depois**, se `instance.status == Match.STATUS_FINISHED`:
   `snapshot_round_for_match(instance)`.

A ordem importa: snapshot **depois** do recálculo grava o estado já atualizado
(posições/pontos pós-jogo) como a rodada. Erros do snapshot são capturados e
logados sem quebrar o save (mesmo padrão dos outros blocos do signal).

### 4. Cálculo do delta (`src/rankings/services/leaderboard.py`)

- `RankingRow` ganha o campo `movement: int | None`.
- Em `build_pool_leaderboard`, após montar as linhas:
  - Descobre a **rodada anterior** do bolão = segundo maior `round_index` distinto
    em `PoolRankingHistory` (o maior == estado atual). Se houver < 2 rodadas →
    todos `movement = None`.
  - Monta `{participant_id: position}` daquela rodada anterior.
  - Para cada linha: se o participante tem posição anterior →
    `movement = posicao_anterior - row.position`; senão `movement = None`.
- Positivo = subiu, negativo = desceu, 0 = sem mudança.

Observação: a posição "atual" usada é a posição ao vivo da própria
`build_pool_leaderboard` (igual à última rodada gravada enquanto não há novo jogo).

### 5. Template (`src/rankings/templates/rankings/pool_dashboard.html`)

Ao lado do `#{{ row.position }}` no card mobile e na linha da tabela desktop,
renderiza o badge:

- `row.movement > 0` → `▲{{ row.movement }}` em verde (ex. `text-emerald-400`).
- `row.movement < 0` → `▼{{ row.movement|cut:"-" }}` (valor absoluto) em vermelho
  (ex. `text-red-400`).
- `row.movement == 0` ou `None` → não renderiza nada.

Sem alterar o layout existente além de acrescentar o badge.

## Plano de testes (TDD)

`src/rankings/tests.py`:

1. **Modelo/migration:** `PoolRankingHistory` cria e respeita `unique_together`.
1. **Serviço `snapshot_round_for_match`:**
   - Jogo encerrado grava 1 linha por participante do bolão afetado, com
     `position` e dados corretos.
   - Jogo `STATUS_SCHEDULED` não grava nada.
   - Só bolões com aposta no jogo são afetados.
   - Re-snapshot do mesmo match (correção) atualiza a linha existente e mantém o
     `round_index` (não cria rodada nova).
   - Segundo jogo encerrado incrementa `round_index`.
1. **Signal:** salvar um `Match` com placar e `STATUS_FINISHED` dispara o snapshot
   (depois do recálculo); `STATUS_SCHEDULED` não.
1. **`build_pool_leaderboard` / `RankingRow.movement`:**
   - Sobe → `movement` positivo; desce → negativo; igual → 0.
   - Participante sem rodada anterior → `None`.
   - < 2 rodadas no bolão → todos `None`.
1. **Template:** renderiza `▲`/`▼` só quando `movement != 0`; nada quando 0/None.

## Fora de escopo

- Gráficos/visualizações de evolução (a tabela habilita, mas não é entregue agora).
- Backfill de histórico para jogos já encerrados antes desta feature.
- Agrupamento de rodada por dia/fase (decidido: 1 rodada por jogo).
