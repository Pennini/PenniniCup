# Roadmap

**Project:** PenniniCup
**Event:** Copa do Mundo 2026 (junho 2026)
**Status:** Active

## Phases

### Phase 1: Qualidade Base — Testes e Cobertura

**Goal:** Garantir base de testes sólida antes de refactoring de UI

**Status:** Complete ✓ (2026-05-05)

**Deliverables:**

- ✓ Cobertura de testes para `scoring.py` — 100% cobertura direta (18 métodos)
- ✓ Cobertura de testes para `rules.py` — 96% cobertura (19 métodos)
- ✓ Cobertura de testes para `context_builder.py` — 80% cobertura (43 métodos)
- ✓ `coverage` configurado no Makefile com meta mínima (fail_under=70%)

**Requirements:** FR-02, FR-04, NFR-04

______________________________________________________________________

### Phase 2: Palpites Mobile-First

**Goal:** Reformular fluxo de palpites para uso principal no celular

**Status:** Not Started

**Deliverables:**

- Fluxo de palpites (fase de grupos + mata-mata) reformulado para mobile-first
- Feedback visual claro: salvo, pendente, bloqueado por fase travada

**Requirements:** FR-01, FR-03, FR-09

______________________________________________________________________

### Phase 3: Onboarding e Ranking

**Goal:** Melhorar experiência de entrada e acompanhamento de classificação

**Status:** Not Started

**Deliverables:**

- Onboarding: fluxo token → cadastro → pagamento → palpites sem confusão
- Tela de ranking com indicação clara de pontuação e posição relativa

**Requirements:** FR-07, FR-08

______________________________________________________________________

### Phase 4: Qualidade de Código

**Goal:** Normalizar práticas de logging e qualidade de código

**Status:** Not Started

**Deliverables:**

- Ativar Ruff rule `G` (logging format)
- Corrigir inconsistências de f-string vs %s nos logs

**Requirements:** NFR-04

______________________________________________________________________

## Constraints

- Copa do Mundo 2026 começa junho 2026 — prazo urgente
- Tech stack fixo: Django/Python
- Gateway fixo: Mercado Pago
