# Prompt — Lógica de pontuação do bolão Copa do Mundo 2026

## Contexto

Altere a lógica de cálculo da pontuação dos participantes do bolão.
A Copa tem **104 jogos** no total: **72 na fase de grupos** e **32 no mata-mata**.

______________________________________________________________________

## Pontuação por placar

Cada palpite é comparado ao resultado real e pontuado conforme a tabela abaixo.
A verificação dos critérios deve seguir a **ordem de prioridade** listada — o primeiro critério que bater é o que vale, não acumula.

### Fase de grupos

| Prioridade | Critério                                        | Pontos |
| ---------- | ----------------------------------------------- | ------ |
| 1          | Placar exato                                    | 25     |
| 2          | Acertou o vencedor E os gols do vencedor        | 18     |
| 3          | Acertou o vencedor E a diferença de gols        | 15     |
| 4          | Acertou o vencedor E os gols do perdedor        | 12     |
| 5          | Acertou apenas o vencedor (ou que seria empate) | 10     |
| 6          | Nenhum acerto                                   | 0      |

### Mata-mata

Aplica multiplicador **×1.4** sobre os valores da fase de grupos, arredondado para o inteiro mais próximo.
O placar que conta é **sempre o do tempo normal (90 min)**, independente de prorrogação ou pênaltis.

| Prioridade | Critério                                         | Pontos |
| ---------- | ------------------------------------------------ | ------ |
| 1          | Placar exato E acertou o classificado            | 35     |
| 2          | Acertou o classificado E os gols do classificado | 25     |
| 3          | Acertou o classificado E a diferença de gols     | 21     |
| 4          | Acertou o classificado E os gols do eliminado    | 17     |
| 5          | Acertou apenas o classificado                    | 14     |
| 5          | Placar exato MAS classificado errado             | 10     |
| 6          | Nenhum acerto                                    | 0      |

> **Regra especial:** placar exato com classificado errado vale apenas 10 pts. Errar quem avança anula o mérito do placar perfeito.

### Mata-mata no Tipo 2 (palpite progressivo)

No bolão **Tipo 2** o mata-mata é pontuado **pelo classificado**, não por posição. O classificado palpitado é a **identidade do time** que o participante projetou para aquele jogo (deduzida do bracket projetado do participante quando os times reais ainda não existem, ex.: R16+), comparada ao `match.winner` real.

A pontuação é **travada pelo classificado**:

- **Classificado errado → 0**, mesmo com placar exato (não há consolação de 10 pts; é o gate, não acumula).
- Se a identidade do classificado projetado não bater com quem avançou de fato (ou o time projetado foi eliminado antes deste jogo), o palpite **não pontua** naquela rodada.
- **Classificado certo →** aplicam-se as faixas `knockout_*` (placar exato 35 / gols do classificado 25 / diferença 21 / gols do eliminado 17 / só o classificado 14), pelos valores das configurações do bolão.

Não há **bônus de classificado** (team advancement) no Tipo 2 — o acerto do classificado já é a condição de pontuar o jogo.

Real empate decidido nos pênaltis (Tipo 2, classificado certo): placar exato **35** / mesma diferença (0) **21** / senão **14**.

______________________________________________________________________

## Como determinar o classificado no mata-mata

O classificado é o time que avança para a próxima fase, independente de como (tempo normal, prorrogação ou pênaltis).

**No palpite:** o participante sempre palpita o placar do tempo normal. O classificado é **deduzido automaticamente** do palpite:

- Se o palpite tiver vencedor (ex: 2×1) → o vencedor do placar é o classificado palpitado.
- Se o palpite for empatado (ex: 1×1) → o participante **escolhe explicitamente** qual time avança em campo separado obrigatório.

**No resultado real:** o classificado é sempre o time que avançou de fato (pode ter sido por prorrogação ou pênaltis mesmo que o tempo normal tenha terminado empatado).

______________________________________________________________________

## Definições dos critérios

```
Dado resultado real do tempo normal (homeGoals, awayGoals),
time classificado real (actualAdvancing: homeTeam | awayTeam),
palpite de placar (guessHome, guessAway)
e classificado palpitado (guessAdvancing, deduzido ou explícito):

isExactScore             = guessHome == homeGoals && guessAway == awayGoals

isClassifiedCorrect      = guessAdvancing == actualAdvancing

isClassifiedGoalsCorrect = isClassifiedCorrect &&
                           (
                             (actualAdvancing == homeTeam && guessHome == homeGoals) ||
                             (actualAdvancing == awayTeam && guessAway == awayGoals)
                           )

isDiffCorrect            = isClassifiedCorrect &&
                           (guessHome - guessAway) == (homeGoals - awayGoals)

isEliminatedGoalsCorrect = isClassifiedCorrect &&
                           (
                             (actualAdvancing == homeTeam && guessAway == awayGoals) ||
                             (actualAdvancing == awayTeam && guessHome == homeGoals)
                           )
```

## Exemplos de validação

### Fase de grupos — resultado real: 2×1

| Palpite | Pontos | Motivo                          |
| ------- | ------ | ------------------------------- |
| 2 × 1   | 25     | Placar exato                    |
| 2 × 0   | 18     | Vencedor + gols do vencedor (2) |
| 3 × 2   | 15     | Vencedor + diferença (+1)       |
| 3 × 1   | 12     | Vencedor + gols do perdedor (1) |
| 3 × 0   | 10     | Só o vencedor                   |
| 1 × 1   | 0      | Palpitou empate, houve vencedor |
| 0 × 2   | 0      | Vencedor errado                 |

### Mata-mata — resultado real: 2×1 no tempo normal (Time A classificado)

| Palpite placar | Classificado palpitado | Pontos | Motivo                                  |
| -------------- | ---------------------- | ------ | --------------------------------------- |
| 2 × 1          | Time A (deduzido)      | 35     | Placar exato + classificado correto     |
| 2 × 0          | Time A (deduzido)      | 25     | Classificado + gols do classificado (2) |
| 3 × 2          | Time A (deduzido)      | 21     | Classificado + diferença (+1)           |
| 3 × 1          | Time A (deduzido)      | 17     | Classificado + gols do eliminado (1)    |
| 1 × 0          | Time A (deduzido)      | 14     | Só o classificado                       |
| 0 × 2          | Time B (deduzido)      | 0      | Classificado errado                     |

### Mata-mata — resultado real: 1×1 no tempo normal, Time A avança nos pênaltis

| Palpite placar | Classificado palpitado | Pontos | Motivo                                                   |
| -------------- | ---------------------- | ------ | -------------------------------------------------------- |
| 1 × 1          | Time A (explícito)     | 35     | Placar exato + classificado correto                      |
| 1 × 1          | Time B (explícito)     | 10     | Placar exato MAS classificado errado                     |
| 2 × 2          | Time A (explícito)     | 21     | Classificado correto + diferença 0                       |
| 2 × 2          | Time B (explícito)     | 0      | Classificado errado                                      |
| 0 × 0          | Time A (explícito)     | 21     | Classificado correto + diferença 0                       |
| 3 × 2          | Time A (deduzido)      | 14     | Só o classificado (acertou quem avançou, errou o empate) |
| 0 × 2          | Time B (deduzido)      | 0      | Classificado errado                                      |

______________________________________________________________________

## Bônus de previsão

| Bônus        | Pontos |
| ------------ | ------ |
| Campeão      | 120    |
| Artilheiro   | 100    |
| Vice-campeão | 60     |
| 3º lugar     | 30     |

**Regras:**

- Artilheiro: se houver empate na artilharia, qualquer palpite no grupo de artilheiros empatados pontua.
- Bônus se acumulam entre si — ex: se acertar campeão e vice, soma os pontos dos dois.
