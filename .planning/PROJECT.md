# PenniniCup

## What This Is

Plataforma privada de bolão para a Copa do Mundo, com acesso por convite (token), pagamento de taxa de entrada via PIX (Mercado Pago) e pontuação automática baseada nos resultados reais das partidas. Voltada para grupos de amigos e família. Copa do Mundo 2026 é o evento-alvo imediato.

## Core Value

Participantes conseguem registrar palpites facilmente no celular e acompanhar o ranking em tempo real durante a Copa.

## Requirements

### Validated

- ✓ Acesso privado via token de convite — existente
- ✓ Cadastro de usuário com e-mail verificado — existente
- ✓ Pagamento de entrada via PIX (Mercado Pago) — existente
- ✓ Registro de palpites para fase de grupos e mata-mata — existente
- ✓ Pontuação automática baseada em resultados reais (PoolBet scoring) — existente
- ✓ Ranking de participantes com critérios de desempate — existente
- ✓ Projeção de classificação de grupos (placeholders de mata-mata) — existente
- ✓ Sincronização de partidas via FIFA API — existente
- ✓ Painel admin Django para gestão de temporada/partidas — existente
- ✓ Sistema de e-mail transacional (verificação, reset de senha) — existente

### Active

- [ ] Fluxo de palpites (fase de grupos + mata-mata) reformulado para mobile-first
- [ ] Feedback visual claro nos palpites: salvo, pendente, bloqueado por fase travada
- [ ] Onboarding melhorado: fluxo token → cadastro → pagamento → palpites sem confusão
- [ ] Tela de ranking com indicação clara de pontuação e posição relativa
- [ ] Cobertura de testes para `scoring.py` (edge cases: empate, gols únicos, bônus inativo)
- [ ] Cobertura de testes para `rules.py` (normalize_stage_key: variantes PT/EN da FIFA API)
- [ ] Cobertura de testes para `context_builder.py` (lógica de montagem de contexto de view)
- [ ] Configurar `coverage` no Makefile com meta mínima
- [ ] Ativar Ruff rule `G` (logging format) e corrigir inconsistências de f-string vs %s

### Out of Scope

- Notificações push/WhatsApp — não priorizado para Copa 2026
- Múltiplos bolões independentes — arquitetura atual não suporta; seria reescrita significativa
- Suporte a torneios além da Copa do Mundo — fora do foco atual
- OAuth / login social — público é fechado (convite), não justifica

## Context

- Codebase Django 4.x + Python 3.12, Ruff, pre-commit hooks configurados
- Pagamento via Mercado Pago SDK; webhooks com validação de assinatura + idempotência
- Ruff configurado com E, F, I, B, UP, SIM, PLE — sem `G` (logging format)
- 145 testes existentes; `accounts` bem coberto (61 testes); gaps em `scoring.py`, `rules.py`, `context_builder.py`
- Sem ferramenta de cobertura configurada; Django test runner (não pytest runner)
- setUp de testes muito verboso (40-60 linhas cada) — sem factory library
- Copa do Mundo 2026 começa em junho de 2026; prazo urgente

## Constraints

- **Tech stack**: Django/Python — sem reescrita de backend
- **Tempo**: Urgente — Copa começa junho 2026; roadmap deve ser executável rapidamente
- **Usuários**: Grupo fechado de amigos/família — sem necessidade de escala massiva
- **Pagamento**: Mercado Pago fixo — não trocar gateway

## Key Decisions

| Decision                                        | Rationale                                                 | Outcome   |
| ----------------------------------------------- | --------------------------------------------------------- | --------- |
| Mobile-first para fluxo de palpites             | Usuários acessam principalmente pelo celular              | — Pending |
| Priorizar fluxo de palpites antes do onboarding | Core value é registrar palpites; onboarding é pontual     | — Pending |
| Testes antes de UI                              | Qualidade base evita regressões durante refactoring de UI | — Pending |
| Não usar factory_boy por enquanto               | Scope controlado; factory lib é melhoria incremental      | — Pending |

## Evolution

Este documento evolui nas transições de fase e marcos.

**Após cada fase** (via `/gsd-transition`):

1. Requisitos invalidados? → mover para Out of Scope com motivo
1. Requisitos validados? → mover para Validated com referência de fase
1. Novos requisitos emergiram? → adicionar em Active
1. Decisões a registrar? → adicionar em Key Decisions
1. "What This Is" ainda preciso? → atualizar se desviou

**Após cada milestone** (via `/gsd-complete-milestone`):

1. Revisão completa de todas as seções
1. Verificar Core Value — ainda é a prioridade certa?
1. Auditar Out of Scope — motivos ainda válidos?
1. Atualizar Context com estado atual

______________________________________________________________________

*Last updated: 2026-05-05 after initialization*
