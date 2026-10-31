# Pré-registro H3 — popularidade da Lotofácil (teste prospectivo)

Registrado em 31/10/2026, antes do sorteio do concurso 3819 e antes de qualquer resultado prospectivo. Depois de
registrado, nada aqui muda; qualquer alteração exige emenda datada no fim deste arquivo, com o hash anterior.

## Hipóteses mantidas separadas
- **H1** (prever números sorteados): fora deste experimento. Nada daqui é evidência de H1.
- **H2** (cobertura de carteira): fora deste experimento.
- **H3a** (ranking, confirmatória): o modelo congelado ordena corretamente as combinações sorteadas por quantos
  apostadores as escolheram.
- **H3b** (calibração): só descritiva; nunca é usada para declarar H3 confirmada.

## Evidência anterior (retrospectiva, motivou o teste)
Walk-forward em 2.290 concursos (1529–3818), pesos treinados só com o passado: Spearman +0,63 (14 pontos) e +0,30
(15 pontos); redução do desvio de Poisson de 24,7% e 7,6% contra só vendas. Ressalva: a configuração foi escolhida em
31/10/2026 olhando dados que incluem esse período. Por isso o resultado retrospectivo não é confirmação.

## Modelo congelado (nada disso muda durante o teste)
- Algoritmo: média de ridge (λ = 3,0) e rede de 2 camadas tanh (8 ocultos, 25 épocas, semente 1), funções
  `_ajusta` / `_mlp` / `_ridge` de `otimizador.py`.
- Atributos: um-quente das 15 dezenas + os padrões de escolha `PADROES` (`_padroes`), padronizados com o treino.
- Alvo de treino: `y_multi` de `_amostras` (log de ganhadores/esperados combinando as faixas, peso pela precisão).
- Treino: concursos anteriores a t, janela dos últimos 1.000, peso exponencial com meia-vida de 500 concursos.
- Configuração: `{"modelo": "media", "alvo": "multi", "padroes": true, "meia_vida": 500, "janela": 1000, "lambda": 3.0}`.
- Retreino: um modelo novo para cada concurso t, treinado com tudo o que a base local tiver de concursos < t no
  momento do selo (o selo grava o último concurso usado e o hash dos dados). O treino é determinístico: o mesmo
  passado gera o mesmo modelo, bit a bit.
- Código congelado (SHA-256): `29c5a231c93696d383396b03ba4b14bcec6707d3a208cc144d0efb55015a6ebd`
  (texto exato das funções do modelo em `otimizador.py`, `hypergeom` de `core.py`, regras da Lotofácil usadas e o
  arquivo `popularidade_h3.py` inteiro). Se o hash mudar, o app se recusa a selar.

## Procedimento por concurso
1. Antes de o resultado de t entrar na base local: treina com concursos < t, grava o modelo e sela no registro
   encadeado (`h3/registro.jsonl` na pasta de dados do app): concurso, último concurso de treino, hash dos dados,
   hash do modelo, hash do código, hash deste arquivo, hash do registro anterior.
2. Depois do sorteio: o modelo selado (hash conferido) calcula o fator da combinação sorteada, com o resultado de
   t−1 como "último resultado".
3. Concursos que não foram selados antes de o resultado entrar na base (app desligado) ficam fora, por motivo
   operacional que não depende do resultado; o horizonte avança para completar 150.

## Medidas
- Ganhadores esperados pelas vendas: E_h(t) = arrecadação(t) × Σ ganhadores_h / Σ arrecadação nos 100 concursos
  imediatamente anteriores com dado oficial (só passado; sem nenhuma informação das dezenas).
- R_h(t) = ganhadores oficiais de h pontos em t / E_h(t), h ∈ {14, 15}.
- **Concursos sem ganhador de 15 pontos permanecem na amostra com R15 = 0. Nenhum concurso é excluído em função do
  resultado observado.** Nenhuma transformação logarítmica é usada nos testes; empates (vários R15 = 0) recebem o
  posto médio.
- Exclusão permitida só por falta de dado oficial (arrecadação ou ganhadores de 14/15 ausentes, ou resultado de t−1
  ausente), registrada e listada.

## Teste confirmatório (H3a) — uma única vez, nos primeiros 150 concursos avaliados a partir do 3819
- Estatística primária: ρ de Spearman entre o fator previsto e R14.
- H0: ρ ≤ 0; H1: ρ > 0. α = 0,05, unilateral.
- Significância: teste de permutação — 100.000 permutações do fator entre os concursos (gerador numpy
  `default_rng(3819)`), p = (1 + nº de permutações com ρ ≥ ρ observado) / 100.001.
- H3a confirmada se e somente se p < 0,05.

## Secundárias (não decidem H3a; Holm entre as três)
1. ρ de Spearman entre fator e R15 (mesmo teste de permutação).
2. Razão (Σ ganhadores / Σ esperados) dos 20% de concursos com maior fator sobre a dos 20% com menor fator, para 14
   pontos; H1: razão > 1; permutação como acima. Os 20% são os 30 concursos de menor e de maior fator (empates
   desfeitos pelo número do concurso).
3. A mesma razão para 15 pontos.

## H3b — só descritiva
Fator previsto, fator observado, inclinação de calibração (log((g14 + 0,5)/(E14 + 0,5)) sobre log do fator),
erro, comportamento por quintil e deterioração temporal.

## Regras de parada
Sem parada antecipada: o teste acontece só no 150º concurso avaliado, nem antes (mesmo com p muito pequeno), nem
depois. Não se altera modelo, horizonte ou métricas por causa de resultados parciais. O app mostra acompanhamento
descritivo (ρ parciais, sem p) rotulado como "não é o teste".
