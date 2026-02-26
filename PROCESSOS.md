# penninicup/

# ├── accounts/

# ├── bolao/

# ├── matches/

# ├── predictions/

# ├── core/

# ├── adminpanel/ (opcional)

# 🟩 Sprint 1 (Semana 1–2) — Fundação

# ✅ accounts

# 🔨 matches

# 🔨 Importação de dados

# 💯 Perfeito começar aqui.

# Sugestão pequena:

# Já pense nos models de matches orientados à pontuação, não só exibição.

# Ex:

# home_team, away_team

# phase (GROUP, OITAVAS, QUARTAS…)

# is_knockout

# finished

# 👉 Isso evita gambiarra no Sprint 2.

# Status: ⭐⭐⭐⭐⭐

# 🟨 Sprint 2 (Semana 3–4) — Core do jogo

# 🔨 predictions

# 🔨 Interface de palpites

# 🔨 Sistema de pontos

# Esse sprint é o coração do PenniniCup.

# Aqui vai a dica mais importante do projeto inteiro:

# 👉 Separe cálculo de pontos em um service, não no model nem na view.

# Ex:

# predictions/

# ├── services/

# │ └── scoring.py

# Isso vai te salvar quando:

# mudar regras

# criar conquistas

# recalcular ranking

# Status: ⭐⭐⭐⭐⭐ (crítico, mas bem posicionado)

# 🟦 Sprint 3 (Semana 5–6) — Social & escala

# 🔨 bolao

# 🔨 Convites

# 🔨 Rankings por bolão

# Excelente timing.

# Você já sabe:

# como funciona palpite

# como pontua

# como ranquear

# Então agora o bolão vira só um container lógico.

# Sugestão:

# Rankings derivados, não salvos (ou cacheados)

# Convite por:

# token único

# expiração opcional

# Status: ⭐⭐⭐⭐☆ (muito bom)

# 🟪 Sprint 4 (Semana 7–8) — Produto “uau”

# 🔨 Dashboard

# 🔨 Conquistas

# 🔨 Notificações

# Esse sprint transforma o projeto de “funciona” para “as pessoas usam todo dia”.

# Sugestão de ordem interna:

# Dashboard simples (ranking + últimos jogos)

# Estatísticas pessoais

# Conquistas (100% derivadas de dados existentes)

# Notificações (email primeiro, push depois se quiser)

# Status: ⭐⭐⭐⭐☆

# 🟥 Sprint 5+ — Profissionalização

# 🔨 adminpanel

# 🔨 Testes

# 🔨 Performance

# Isso aqui é luxo inteligente, não pressa.

# Boas escolhas:

# Adminpanel só se o Django Admin ficar limitado

# Testes depois do core (senão você testa coisa que vai mudar)

# Performance só quando houver problema real

# Status: ⭐⭐⭐⭐⭐
