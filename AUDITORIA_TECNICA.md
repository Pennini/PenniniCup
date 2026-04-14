# Auditoria Técnica Completa — PenniniCup

> **Data da última atualização:** 2026-04-14
> **Data da auditoria original:** 2026-04-13
> **Escopo:** Backend Django completo (models, views, forms, services, urls, commands, migrations, settings, integrações, webhooks, segurança, deploy)
> **Postura:** Revisão agressiva, sem suavizar críticas. Foco em riscos reais de produção.

______________________________________________________________________

## Índice

1. [Lista Priorizada de Problemas](#lista-priorizada-de-problemas-por-severidade)
   - [🔴 Crítico](#-cr%C3%ADtico)
   - [🟠 Alto](#-alto)
   - [🟡 Médio](#-m%C3%A9dio)
   - [🟢 Baixo](#-baixo)
1. [Top 10 Riscos de Produção](#top-10-riscos-que-podem-causar-incidente-em-produ%C3%A7%C3%A3o)
1. [Áreas Verificadas Sem Falhas Significativas](#%C3%A1reas-verificadas-sem-falhas-significativas)
1. [Plano de Ação em 3 Ondas](#plano-de-a%C3%A7%C3%A3o-em-3-ondas)
1. [Notas Adicionais](#notas-adicionais)
1. [Checklist de Segurança](#checklist-de-seguran%C3%A7a)

______________________________________________________________________

# LISTA PRIORIZADA DE PROBLEMAS POR SEVERIDADE

______________________________________________________________________

## 🔴 CRÍTICO

______________________________________________________________________

### 1. `ALLOWED_HOSTS = ["*"]` em produção (Docker)

**Severidade:** Crítico
**Impacto em produção:** `src/config/settings/docker.py` não redefine `ALLOWED_HOSTS`. Se a variável de ambiente não for configurada, o Django aceita requests para **qualquer host**. Isso permite ataques de DNS rebinding, onde um atacante controla o header `Host` e pode gerar links maliciosos que parecem legítimos.
**Evidência técnica:** `src/config/settings/base.py`: `ALLOWED_HOSTS = []` (vazio). `src/config/settings/docker.py` não sobrescreve. O system check em `checks.py` valida que não está vazio, mas não impede o startup — apenas gera warning no `check` command.
**Causa raiz:** Sem validação fail-fast no startup. Depende de operador rodar `python manage.py check` antes de deploy.
**Correção recomendada:**

- Em `docker.py`: validar explicitamente `ALLOWED_HOSTS` e crashar se estiver vazio ou com valor inseguro.
- Adicionar `DJANGO_ALLOWED_HOSTS` env var obrigatória em produção com mensagem clara de erro.
  **Esforço estimado:** Baixo
  **Prioridade sugerida:** Imediato
