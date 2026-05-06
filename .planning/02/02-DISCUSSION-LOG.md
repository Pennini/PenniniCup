# Phase 2: Palpites Mobile-First - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-05-06
**Phase:** 2-Palpites Mobile-First
**Areas discussed:** Save flow, Navegação/scroll, Layout mobile, Feedback visual

______________________________________________________________________

## Save Flow

| Option                            | Description                                      | Selected |
| --------------------------------- | ------------------------------------------------ | -------- |
| Auto-save por campo (AJAX onblur) | Salva via save_bet ao sair do campo. Zero botão. |          |
| Botão por card                    | Cada card tem próprio botão Salvar.              |          |
| Botão único no rodapé (bulk save) | Fluxo atual, apenas melhora acesso mobile.       | ✓        |

**User's choice:** Manter bulk save — sem auto-save AJAX. Motivação: minimizar queries e custo de infra.

**Revisão mid-discussion:** Usuário inicialmente respondeu "auto-save" mas ao ver pergunta sobre `beforeunload` clarificou preferência por manter botão explícito.

**Notes:** Botão Salvar deve aparecer no centro da top bar mobile (contextual), destacado em laranja quando há dirty state. Reutilizar lógica `dirtyCards` JS existente.

______________________________________________________________________

## Navegação/Scroll

| Option                                        | Description                                   | Selected |
| --------------------------------------------- | --------------------------------------------- | -------- |
| Tabs por fase + scroll dentro                 | Tab Grupos/Mata-mata. Scroll contínuo dentro. | parcial  |
| Tabs por fase + jump por grupo                | Igual + âncoras Grupo A/B/C.                  |          |
| Scroll único com sticky headers               | Tudo em scroll, cabeçalho de grupo fixo.      |          |
| Agrupamento configurável + barra de progresso | Toggle Por Data / Por Grupo + barra progresso | ✓        |

**User's choice:** Toggle de agrupamento abaixo do toggle de fase existente, ao lado da barra de progresso.

**Notes:**

- Barra de progresso: `palpites salvos / total` da fase ativa. "Salvo" = salvo no servidor.
- Toggle: "Por Data" (padrão) | "Por Grupo".
- Por Data: blocos por data real das partidas (dinâmico do banco).
- Padrão: Por Data — mostra jogos do dia primeiro.
- Não persiste entre sessões (reseta para Por Data).
- Mata-mata: sempre por fase (Oitavas/Quartas/Semi/Final) — toggle não se aplica.

______________________________________________________________________

## Layout Mobile

| Option                                         | Description                                   | Selected |
| ---------------------------------------------- | --------------------------------------------- | -------- |
| Linha única compacta                           | Time A [00] x [00] Time B.                    |          |
| Layout centrado: Time A — \[00\]:[00] — Time B | Placar centralizado entre times.              | ✓        |
| Manter layout atual (adaptado)                 | ~4 linhas por card, só ajustar padding/fonte. |          |

**User's choice:** Layout centrado — `Time A  [00] : [00]  Time B` em uma linha.

**Notes:**

- Card mata-mata com empate: segunda linha com select "Classificado" abaixo do placar.
- Botão Salvar: no centro da top bar mobile (contextual, só quando há pendente).
- Inputs: mínimo 44px para toque confortável.
- Card artilheiro: manter posição e layout atual.
- Badge Aberto/Fechado: manter pills atuais.

______________________________________________________________________

## Feedback Visual

| Option                    | Description                        | Selected |
| ------------------------- | ---------------------------------- | -------- |
| Toast de sucesso          | "Palpites salvos!" aparece e some. | ✓        |
| Botão muda para "✓ Salvo" | Feedback inline no botão por 2s.   |          |

| Option                   | Description                           | Selected |
| ------------------------ | ------------------------------------- | -------- |
| Pendente: botão laranja  | Botão muda cor quando há dirty state. | ✓        |
| Pendente: badge por card | Pill "Pendente" por card alterado.    |          |

| Option                                       | Description                          | Selected |
| -------------------------------------------- | ------------------------------------ | -------- |
| Bloqueado: inputs disabled + badge "Fechado" | Comportamento atual mantido.         | ✓        |
| Bloqueado: card cinza/opaco + badge          | Card inteiro com opacidade reduzida. |          |

| Option                               | Description                                     | Selected |
| ------------------------------------ | ----------------------------------------------- | -------- |
| Erro: toast + badge vermelho no card | Toast global + badge inline no card que falhou. | ✓        |
| Erro: apenas toast global            | Mensagem no topo, sem indicação por card.       |          |

| Option                                    | Description                 | Selected |
| ----------------------------------------- | --------------------------- | -------- |
| Barra de progresso: barra gráfica + texto | `██████░░  32/48 palpites`  | ✓        |
| Só número                                 | `32 de 48 jogos palpitados` |          |

______________________________________________________________________

## Claude's Discretion

- Implementação interna do toast (vanilla JS vs Django messages).
- Ponto exato de injeção HTML do botão Salvar na top bar mobile.
- Breakpoints Tailwind para cards compactos.

## Deferred Ideas

Nenhuma ideia fora do escopo da fase surgiu.
