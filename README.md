# LuckHarness

App para Windows que analisa as loterias da Caixa com matemática honesta: probabilidades exatas, valor esperado, otimização de apostas e gestão de banca. Tem também um assistente de IA local (Ollama).

> **Aviso:** nenhum programa prevê os números sorteados. Os sorteios são aleatórios, e a auditoria do próprio app confirma isso nos dados reais. O LuckHarness ajuda a gastar melhor: escolher o jogo com mais chance de prêmio por real, espalhar os cartões, dividir menos o prêmio e saber quanto dá para gastar. Ele não garante lucro. Jogue com limite.

## Download

Baixe o instalador **`luckHarness-Setup.exe`** em [Releases](https://github.com/uilsonrios/LuckHarness/releases/latest). O app avisa sozinho quando sai uma versão nova.

O Windows pode mostrar o aviso "O Windows protegeu o computador", porque o executável não é assinado. Para instalar, clique em **Mais informações → Executar assim mesmo**.

## O que ele faz

| Aba | Para que serve |
|---|---|
| **Minha aposta da semana** | Você diz quanto quer gastar e o app monta os cartões prontos, com a chance de cada faixa e o botão "Apostei" para registrar o gasto. Tem volante clicável e calendário de sorteios. No primeiro uso, um assistente pergunta o jogo, o orçamento e o limite do mês |
| **Hoje** | O que saiu em cada loteria e o que acumulou, com o próximo concurso e o prêmio estimado (dados oficiais da Caixa). Abaixo, a rede neural ao vivo: antes de cada sorteio, a rede (versão congelada) e as estratégias simples registram suas apostas com selo; depois do resultado, o placar mostra todas, inclusive as que perdem do acaso |
| **Análise** | Frequência, atrasos e somas dos sorteios, quantos números se repetem do concurso anterior (real vs. acaso) e um modelo de aprendizado testado contra o acaso (mostra que não supera o acaso) |
| **Minhas apostas** | Salva as apostas e confere sozinho com os resultados oficiais: dezenas, trevos, Time do Coração, Mês da Sorte e os dois sorteios da Dupla Sena |
| **Fechamento** | Gera cartões com garantia, por exemplo "se 4 das minhas 15 saírem, um cartão faz 3". Uma busca local reduz o número de cartões |
| **Retorno** | Quanto cada jogo devolve em média agora, e alertas quando o prêmio acumulado melhora o retorno |
| **Planejamento** | Simulação de anos de apostas e histórico do retorno |
| **Otimizador** | Escolhe o jogo e o formato (cartões simples, bolão ou fechamento) para o seu orçamento. Também tem "Aposta mínima, prêmio maior", risco de ruína, teste no passado, auditoria de aleatoriedade e previsão do acumulado |
| **Laboratório** | Testa qualquer estratégia (inclusive a sua aposta) com os resultados reais, às cegas: teste cego com impressão digital das apostas, teste contínuo com "aprende com erros e acertos", e registro de apostas para sorteios que ainda não aconteceram |
| **Banca** | Gastos, prêmios e limite mensal |
| **Harness IA** | Chat com um modelo local que recebe as contas do app como contexto |

### Otimizador: o que dá para melhorar de verdade

- **Escolha do jogo.** Com o mesmo dinheiro, a Lotofácil premia cerca de 400 vezes mais vezes que a Mega-Sena, e o Dia de Sorte (contando o Mês da Sorte) cerca de 600 vezes.
- **Cartões espalhados.** Os cartões repetem o mínimo de números entre si. Times, meses e trevos também não se repetem: com 12 cartões no Dia de Sorte, o Mês da Sorte sai em todo concurso.
- **Modelo de popularidade.** É uma rede de 1 camada que aprende o que o público mais joga (números e padrões de escolha: datas, sequências, desenhos no volante, repetir o último resultado), a partir do número real de ganhadores de cada concurso. O botão "Acumulados que saíram" mostra, nos prêmios que ficaram vários concursos sem ganhador, que a maioria era de combinações pouco jogadas. O app só usa o modelo de um jogo quando ele acerta em concursos que não viu no treino. Números pouco jogados dividem menos o prêmio, mas a chance de acertar continua a mesma.
- **Teste no passado.** O plano é jogado nos concursos reais, com os prêmios pagos de verdade, e comparado com cartões ao acaso.
- **Aposta mínima, prêmio maior.** Você diz quanto quer gastar (por exemplo R$ 6) e o tamanho do prêmio que quer receber de uma vez (R$ 1 mil, 10 mil, 100 mil ou 1 milhão). O app compara os 9 jogos, em cartões simples ou numa cota de bolão, e mostra onde a chance é maior. Exemplo: para receber pelo menos R$ 1 milhão gastando até R$ 10, 1/7 de um bolão de 7 dezenas da Quina dá 1 chance em 1,1 milhão, contra 1 em 50 milhões numa aposta simples da Mega-Sena.

Exemplo dos últimos 104 concursos, com o mesmo gasto, comparado com 100 sequências de cartões ao acaso (31/10/2026):

| Jogo | Concursos com algum prêmio: plano do app | Cartões ao acaso: média (90% das sequências) |
|---|---|---|
| Dia de Sorte (12 cartões) | 104 | 78 (71 a 84) |
| Lotofácil (10 cartões) | 86 | 69 (61 a 78) |
| Timemania (8 cartões) | 39 | 30 (22 a 38) |

O retorno total continuou abaixo do gasto em todos os casos. Isso não depende do método: é como as loterias funcionam.

## IA local (opcional)

1. Instale o [Ollama](https://ollama.com).
2. Rode `ollama pull qwen3:4b-instruct`.
3. Abra o app; o modelo pode ser trocado na aba Harness IA.

As variáveis `OLLAMA_URL` e `OLLAMA_MODEL` mudam o servidor e o modelo padrão.

## Rodar pelo código

Requer Python 3.12.

```bat
pip install -r requirements-dev.txt
python luckharness.py
```

| Comando | O que faz |
|---|---|
| `pytest` | Roda os testes de lógica e de interface |
| `ruff check .` | Roda o lint |
| `build.bat` | Roda os testes, gera o `dist\luckHarness.exe` e, se o Inno Setup 6 estiver instalado, gera o instalador |

### Modalidades

As 9 loterias de escolha de números, com regras conferidas nas páginas oficiais da Caixa em 31/10/2026:

| Modalidade | Detalhes que o app trata |
|---|---|
| Mega-Sena, Quina, Lotofácil | 6 a 20, 5 a 15 e 15 a 20 números; apostas múltiplas viram as apostas simples que contêm |
| Lotomania | 50 números; o "00" é guardado como 100 em todo o app (pode digitar "00"); a faixa de zero acerto conta; `+ espelho` registra também a aposta-espelho (os outros 50, uma aposta a mais) |
| Dupla Sena | Os dois sorteios são conferidos e avaliados separadamente (Laboratório e auditoria usam os dois; frequências e o modelo da Análise usam o 1º) |
| Timemania | Time do Coração à parte dos números, com nomes padronizados. A lista oficial dos 80 times do biênio (Portaria do Ministério do Esporte) vai em `timemania_times.json`; sem ela, o app usa só os times já sorteados no biênio atual |
| Dia de Sorte | Mês da Sorte à parte, cumulativo com as faixas |
| Super Sete | 7 colunas; de 7 a 21 números (vírgula separa os números de uma coluna: `1,5 3 4 0 9 2 7`), preço pela tabela oficial |
| +Milionária | 6 a 12 números e 2 a 6 trevos; as 10 faixas de números × trevos |

A **Loteria Federal** não está no app: ela tem bilhetes de 5 dígitos, frações e prêmios por aproximação, milhar, centena e dezena, sem escolha de números. Os modelos e as métricas do LuckHarness não se aplicam a ela.

As regras de cada modalidade ficam numa ficha única, `MODALIDADES` em `core.py` (universo, números marcados, faixas, preço, trevos, colunas, sorteios, extra, volante e dias); o resto do código pergunta a característica, não o nome do jogo. Os preços têm vigência por concurso (`PRECOS`, pelo cronograma oficial do reajuste de 2025); antes disso o preço da época não está registrado, e o teste no passado avisa que usou o atual. Sem `timemania_times.json`, o Time do Coração é escolhido entre os times já sorteados no biênio atual (todos do volante vigente). Concursos com data que recua em relação ao anterior (erro na fonte) aparecem na auditoria.

### Como os modelos são avaliados

Nenhum modelo prevê os números sorteados, e o app trata qualquer alegação nesse sentido como hipótese a testar:

- **Laboratório:** cada estratégia só vê concursos anteriores (há teste automático para isso). Mostra a diferença para o acaso com intervalo de confiança, o menor efeito que o teste consegue detectar, a estabilidade a cada 100 concursos e a correção de Holm sobre **todas** as execuções já registradas naquele jogo (tabela `experimentos`, com versão, semente e hash dos dados). "Sem evidência de diferença" não quer dizer "igual ao acaso".
- **Modelo de popularidade:** a configuração é escolhida na validação, a decisão de usá-lo vem de um trecho reservado e a nota de teste só é relatada. Em 31/10/2026 o período de teste foi consultado durante o desenvolvimento; o app avisa isso até o teste passar a começar depois dessa data.
- **`experimentos.py`** (pesquisa, requer numpy): redes neurais em walk-forward, com distribuição nula simulada, várias sementes, curvas de treino e um registro em `experimentos/registro.jsonl`. Se o período de teste já foi consultado, o resultado sai marcado como exploratório.
- **Auditoria da rede (31/10/2026, 9 loterias, 20 sementes, critérios registrados antes):** a rede aprende padrões artificiais, mas nas loterias reais nenhuma vantagem sobre o acaso ou sobre modelos simples sobreviveu à correção para múltiplos testes. Em acompanhamento nos concursos futuros: a rede no Dia de Sorte e a frequência histórica na Lotofácil.
- **Teste confiável de uma versão nova:** só em concursos futuros, com modelo e métrica registrados antes ("Registrar para o próximo sorteio").

### Estrutura

| Arquivo | Conteúdo |
|---|---|
| `core.py` | Dados, banco SQLite, login, estatística, conferência, fechamento e alertas |
| `otimizador.py` | Probabilidades exatas, otimizador, cartões espalhados, modelo de popularidade, teste no passado, risco de ruína, auditoria e previsão |
| `laboratorio.py` | Laboratório de estratégias: testes cegos, teste contínuo, registros para sorteios futuros e registro de experimentos |
| `experimentos.py` | Experimentos de previsão reproduzíveis (pesquisa; não entra no app) |
| `aprendizado.py` | Bateria de aprendizado sequencial: rede online e modelos simples, previsões encadeadas por hash, critérios pré-registrados (`experimentos/criterios_aprendizado.md`) e versão congelada (`experimentos/aprendizado/congelado_v1.0.json`) |
| `luckharness.py` | Interface Tkinter |
| `tests/` | Testes pytest, incluindo a abertura de todas as abas |

### De onde vêm os dados

- **Resultados e prêmios pagos:** API oficial da Caixa (`servicebus2` e `servicebus3`). O histórico completo vem de uma vez por uma API espelho, e o app busca na Caixa os concursos que faltarem nela. Em 30/10/2026, os 28.177 concursos das 9 loterias foram conferidos com o histórico do [LotoAgora](https://lotoagora.com.br/downloads/) (cópia da Caixa), sem nenhuma diferença.
- **Preços das apostas:** conforme as páginas oficiais em [loterias.caixa.gov.br](https://loterias.caixa.gov.br/) (reajuste de 09/07/2025). Ficam em `JOGOS`, no `core.py`; atualize ali a cada reajuste.
- **Regras de bolão:** a cota mínima e o máximo de cotas para cada número de dezenas, conforme as mesmas páginas oficiais (consultadas em 30/10/2026). Ficam em `BOLAO`, no `core.py`.

Os dados ficam em `%APPDATA%\luckHarness\`: o banco, as preferências, o log e os modelos treinados. O botão **Backup** no topo do app salva uma cópia do banco.

## Publicar uma versão

1. Mude `__version__` em `core.py`.
2. Faça o commit.
3. Rode `git tag vX.Y.Z && git push --tags`.

O GitHub Actions roda os testes, gera o instalador e publica o Release.

## Licença

[MIT](LICENSE)
