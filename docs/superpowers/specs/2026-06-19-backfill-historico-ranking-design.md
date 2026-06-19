# Reprocessamento de histórico de ranking (backfill as-of) — Design

## Contexto

A Copa já está em andamento e a tabela `PoolRankingHistory` (criada na feature de
movimento de posição) está vazia: o snapshot por jogo só passou a rodar depois que
os jogos já tinham placar, então não há histórico das rodadas anteriores. Sem esse
histórico, a aba de ranking não mostra o andamento da performance dos usuários nem o
badge de mudança de posição.

Precisamos de uma forma de **reprocessar/reconstruir** o histórico a partir dos jogos
já encerrados, por bolão, disparável pelo admin e por linha de comando.

## Objetivo

Reconstruir `PoolRankingHistory` de forma **fiel ao que cada rodada realmente
mostrava** (reconstrução "as-of"), para bolões em andamento, populando a aba de
ranking e o badge de movimento.

## Definições

- **Jogo encerrado**: `home_score` e `away_score` não nulos.
- **Rodada**: cada jogo encerrado da season do bolão em que **≥1 participante ativo
  apostou**, ordenado por `(match_date_utc, match_number, id)`. A rodada *k* é o
  prefixo dos *k* primeiros jogos encerrados nessa ordem.
- **As-of da rodada *k***: standings calculadas contando **apenas** os jogos do
  prefixo até *k* (conjunto `allowed_match_ids`).

## Arquitetura

Núcleo = um **service de backfill** que monta, rodada a rodada, o conjunto de jogos
permitidos e delega o cálculo das standings a um **agregador as-of isolado**. Admin e
comando de gestão apenas chamam o service.

Decisão (escolha do usuário): o agregador as-of é um **módulo isolado**, que reusa o
cálculo por aposta existente (`calculate_bet_points`) mas reimplementa a agregação e
os bônus com gating temporal. Não toca o caminho de pontuação live, eliminando risco
de regressão no fluxo de produção.

## Componentes

### 1. Agregador as-of isolado — `src/pool/services/asof_standings.py`

Função `compute_asof_standings(pool, allowed_match_ids, scoring_config, official_result)`
→ `list[AsOfStanding]` (um por participante ativo elegível), **sem tocar o banco**.

`AsOfStanding` é um dataclass com os campos usados na ordenação e no snapshot:
`participant`, `total_points`, `group_points`, `knockout_points`, `exact_score_hits`,
`advancing_hits`, `champion_hit`, `top_scorer_hit`.

Regras de gating (espelham `recalculate_participant_scores`, restritas a
`allowed_match_ids`):

- **Per-bet**: soma `calculate_bet_points(bet, ...)` apenas para bets cujo
  `match_id ∈ allowed_match_ids`. Bets fora do conjunto são ignorados.
- **Avanço de time (tipo 1)**: vencedores de cada stage considerados apenas entre
  matches `∈ allowed_match_ids`.
- **Bônus de classificados de grupo**: aplicado só quando a fase de grupos está
  encerrada *dentro do conjunto* — isto é, todos os jogos de grupo da season
  ∈ `allowed_match_ids`. Caso contrário 0.
- **Bônus de pódio (campeão/vice/3º)**: cada um aplicado só se o jogo correspondente
  (final / disputa de 3º) ∈ `allowed_match_ids` e encerrado. O pódio é derivado dos
  resultados desses jogos no conjunto, não do `OfficialResult` salvo (que reflete o
  estado final).
- **Bônus de artilheiro**: aplicado com base no `official_result` atual (o artilheiro
  não tem jogo único associado); aceitável, pois só muda no fim do torneio.

### 2. Service de backfill — `src/rankings/services/history_backfill.py`

Função `backfill_pool_history(pool)` → `int` (nº de rodadas gravadas):

1. Em transação: apaga `PoolRankingHistory.objects.filter(pool=pool)` (rebuild
   idempotente).
1. Monta a lista ordenada de jogos encerrados da season em que ≥1 participante ativo
   do bolão apostou.
1. Para cada rodada *k* (1-indexed), com `allowed_match_ids` = ids do prefixo até *k*:
   - `rows = compute_asof_standings(pool, allowed_match_ids, ...)`
   - ordena `rows` pela **mesma chave de desempate do leaderboard** (`_score_key`) e
     aplica os `RankingTieBreakOverride` atuais do bolão, produzindo posições 1..N
   - cria as linhas `PoolRankingHistory(pool, participant, match=jogo_k, round_index=k, position, <campos de pontuação>)`
1. `bulk_create` das linhas.

Função auxiliar `backfill_pools(pools)` → soma, para reuso por comando/admin em massa.

### 3. Admin — `src/rankings/admin.py` (ou admin do Pool)

Ação de admin "Reprocessar histórico de ranking" registrada no `ModelAdmin` do
`Pool`, chamando `backfill_pool_history` para cada bolão selecionado e exibindo
`messages.success` com a contagem de rodadas. (Botão na change-page é opcional e fica
fora do escopo mínimo; a admin action já cobre 1 ou N bolões.)

### 4. Comando de gestão — `src/rankings/management/commands/backfill_ranking_history.py`

`python -m src.manage backfill_ranking_history [--pool SLUG | --season ID | --all]`:
seleciona os bolões ativos correspondentes, chama o service, imprime contagem por
bolão. Exige exatamente um dos seletores.

## Decisões fixadas

- **Rebuild total por bolão** (apaga e reconstrói) → idempotente e re-rodável a
  qualquer momento.
- **Elegibilidade e overrides**: usa o estado atual (mesma regra de
  `eligible_participants` e `RankingTieBreakOverride` do leaderboard live).
- **Continuidade**: a última rodada do backfill (allowed = todos os jogos encerrados)
  coincide com as standings live atuais; snapshots live seguintes apenas anexam
  `round_index = max+1`.

## Tratamento de erros

- Bolão sem jogos encerrados: backfill grava 0 rodadas e retorna 0 (sem erro).
- Comando sem seletor ou com bolão inexistente: `CommandError` com mensagem clara.
- Admin action: exceção por bolão é capturada e reportada via `messages.error` sem
  abortar os demais.

## Testes

- **As-of**: jogo no meio do torneio produz pontuação parcial correta (não a final);
  bônus de grupo só aparece quando todos os jogos de grupo estão no conjunto.
- **Backfill**: cenário com 3 participantes que trocam de posição ao longo de 3
  rodadas gera `round_index` 1..3 com posições e movimento corretos.
- **Idempotência**: rodar `backfill_pool_history` duas vezes produz o mesmo conjunto
  de linhas.
- **Comando**: `--pool` chama o service e reporta contagem; sem seletor → erro.
- **Admin action**: dispara o service para os bolões selecionados.
