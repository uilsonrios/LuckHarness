# Critérios pré-registrados — bateria de aprendizado sequencial (2ª etapa)

Registrados em 31/10/2026, ANTES de rodar a avaliação final. O hash SHA-256 deste arquivo vai no relatório e em
cada registro da bateria; ele não pode ser alterado depois de conhecidos os resultados.

## Períodos (por modalidade, ordem cronológica, depois dos 200 concursos iniciais usados só como histórico)
1. **Aquecimento:** primeiros 500 concursos — os modelos só aprendem, nada é avaliado.
2. **Desenvolvimento:** metade dos concursos restantes — único período usado para escolher configuração da rede
   (taxa de aprendizado, tamanho da camada oculta, conjunto de atributos), por log loss médio em 3 sementes.
3. **Avaliação final:** a outra metade — nunca usada para escolha. A rede continua aprendendo concurso a concurso
   (prevê, registra, revela, atualiza).

Ressalva declarada: o histórico inteiro já foi consultado em experimentos anteriores (v1 e v2 da rede online). A
avaliação final histórica, portanto, não é um teste inédito; só concursos futuros registrados antes do sorteio são.

## Métrica primária
Acertos por concurso com a aposta mínima de cada modalidade (Mega 6, Lotofácil 15, Quina 5, Lotomania 50,
Timemania 10, Dia de Sorte 7, Dupla Sena 6 com os dois sorteios, +Milionária 6 números, Super Sete 1 dígito por
coluna), comparados com a expectativa exata ao acaso. Unidade estatística: o concurso (média das sementes por
concurso), desvio empírico entre concursos.

## Hipóteses primárias (família de 9, correção de Holm, α = 0,05, unilateral)
H1(modalidade): a rede tem média de acertos maior que o acaso no período final.

## Hipóteses secundárias (família de 9, Holm)
H2(modalidade): a rede tem média maior que o MELHOR modelo de referência da mesma modalidade no período final
(diferença pareada por concurso; o melhor de referência é escolhido no período de desenvolvimento).
Métricas probabilísticas (log loss e Brier contra a taxa base) são relatadas, não decidem.

## Critério de aprovação ("promissora") — TODOS:
1. Aprende padrões artificiais independentes, inclusive um tipo sorteado às cegas (escolha selada por hash).
2. Testes automáticos de vazamento passam (previsão gravada antes de qualquer atualização; atributos só do passado).
3. H1 rejeitada após Holm.
4. H2 rejeitada após Holm.
5. Diferença positiva em pelo menos 75% das sementes (20 sementes).
6. Diferença positiva em pelo menos 75% dos blocos de 250 concursos do período final.
7. Reprodutível: mesma semente reproduz exatamente as previsões (hash da cadeia).
8. Mantém vantagem nos concursos futuros registrados (exige acompanhamento; não decidível hoje).

Se não passar, o resultado é registrado como "sem evidência de superioridade" — não como prova de impossibilidade.

## Emenda 1 (31/10/2026, antes de qualquer resultado da bateria)
Com 200 + 500 concursos iniciais, a +Milionária (403 concursos) ficaria sem avaliação e a Super Sete (917) com
217. Para modalidades com menos de 1.400 concursos, o histórico inicial passa a 50 e o aquecimento a 100. Os
trevos da +Milionária são avaliados como série secundária (2 de 6), fora da família primária.
Hash anterior deste arquivo: d0df2fc7a5508362446d1f2560c44e50fab3d42c9f2c0e039e3ed89a5167168d
