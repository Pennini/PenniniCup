# Auditoria Técnica — PenniniCup

**Data:** 2026-04-11
**Escopo:** Backend Django completo (models, views, forms, services, urls, commands, migrations, settings, integrações, webhooks, segurança)
**Postura:** Revisão agressiva, sem suavizar críticas. Foco em riscos reais de produção.

______________________________________________________________________

# LISTA PRIORIZADA DE PROBLEMAS POR SEVERIDADE

______________________________________________________________________

## 🔴 CRÍTICO

### 1. Race condition / falta de idempotência no webhook de pagamento

**Severidade:** Crítico
**Impacto em produção:** Crédito duplicado ou processamento duplo se o Mercado Pago reenviar o webhook (comportamento padrão da plataforma). Se no futuro for adicionada lógica de crédito (ex: ativar participante automaticamente), o bug será financeiro direto.
**Evidência técnica:** `src/payments/webhooks.py` — o handler atualiza `status` e `amount_received` sem verificar se o pagamento já estava aprovado. Não há transação atômica com `select_for_update`.

```python
# webhooks.py ~linha 100
old_status = payment.status
payment.status = payment_data.get("status", "unknown")
# ...
payment.save()
```

**Causa raiz:** Nenhuma verificação de idempotência. O webhook não verifica transição de estado — apenas sobrescreve.
**Correção recomendada:**

- Adicionar `if old_status == "approved" and payment.status == "approved": return HttpResponse(status=200)` antes de qualquer lógica.
- Usar `select_for_update()` no `Payment.objects.filter()` para travar a linha durante processamento concorrente.
  **Esforço:** Baixo
  **Prioridade:** Imediato

______________________________________________________________________

### 2. `@csrf_exempt` no webhook com fallback inseguro em produção

**Severidade:** Crítico
**Impacto em produção:** Se `MERCADO_PAGO_WEBHOOK_SECRET` não estiver configurado em produção, `verify_webhook_signature` retorna `True` e aceita qualquer POST forjado. Qualquer atacante pode enviar payloads arbitrários ao endpoint.
**Evidência técnica:** `src/payments/webhooks.py`:

```python
def verify_webhook_signature(request) -> bool:
    if not settings.MERCADO_PAGO_WEBHOOK_SECRET:
        logger.warning("MERCADO_PAGO_WEBHOOK_SECRET não configurado - webhook sem validação")
        return True  # Em desenvolvimento, permite sem validação
```

**Causa raiz:** Fallback permissivo para dev pode vazar para produção se a variável de ambiente não for configurada.
**Correção recomendada:**

- Em produção (`not DEBUG`), sempre exigir assinatura válida. Retornar 401 se segredo ausente.
- Adicionar validação de IP whitelist ou header de origem do Mercado Pago.
  **Esforço:** Baixo
  **Prioridade:** Imediato

______________________________________________________________________

### 3. Validação insuficiente de credenciais em produção

**Severidade:** Crítico
**Impacto em produção:** Apenas `MERCADO_PAGO_ACCESS_TOKEN` é validado. `EMAIL_HOST_PASSWORD`, `MERCADO_PAGO_WEBHOOK_SECRET`, `PIX_KEY` podem estar vazios em produção sem nenhum erro ou warning.
**Evidência técnica:** `src/config/settings/base.py` ~linha 120:

```python
if not DEBUG and not MERCADO_PAGO_ACCESS_TOKEN:
    raise ValueError("MERCADO_PAGO_ACCESS_TOKEN deve ser configurado em produção")
```

**Causa raiz:** Validação incompleta de secrets obrigatórios.
**Correção recomendada:** Criar Django system check (`checks.py`) que valida todas as credenciais obrigatórias em produção: `MERCADO_PAGO_ACCESS_TOKEN`, `MERCADO_PAGO_WEBHOOK_SECRET`, `EMAIL_HOST_PASSWORD`, `PIX_KEY`, `SECRET_KEY`, `ALLOWED_HOSTS`, `ADMIN_URL`.
**Esforço:** Baixo
**Prioridade:** Imediato

______________________________________________________________________

### 4. `KeyError` certo no `pix_payment_view` se API do MP falhar

**Severidade:** Crítico
**Impacto em produção:** Crash 500 para todo usuário que tentar acessar a página de pagamento PIX quando a API do Mercado Pago estiver indisponível ou retornar erro.
**Evidência técnica:** `src/payments/views.py` ~linha 99-107:

```python
mp_data = None
if payment.mp_payment_id:
    mp_data = get_payment_status(payment.mp_payment_id)
    if not mp_data:
        logger.error(f"Não foi possível buscar dados do pagamento MP: {payment.mp_payment_id}")

context = {
    ...
    "mp_data": mp_data,
    ...
}

logger.info(f"""
    Exibindo página PIX com esses dados: {mp_data["id"]} |  # <-- KeyError se mp_data é None
    Amount {mp_data["transaction_amount"]} |  # <-- KeyError
    url_sandbox={mp_data["point_of_interaction"]["transaction_data"]["ticket_url"]}  # <-- KeyError
""")
```

**Causa raiz:** Acesso direto a chaves de dicionário sem verificar se o valor é None. O log é executado antes do render, crashando a request.
**Correção recomendada:** Se `mp_data is None`, renderizar template com mensagem de erro operacional ou redirecionar para `payment_pending_view`. Mover o log para dentro do bloco onde `mp_data` é garantido.
**Esforço:** Baixo
**Prioridade:** Imediato

______________________________________________________________________

### 5. Log de dados sensíveis em nível INFO

**Severidade:** Crítico
**Impacto em produção:** Emails de usuários, IDs de pagamento e dados de transação são logados em info level. Em um ambiente com logging centralizado (ELK, Datadog), isso expõe PII e dados financeiros.
**Evidência técnica:**

- `src/payments/views.py` ~linha 55: `logger.info(f"Pagamento criado: id={payment.id}, user={request.user.email}, amount={amount}")`
- `src/payments/views.py` ~linha 99-103: log com dados completos da transação MP
- `src/payments/webhooks.py` ~linha 61: `logger.warning(f"Assinatura do webhook inválida. Expected: {expected_hash}, Received: {received_hash}")` — loga hash esperado!

**Correção recomendada:**

- Usar apenas IDs opacos nos logs (`user_id=123`, nunca email).
- Nunca logar hashes de assinatura esperados.
- Reduzir level para DEBUG em dados de transação.
  **Esforço:** Baixo
  **Prioridade:** Imediato

______________________________________________________________________

## 🟠 ALTO

### 6. `save_bets_bulk` sem atomicidade por batch — salvas parciais

**Severidade:** Alto
**Impacto em produção:** Se 10 palpites são enviados e o 7º falha, os 6 anteriores foram salvos. O usuário vê estado inconsistente e recebe mensagens confusas ("Alguns palpites foram salvos, mas houve erros em outros").
**Evidência técnica:** `src/pool/views.py` ~linha 720: cada bet tem seu próprio `with transaction.atomic()`, mas o loop inteiro não é atômico.

**Causa raiz:** Granularidade de transação muito fina para operação de batch.
**Correção recomendada:** Envolver todo o loop em uma transação atômica única. Se falhar, rollback total. Alternativa menor: agrupar em batches menores com rollback parcial claro.
**Esforço:** Médio
**Prioridade:** Próxima sprint

______________________________________________________________________

### 7. Filtro redundante e potencialmente incorreto em `refresh_prize_distribution`

**Severidade:** Alto
**Impacto em produção:** O join `user__pool_participations__pool=self` é redundante — `Payment` já tem FK para `pool`. Isso pode causar duplicação de resultados do `SUM` se o join gerar rows extras.
**Evidência técnica:** `src/pool/models.py` ~linha 87:

```python
Payment.objects.filter(
    pool=self,
    status="approved",
    user__pool_participations__pool=self,  # redundante
    user__pool_participations__is_active=True,
)
```

**Causa raiz:** O filtro tenta garantir que apenas pagamentos de participantes ativos sejam contados, mas o join gera rows duplicados.
**Correção recomendada:** Simplificar para `Payment.objects.filter(pool=self, status="approved")`. Se a intenção é filtrar por participante ativo, usar subquery ou `Exists`.
**Esforço:** Baixo
**Prioridade:** Próxima sprint

______________________________________________________________________

### 8. N+1 queries em `build_pool_participant_view_context`

**Severidade:** Alto
**Impacto em produção:** Para um bolão com 64 partidas, a view executa facilmente 100+ queries por request. Degradação progressiva conforme o número de partidas e participantes aumenta.
**Evidência técnica:** `src/pool/views.py` ~linha 350: `Match.objects.filter().select_related(...)` é otimizado, mas:

- `_ensure_participant_bets` faz query separada para bets
- `load_persisted_group_standings` faz query para standings
- `load_persisted_third_places` faz query para third places
- `_top_scorer_options_for_pool` faz query separada

**Correção recomendada:** Adicionar `prefetch_related` para `bets`, `projected_standings`, `projected_third_places`. Cache de top scorer options.
**Esforço:** Médio
**Prioridade:** Próxima sprint

______________________________________________________________________

### 9. Zero testes no módulo de pagamentos

**Severidade:** Alto
**Impacto em produção:** O módulo que processa dinheiro real tem `tests.py` vazio. Nenhuma validação de webhook, criação de pagamento, tratamento de erro ou idempotência é testada. Bugs de pagamento só serão descobertos em produção.
**Evidência técnica:** `src/payments/tests.py` — arquivo completamente vazio.
**Correção recomendada:** Escrever testes para:

- `create_pix_payment` — sucesso, falha MP, idempotência
- `mercado_pago_webhook` — assinatura válida, inválida, duplicada, missing fields
- `pix_payment_view` — mp_data None, payment already paid
- `payment_success_view` / `payment_pending_view` — acesso por usuário errado
  **Esforço:** Alto
  **Prioridade:** Próxima sprint

______________________________________________________________________

### 10. `sync_teams` escreve bandeiras em `STATICFILES_DIRS`

**Severidade:** Alto
**Impacto em produção:** Em deploy com múltiplos workers/containers, bandeiras são baixadas para o filesystem local de cada nó — não são compartilhadas. Além disso, escreve em diretório que deveria ser read-only após `collectstatic`.
**Evidência técnica:** `src/football/services/sync_teams.py` ~linha 20:

```python
filepath = f"{settings.STATICFILES_DIRS[0]}/{filename}"
```

**Causa raiz:** Confusão entre static files (build-time) e media files (runtime).
**Correção recomendada:** Usar `FileField` com media storage backend configurável (local em dev, S3/GCS em produção). Nunca escrever em `STATICFILES_DIRS`.
**Esforço:** Médio
**Prioridade:** Próxima sprint

______________________________________________________________________

### 11. `ADMIN_URL` com valor default previsível

**Severidade:** Alto
**Impacto em produção:** Se `DJANGO_ADMIN_URL` não for configurado, o admin fica em `painel-interno-admin/` — previsível por atacantes e scanners automatizados.
**Evidência técnica:** `src/config/settings/base.py`:

```python
ADMIN_URL = os.getenv("DJANGO_ADMIN_URL", "painel-interno-admin/").strip("/") + "/"
```

**Correção recomendada:** Não ter default. Exigir configuração explícita em produção via system check. Em dev, usar valor dummy mas logar warning.
**Esforço:** Baixo
**Prioridade:** Próxima sprint

______________________________________________________________________

### 12. `sync_matches` recalcula TODOS os pools síncronamente

**Severidade:** Alto
**Impacto em produção:** `recalculate_all_pools(season=season)` é chamado no final do sync. Com 100 pools × 50 participantes = 5000 recalculações síncronas. O comando pode levar minutos e travar o banco.
**Evidência técnica:** `src/football/services/sync_matches.py` última linha:

```python
recalculate_all_pools(season=season)
```

**Correção recomendada:** Enfileirar recálculos via `PoolProjectionRecalc` (já existe o mecanismo de fila). Ou disparar via Celery quando disponível.
**Esforço:** Médio
**Prioridade:** Backlog

______________________________________________________________________

## 🟡 MÉDIO

### 13. `refresh_prize_distribution` chamado em view GET

**Severidade:** Médio
**Impacto em produção:** `src/penninicup/views.py` ~linha 242 chama `selected_pool.refresh_prize_distribution(save=True)` numa view GET. Isso é side-effect num GET — viola HTTP semantics, pode ser triggerado por prefetch/crawler/bots, e modifica dados do banco.
**Correção recomendada:** Mover para celery task ou management command. No GET, apenas exibir valor cacheado.
**Esforço:** Médio
**Prioridade:** Backlog

### 14. `RegisterView` cria usuário ativo e depois desativa

**Severidade:** Médio
**Impacto em produção:** `super().form_valid(form)` cria o usuário, depois `self.object.is_active = False`. Se houver signals ou middleware entre esses passos, podem ver o usuário como ativo antes da desativação.
**Correção recomendada:** Criar usuário já com `is_active=False` sobrescrevendo `form.save()` ou setando antes do save.
**Esforço:** Baixo
**Prioridade:** Backlog

### 15. `InviteToken.use()` não é atômico

**Severidade:** Médio
**Impacto em produção:** O método de instância `use()` faz `self.uses_count += 1; self.save()` sem lock. O método de classe `use_token()` é atômico, mas se algum código chamar `use()` diretamente, há race condition.
**Correção recomendada:** Tornar `use()` privado (`_use()`) ou remover e forçar uso de `use_token()`.
**Esforço:** Baixo
**Prioridade:** Backlog

### 16. `process_next_projection_recalc_job` sem limite de retries

**Severidade:** Médio
**Impacto em produção:** Jobs com erro persistente são reprocessados infinitamente. O campo `attempts` é incrementado mas nunca checado contra um limite máximo.
**Correção recomendada:** Adicionar `MAX_ATTEMPTS = 5` e marcar como `FAILED` permanentemente após N tentativas.
**Esforço:** Baixo
**Prioridade:** Backlog

### 17. Templates carregam Lucide icons de CDN sem SRI

**Severidade:** Médio
**Impacto em produção:** `src/templates/base.html` carrega `<script src="https://unpkg.com/lucide@latest">` sem integrity hash e sem versão fixa. Vulnerável a supply chain attack se unpkg for comprometido.
**Correção recomendada:** Usar versão fixa com SRI hash (`@1.0.0` + integrity), ou bundler local via npm.
**Esforço:** Baixo
**Prioridade:** Backlog

### 18. `PoolParticipant.Meta.ordering` sem índice

**Severidade:** Médio
**Impacto em produção:** `ordering = ["-total_points", "joined_at"]` — sem índice composto, o Django faz filesort em toda query de participantes. Degrada com escala.
**Correção recomendada:** Adicionar `models.Index(fields=["-total_points", "joined_at"])` no Meta.
**Esforço:** Baixo
**Prioridade:** Backlog

### 19. Webhook handler retorna detalhes de erro HTTP 500

**Severidade:** Médio
**Impacto em produção:** `src/payments/webhooks.py` retorna `HttpResponse("Internal server error", status=500)` com mensagem genérica — OK. Mas `logger.exception` pode vazar stack traces para logs centralizados.
**Correção recomendada:** Já está aceitável, mas adicionar rate limiting no endpoint para evitar abuso de logging.
**Esforço:** Baixo
**Prioridade:** Backlog

### 20. `save_bet` AJAX retorna exceção raw no response

**Severidade:** Médio
**Impacto em produção:** `src/pool/views.py` ~linha 617: `return JsonResponse({"ok": False, "error": str(exc)}, status=400)` — expõe mensagem de exceção interna ao frontend. Pode vazar detalhes de implementação.
**Correção recomendada:** Mapear exceções para mensagens amigáveis. Logar o detalhe, retornar mensagem genérica ao client.
**Esforço:** Baixo
**Prioridade:** Backlog

______________________________________________________________________

## 🟢 BAIXO

### 21. `SECRET_KEY = NotImplemented` em `base.py`

**Severidade:** Baixo
**Impacto:** Se `local/settings.dev.py` não sobrescrever, Django crash na inicialização. Não é bug de segurança, mas confuso para novos devs.
**Correção:** Usar `os.getenv("SECRET_KEY")` com mensagem de erro clara se ausente.
**Esforço:** Baixo

### 22. `amount` do Payment com `max_digits=8` — limita a R$999,999.99

**Severidade:** Baixo
**Impacto:** Se entry fee × participantes ultrapassar R$100k, o campo não suporta.
**Correção:** Aumentar para `max_digits=10, decimal_places=2`.
**Esforço:** Baixo

### 23. Mensagens sem acentos nos views do pool

**Severidade:** Baixo
**Impacto:** Inconsistência de UX. Ex: "Voce entrou no bolao" vs "Você entrou no bolão".
**Correção:** Padronizar com acentos ou implementar i18n.
**Esforço:** Baixo

### 24. `DJANGO_SETTINGS_PROFILE` determina modo de teste — frágil

**Severidade:** Baixo
**Impacto:** Se variável de ambiente mudar de nome, testes rodam com settings de produção sem warning.
**Correção:** Adicionar check explícito no `Makefile` de teste.
**Esforço:** Baixo

### 25. Sem custom middleware de logging/request tracing

**Severidade:** Baixo
**Impacto:** Requests não têm correlation ID. Em produção, é impossível rastrear um erro de ponta a ponta nos logs.
**Correção:** Adicionar middleware que gera request ID e inclui em todos os logs e response headers.
**Esforço:** Baixo

### 26. `penninicup/views.py` importa de `pool/views.py` — acoplamento entre apps

**Severidade:** Baixo
**Impacto:** `from src.pool.views import build_pool_participant_view_context` — função de view importada por outro app. Viola boundaries de app Django.
**Correção:** Mover `build_pool_participant_view_context` para `src/pool/services/context_builder.py`.
**Esforço:** Baixo

______________________________________________________________________

# TOP 10 RISCOS QUE PODEM CAUSAR INCIDENTE EM PRODUÇÃO

| #   | Risco                                                            | Probabilidade |            Impacto             | Cenário                                                                                                       |
| --- | ---------------------------------------------------------------- | :-----------: | :----------------------------: | ------------------------------------------------------------------------------------------------------------- |
| 1   | **KeyError no `pix_payment_view`** se API do MP falhar           |   **Alta**    |       **500 em cascata**       | Todo usuário tentando pagar recebe erro 500. Receita zero durante indisponibilidade do MP.                    |
| 2   | **Webhook sem idempotência** — processamento duplo               |   **Média**   |    **Prejuízo financeiro**     | MP reenvia webhook (comportamento padrão). Se lógica de crédito for adicionada, usuário recebe crédito duplo. |
| 3   | **Webhook sem validação em produção** se segredo não configurado |   **Média**   | **Aceita pagamentos forjados** | Deploy sem `MERCADO_PAGO_WEBHOOK_SECRET` → qualquer POST é aceito como pagamento aprovado.                    |
| 4   | **save_bets_bulk com salvas parciais** corrompe estado           |   **Média**   |     **Perda de palpites**      | Usuário salva 64 palpites, 5 falham. 59 salvos, 5 perdidos. Sem indicação clara de quais.                     |
| 5   | **N+1 queries no `pool_detail`**                                 |   **Alta**    |    **Lentidão progressiva**    | Cada partida adicionada aumenta queries linearmente. Com 100+ partidas, request > 5s.                         |
| 6   | **refresh_prize_distribution no GET**                            |   **Média**   |  **Side-effect por crawler**   | Googlebot ou prefetch triggeram write no banco. Concorrência com outros writes pode causar deadlock.          |
| 7   | **Bandeiras escritas em STATICFILES_DIRS**                       |   **Média**   |    **Falha em multi-node**     | Em deploy com 3 containers, cada um tem bandeiras diferentes. Usuários veem imagens inconsistentes.           |
| 8   | **Zero testes no módulo de pagamentos**                          |   **Alta**    |    **Bugs não detectados**     | Qualquer regressão no webhook ou criação de pagamento só é descoberta quando um usuário real reporta.         |
| 9   | **sync_matches recalcula todos os pools síncrono**               |   **Alta**    |     **Timeout de comando**     | Com 50+ pools, `sync_matches` leva minutos. Se rodar via cron, pode sobrepor execuções.                       |
| 10  | **ADMIN_URL default previsível**                                 |   **Baixa**   |       **Admin exposto**        | Scanners automatizados encontram `/painel-interno-admin/`. Brute force em credenciais fracas.                 |

______________________________________________________________________

# ÁREAS VERIFICADAS SEM FALHAS SIGNIFICATIVAS

| Área                                    | Veredito | Observação                                                                    |
| --------------------------------------- | -------- | ----------------------------------------------------------------------------- |
| `InviteToken.use_token()` atômico       | ✅ OK    | Usa `select_for_update` corretamente. Protegido contra race condition.        |
| Modelo `CustomUser` com email único     | ✅ OK    | Constraint case-insensitive implementada.                                     |
| Proteção CSRF (geral)                   | ✅ OK    | `CsrfViewMiddleware` ativo. Apenas webhook tem `@csrf_exempt` (justificável). |
| `PoolBet.clean()` valida fase bloqueada | ✅ OK    | Validação no model level, não só na view.                                     |
| Tie-breaking no leaderboard             | ✅ OK    | Lógica com overrides manuais bem implementada.                                |
| Projeção de knockout com placeholders   | ✅ OK    | Resolução de W(n), RU(n) e third-place mapeada corretamente.                  |
| Testes do módulo accounts               | ✅ OK    | Cobertura razoável para registro, verificação, login, password reset.         |
| Testes do módulo pool                   | ✅ OK    | Boa cobertura de regras de aposta, tokens, projeções, tie-breakers.           |
| Timezone handling no sync de matches    | ✅ OK    | Testes específicos para UTC → Brasilia.                                       |
| Settings split com split-settings       | ✅ OK    | Arquitetura limpa. Test profile isolado corretamente.                         |

______________________________________________________________________

# PLANO DE AÇÃO EM 3 ONDAS

## Onda 1: Correções Imediatas de Risco (1-3 dias)

Corrigir bugs que podem causar incidente ativo em produção agora.

| #   | Ação                                                                              | Arquivo(s)                                          | Esforço |
| --- | --------------------------------------------------------------------------------- | --------------------------------------------------- | :-----: |
| 1.1 | Fixar `KeyError` no `pix_payment_view` quando `mp_data` é None                    | `src/payments/views.py`                             |  15min  |
| 1.2 | Adicionar idempotência ao webhook (verificar transição de status)                 | `src/payments/webhooks.py`                          |  30min  |
| 1.3 | Adicionar system checks para credenciais obrigatórias em produção                 | `src/config/settings/`                              |   1h    |
| 1.4 | Remover dados sensíveis dos logs de pagamento e webhook                           | `src/payments/views.py`, `src/payments/webhooks.py` |  30min  |
| 1.5 | Corrigir fallback de validação de webhook — nunca aceitar sem segredo em produção | `src/payments/webhooks.py`                          |  15min  |
| 1.6 | Aumentar `max_digits` do campo `amount` do Payment de 8 para 10                   | `src/payments/models.py` + migration                |  15min  |
| 1.7 | Mascarar exceções raw no response AJAX do `save_bet`                              | `src/pool/views.py`                                 |  15min  |

______________________________________________________________________

## Onda 2: Estabilização e Confiabilidade (1-2 semanas)

Reduzir superfície de bugs e melhorar confiabilidade operacional.

| #    | Ação                                                                                                 | Esforço  |
| ---- | ---------------------------------------------------------------------------------------------------- | :------: |
| 2.1  | Escrever testes completos para módulo de pagamentos (webhook, criação, idempotência, error handling) | 1-2 dias |
| 2.2  | Tornar `save_bets_bulk` atômico por batch completo                                                   |    2h    |
| 2.3  | Corrigir N+1 queries com `prefetch_related` nos views do pool                                        |    3h    |
| 2.4  | Adicionar `max_attempts` ao processamento de projeção (limitar retries)                              |    1h    |
| 2.5  | Mover `refresh_prize_distribution` para fora do GET (rules view)                                     |    2h    |
| 2.6  | Corrigir filtro redundante em `refresh_prize_distribution`                                           |    1h    |
| 2.7  | Criar usuário já com `is_active=False` no registro (antes do save)                                   |  30min   |
| 2.8  | Proteger `InviteToken.use()` ou torná-lo privado                                                     |  30min   |
| 2.9  | Corrigir `ADMIN_URL` para não ter default em produção                                                |  30min   |
| 2.10 | Adicionar índice composto em `PoolParticipant(total_points, joined_at)`                              |  30min   |
| 2.11 | Adicionar request ID middleware para tracing de logs                                                 |    2h    |
| 2.12 | Mover `build_pool_participant_view_context` para service layer                                       |    1h    |

______________________________________________________________________

## Onda 3: Melhorias Estruturais de Médio Prazo (1-2 meses)

Investir em arquitetura e manutenibilidade de longo prazo.

| #    | Ação                                                                                  |   Esforço   |
| ---- | ------------------------------------------------------------------------------------- | :---------: |
| 3.1  | Migrar downloads de bandeiras para Media storage backend (S3/GCS)                     |  1 semana   |
| 3.2  | Adicionar Celery + Redis para processamento assíncrono (recálculos, sync da FIFA API) | 1-2 semanas |
| 3.3  | Implementar rate limiting em endpoints sensíveis (registro, webhook, login)           |   2 dias    |
| 3.4  | Adicionar SRI hash para scripts CDN ou bundle local do Lucide icons                   |     1h      |
| 3.5  | Padronizar mensagens com acentos e implementar i18n                                   |  1 semana   |
| 3.6  | Configurar Sentry ou similar para monitoramento de erros em produção                  |    1 dia    |
| 3.7  | Cobertura de testes mínima de 80% em todos os módulos                                 |  2 semanas  |
| 3.8  | Adicionar Django security middleware (SECURE_HSTS, SECURE_CONTENT_TYPE, etc.)         |     2h      |
| 3.9  | Implementar health check endpoint (`/health/`) para monitoramento                     |     1h      |
| 3.10 | Adicionar Django check de produção para `DEBUG = False` + `ALLOWED_HOSTS`             |     1h      |
| 3.11 | Separar settings de produção com validação rigorosa (fail fast)                       |    1 dia    |
| 3.12 | Documentar fluxos críticos (pagamento, webhook, apostas) com diagramas                |   2 dias    |

______________________________________________________________________

# NOTAS ADICIONAIS

## Sobre a arquitetura geral

O projeto tem **boa separação de concerns** em vários aspectos:

- Service layer para lógica de negócio (`pool/services/`, `football/services/`)
- Fila de projeção via modelo DB (sem necessidade de Celery por enquanto)
- Testes sólidos nos módulos `accounts` e `pool`
- Settings organizados com `split-settings`

## Dívidas técnicas acumuladas

1. **Módulo de pagamentos é o elo mais fraco** — zero testes, lógica crítica sem proteção de idempotência, logs inseguros.
1. **Acoplamento entre apps** — `penninicup` importa de `pool.views`, `accounts` importa de `pool.models`.
1. **Operações síncronas que devem ser assíncronas** — recálculo de scores, sync da FIFA API, download de bandeiras.

## Recomendação de priorização

Se só puder fazer **3 coisas** imediatamente:

1. **Fixar o KeyError do `pix_payment_view`** — crash 100% garantido se API do MP falhar.
1. **Adicionar idempotência ao webhook** — risco financeiro real.
1. **Escrever testes do módulo de pagamentos** — sem testes, qualquer correção é um tiro no escuro.
