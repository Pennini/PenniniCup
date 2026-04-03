# 🏆 Regras de Classificação e Chaveamento — Copa do Mundo 2026 (Baseado na Planilha Oficial)

Este documento descreve de forma completa e técnica as regras utilizadas para:

- Classificação da fase de grupos
- Seleção e ordenação dos terceiros colocados
- Preenchimento do chaveamento do mata-mata
- Resolução de placeholders (ex: `1A`, `3ABCDF`, `W74`)

O objetivo é servir como **referência para implementação em código (Python)**.

______________________________________________________________________

# 1. 📊 Classificação da Fase de Grupos

## 1.1 Estrutura

- 12 grupos (A a L)
- Cada grupo possui 4 seleções
- Cada time joga 3 partidas

______________________________________________________________________

## 1.2 Pontuação por jogo

- Vitória → 3 pontos
- Empate → 1 ponto
- Derrota → 0 pontos

______________________________________________________________________

## 1.3 Métricas por time

Para cada time, calcular:

```python
points
goals_for
goals_against
goal_diff = goals_for - goals_against
```

______________________________________________________________________

## 1.4 Ranking (REGRA CRÍTICA)

Calcule um **score numérico único** para cada time na fase de grupos usando a fórmula:

```python
score = (points * 1_000_000) + (goal_diff * 1_000) + goals_for
```

______________________________________________________________________

## 1.5 Ordenação

```python
ranking = sorted(teams, key=score, reverse=True)
```

______________________________________________________________________

## 1.6 Por que isso funciona

Peso dos critérios:

- Pontos → dominante
- Saldo de gols → desempate
- Gols marcados → desempate final

______________________________________________________________________

# 2. 🥉 Seleção dos Terceiros Colocados

## 2.1 Extração

Para cada grupo:

```python
thirds = [group[2] for group in all_groups]
```

Total: 8 times

______________________________________________________________________

## 2.2 Cálculo do score

Mesma fórmula:

```python
score = (points * 1_000_000) + (goal_diff * 1_000) + goals_for
```

______________________________________________________________________

## 2.3 Ranking global

```python
ranked_thirds = sorted(thirds, key=score, reverse=True)
```

______________________________________________________________________

## ⚠️ Importante

Na Copa 2026:

- Todos os terceiros avançam
- MAS a ordem deles define o chaveamento

______________________________________________________________________

# 3. 🔀 AssignThird — Distribuição dos Terceiros

## 3.1 O problema

Os terceiros classificados NÃO são colocados aleatoriamente no mata-mata.

Existe uma regra fixa que define:

> Qual terceiro vai para qual jogo

______________________________________________________________________

## 3.2 Entrada do sistema

Lista dos grupos dos terceiros classificados:

```python
groups = ["A", "B", "C", "D", "E", "F", "G", "H"]
```

______________________________________________________________________

## 3.3 Normalização

```python
key = tuple(sorted(groups))
```

______________________________________________________________________

## 3.4 Tabela de mapeamento (AssignThird)

Estrutura:

```python
mapping = {
    ("A","B","C","D","E","F","G","H"): {
        "slot1": "3A",
        "slot2": "3B",
        ...
    },
}
```

______________________________________________________________________

## 3.5 Interpretação

- A chave representa **quais grupos forneceram terceiros**
- O valor define **onde cada terceiro entra no chaveamento**

______________________________________________________________________

## ⚠️ REGRA CRÍTICA

- NÃO existe escolha dinâmica
- NÃO existe aleatoriedade
- A tabela define tudo

______________________________________________________________________

# 4. 🧩 Placeholders do Mata-Mata

## 4.1 Tipos

### a) Classificação direta

```text
1A → 1º do grupo A
2B → 2º do grupo B
```

______________________________________________________________________

### b) Terceiros

```text
3ABCDF
```

Significa:

- Esse slot será preenchido por um terceiro
- Que pertence a um subconjunto de grupos
- A escolha NÃO é feita nesse momento
- É resolvida via AssignThird

______________________________________________________________________

### c) Vencedores

```text
W74 → vencedor do jogo 74
```

______________________________________________________________________

# 5. ⚙️ Resolução dos Placeholders

## 5.1 Função genérica

```python
def resolve_placeholder(placeholder, context):
```

______________________________________________________________________

## 5.2 Casos

### Caso 1: "1A", "2B"

```python
return group_results[group][position]
```

______________________________________________________________________

### Caso 2: "3ABCDF"

```python
# NÃO escolher aqui

# Apenas identificar que é um slot de terceiro
# Atribuição real vem do AssignThird
```

______________________________________________________________________

### Caso 3: "W74"

```python
return winners[74]
```

______________________________________________________________________

# 6. 🏟️ Construção do Mata-Mata

## 6.1 Fluxo

```python
1. Simular grupos
2. Classificar grupos
3. Extrair terceiros
4. Rankear terceiros
5. Determinar key (grupos)
6. Buscar mapping AssignThird
7. Preencher slots de terceiros
8. Resolver todos os placeholders
```

______________________________________________________________________

## 6.2 Exemplo

```python
distribution = mapping[key]

match_1.teamA = resolve("1A")
match_1.teamB = resolve(distribution["slot1"])
```

______________________________________________________________________

# 7. 🔄 Progressão no Mata-Mata

## 7.1 Para cada jogo

```python
winner = get_winner(prediction)
```

______________________________________________________________________

## 7.2 Atualizar próximos jogos

```python
next_match.slot = winner
```

______________________________________________________________________

# 8. 🎯 Estrutura Recomendada (Python)

```python
simulate_group_stage(predictions)
calculate_score(team)
rank_group(group)
get_all_thirds(groups)
rank_thirds(thirds)
get_third_mapping(groups)
resolve_placeholder(placeholder, context)
build_knockout(matches)
advance_winners(matches)
```

______________________________________________________________________

# 9. ⚠️ Erros Comuns

## ❌ Escolher terceiro dinamicamente

```python
random.choice(thirds)
```

______________________________________________________________________

## ❌ Ignorar ranking dos terceiros

______________________________________________________________________

## ❌ Não usar tabela fixa

______________________________________________________________________

## ✅ Correto

```python
distribution = mapping[key]
```

______________________________________________________________________

# 10. 🧠 Modelo Mental Final

## Classificação

```python
score = pts * 1e6 + saldo * 1e3 + gols
```

______________________________________________________________________

## Terceiros

```python
ranked = sorted(thirds, key=score)
```

______________________________________________________________________

## Chaveamento

```python
key = sorted(groups)
distribution = mapping[key]
```

______________________________________________________________________

## Execução completa

```python
groups → ranking → terceiros → mapping → bracket → winners
```

______________________________________________________________________

# 🚀 Conclusão

Este sistema:

- É 100% determinístico

- Replica exatamente a lógica da planilha oficial

- Depende fortemente da tabela `AssignThird`

- Separa claramente:

  - classificação
  - ranking
  - distribuição
  - execução do mata-mata

______________________________________________________________________

# 🔥 Próximo passo

Para implementação completa, você ainda precisa:

- Converter a aba `AssignThird` em JSON/dicionário
- Integrar isso no seu backend

Sem isso, o sistema ficará incorreto.

______________________________________________________________________
