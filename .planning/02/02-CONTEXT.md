# Phase 2: Palpites Mobile-First - Context

**Gathered:** 2026-05-06
**Status:** Ready for planning

<domain>
## Phase Boundary

Reformular o fluxo de palpites (fase de grupos + mata-mata) para uso principal no celular. Inclui: layout de cards compacto, navegação com agrupamento por data/grupo, barra de progresso, e feedback visual claro (salvo/pendente/bloqueado).

</domain>

<decisions>
## Implementation Decisions

### Save Flow

- **D-01:** Manter bulk save (`save_bets_bulk` POST) — sem auto-save AJAX por campo. Minimizar queries e custo de infra.
- **D-02:** Botão "Salvar palpites" aparece no **centro da top bar mobile** (contextual) — visível e destacado (cor laranja/primária) apenas quando há alterações pendentes (dirty state). Quando não há pendências: oculto ou neutro.
- **D-03:** Manter lógica de `beforeunload` warning para campos sujos não salvos.
- **D-04:** Dirty state já existe no JS atual (`dirtyCards` set) — reutilizar para controlar visibilidade/cor do botão na top bar.

### Navegação e Agrupamento

- **D-05:** Abaixo do toggle de fase existente (Grupos/Mata-mata/Classificação), adicionar segunda camada: **barra de progresso + toggle de agrupamento** lado a lado.
- **D-06:** Barra de progresso mostra `jogos palpitados / total` da fase ativa. "Palpitado" = palpite salvo no servidor (not just input preenchido).
- **D-07:** Toggle de agrupamento: **"Por Data" | "Por Grupo"** — dois modos de visualização dos cards.
- **D-08:** Padrão ao abrir: **Por Data** (mostra jogos mais próximos primeiro — útil durante a Copa).
- **D-09:** Agrupamento por data: blocos com cabeçalho de data, dinâmico com base nas datas reais das partidas no banco.
- **D-10:** Agrupamento por grupo: blocos "Grupo A", "Grupo B", etc.
- **D-11:** Toggle de agrupamento **não persiste** entre sessões — reseta para Por Data a cada visita.
- **D-12:** Fase mata-mata: agrupamento **sempre por fase** (Oitavas / Quartas / Semi / Final) — toggle de agrupamento não se aplica ao mata-mata.

### Layout de Cards Mobile

- **D-13:** Layout centrado horizontal: `Time A  [00] : [00]  Time B` em uma linha. Legível em 320px.
- **D-14:** Cabeçalho do card: fase/grupo + número do jogo + badge de status (Aberto/Fechado) — mesma linha superior.
- **D-15:** Card mata-mata com empate: segunda linha abaixo do placar com select "Classificado: [Time ▾]" — comportamento JS atual mantido, apenas estilo mobile.
- **D-16:** Inputs de placar: tamanho mínimo 44px para toque confortável no mobile.
- **D-17:** Card do artilheiro da Copa: manter posição atual (topo da lista), sem alteração de layout.
- **D-18:** Badge de status (Aberto/Fechado) — manter pill atual, apenas garantir legibilidade mobile.

### Feedback Visual

- **D-19:** **Salvo:** toast no topo com "Palpites salvos!" por alguns segundos. Usar Django messages ou JS toast.
- **D-20:** **Pendente:** botão Salvar muda de cor neutra → **laranja** quando há dirty state. Sem badge por card.
- **D-21:** **Bloqueado (fase travada):** inputs desabilitados + badge "Fechado" (pill border-red-400) no card — comportamento atual mantido e garantido no mobile.
- **D-22:** **Erro de save** (fase bloqueada após deadline, erro de rede): toast de erro global + badge vermelho inline no card que falhou.

### Claude's Discretion

- Implementação interna do toast (vanilla JS vs reusar Django messages renderizados) — escolher o mais simples.
- Exata posição/HTML da top bar mobile para o botão Salvar contextual (verificar `top_nav.html` + `bottom_nav.html` para ponto de injeção).
- Breakpoints Tailwind exatos para layout compacto dos cards.

</decisions>

\<canonical_refs>

## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Requirements

- `.planning/REQUIREMENTS.md` — FR-01 (Palpites Fase de Grupos), FR-03 (Palpites Mata-mata), FR-09 (Mobile-First)
- `.planning/ROADMAP.md` — Phase 2 deliverables

### Templates existentes (ponto de partida)

- `src/pool/templates/pool/detail.html` — página principal de palpites (497 linhas). MUST read completo antes de planejar.
- `src/templates/components/bottom_nav.html` — sidebar mobile (slide-out direita). Contém o toggle hamburguer que abre o menu.
- `src/templates/components/top_nav.html` — top bar (hidden lg:flex). Ponto de injeção do botão Salvar contextual mobile.
- `src/templates/base.html` — estrutura base, checar slot/bloco disponível para injeção contextual.

### Views e endpoints

- `src/pool/views.py` — `save_bets_bulk` (bulk POST), `save_bet` (AJAX individual), `build_pool_participant_view_context`
- `src/pool/services/context_builder.py` — montagem do contexto da view (group_rows, knockout_rows, can_bet, etc.)

### Padrões e convenções

- `.planning/codebase/CONVENTIONS.md` — convenções do projeto
- `.planning/codebase/TESTING.md` — padrões de teste

\</canonical_refs>

\<code_context>

## Existing Code Insights

### Reusable Assets

- **Dirty state JS (`dirtyCards` Set):** já detecta alterações não salvas em `detail.html`. Reutilizar para controlar visibilidade/cor do botão Salvar na top bar.
- **`save_bet` endpoint:** AJAX individual por partida — disponível mas não usado no fluxo principal (D-01 mantém bulk save).
- **Tab toggle existente (`pool_bet_toggle_three_tabs.html`):** toggle Grupos/Mata-mata/Classificação. Novo toggle de agrupamento fica abaixo, não substitui.
- **Badge pills (border-red-400, border-emerald-400):** padrão visual de status já estabelecido. Reutilizar para Fechado/Aberto.
- **`group_rows` / `knockout_rows`:** contexto já montado por `build_pool_participant_view_context`. Reagrupar no template (por data ou por grupo) sem nova query — ordenação/agrupamento no Python ou JS.

### Established Patterns

- **TailwindCSS dark theme:** `bg-neutral-900`, `border-neutral-700`, `text-neutral-400`. Manter paleta.
- **Cor primária de ação:** `bg-orange-500` / `text-orange-200` — usar para botão Salvar com pendências.
- **`ATOMIC_REQUESTS=True`:** toda view em transação. `save_bets_bulk` já usa `@transaction.atomic`.
- **Django messages:** framework de mensagens já instalado e usado (ex: `messages.success`, `messages.error` em `join_pool`). Pode ser base para toast de sucesso/erro.

### Integration Points

- Top bar mobile (`top_nav.html` / `base.html`): injetar botão Salvar contextual via bloco de template ou JavaScript.
- Barra de progresso: precisa de contagem de bets salvos na fase ativa — verificar se `build_pool_participant_view_context` já expõe esse dado ou precisa de campo extra.
- Toggle de agrupamento: lógica de reagrupamento de `group_rows` por data vs grupo — implementar no template (Jinja/Django template filters) ou passar dados pré-agrupados da view.

\</code_context>

<specifics>
## Specific Ideas

- Layout visual dos cards: `Time A  [00] : [00]  Time B` — linha única, placar centralizado.
- Top bar mobile: `[ ← ]  [ Salvar palpites ]  [ ☰ ]` — botão no centro, aparece laranja quando há pendente.
- Barra de progresso + toggle: `██████░░░░  32/48 palpites  [ Por Data | Por Grupo ]` — abaixo do toggle de fase.

</specifics>

<deferred>
## Deferred Ideas

- Nenhuma ideia de escopo expandido surgiu durante a discussão.

</deferred>

______________________________________________________________________

*Phase: 2-Palpites Mobile-First*
*Context gathered: 2026-05-06*
