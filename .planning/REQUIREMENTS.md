# Requirements

## Functional Requirements

### FR-01: Palpites Fase de Grupos

- Participantes palpitam todos os jogos da fase de grupos antes do início da Copa
- Bloqueio automático ao início do primeiro jogo
- Palpites: placar de cada jogo

### FR-02: Pontuação Fase de Grupos

- Acertar vencedor/empate: +6 pts
- Acertar placar exato: +4 pts
- Acertar placar de um time: +2 pts
- Máximo por jogo: 10 pts

### FR-03: Palpites Fase Mata-Mata

- Confrontos definidos pelos resultados reais da fase de grupos
- Palpites: classificado + placar do tempo regulamentar
- Janela fecha antes do primeiro jogo do mata-mata

### FR-04: Pontuação Fase Mata-Mata

- Acertar classificado: +8 pts
- Acertar placar exato: +6 pts
- Acertar placar de um time: +2 pts
- Máximo por jogo: 14 pts
- Placar considerado: tempo regulamentar (sem pênaltis)

### FR-05: Bônus de Pontos

- Campeão: +50 pts
- Vice-campeão: +30 pts
- Terceiro lugar: +20 pts
- Artilheiro: +50 pts
- Máximo de bônus: 150 pts

### FR-06: Critérios de Desempate (em ordem)

1. Acerto do campeão
1. Maior número de placares exatos
1. Acerto do artilheiro
1. Maior número de vencedores/empates acertados
1. Maior pontuação no mata-mata
1. Maior pontuação na fase de grupos
1. Sorteio

### FR-07: Ranking em Tempo Real

- Atualizado após cada rodada
- Pontuação acumulada dos participantes

### FR-08: Onboarding

- Acesso via token de convite
- Fluxo: token → cadastro → pagamento PIX → palpites

### FR-09: Fluxo de Palpites Mobile-First

- Interface reformulada para mobile
- Feedback visual: salvo, pendente, bloqueado

## Non-Functional Requirements

### NFR-01: Timezone

- America/Sao_Paulo (Horário de Brasília)
- USE_TZ = True, datetimes aware

### NFR-02: Stack

- Django 6, Python 3.12, TailwindCSS, PostgreSQL
- Sem reescrita de backend

### NFR-03: Pagamento

- Mercado Pago (PIX) — não trocar gateway
- Webhooks com validação de assinatura + idempotência

### NFR-04: Qualidade

- Pre-commit: Ruff (E, F, I, B, UP, SIM, PLE), gitleaks, prettier
- ATOMIC_REQUESTS=True
- Cobertura de testes com meta mínima configurada

### NFR-05: Escala

- Grupo fechado de amigos/família
- Sem necessidade de escala massiva

## Out of Scope

- Notificações push/WhatsApp
- Múltiplos bolões independentes
- Suporte a torneios além da Copa do Mundo
- OAuth / login social
