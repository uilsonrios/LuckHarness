# LuckHarness

App para Windows que analisa as loterias da Caixa com matemática honesta: probabilidades exatas, valor esperado, otimização de apostas e gestão de banca. Tem também um assistente de IA local (Ollama).

> **Aviso:** nenhum programa prevê os números sorteados. Os sorteios são aleatórios, e a auditoria do próprio app confirma isso nos dados reais. O LuckHarness ajuda a gastar melhor: escolher o jogo com mais chance de prêmio por real, espalhar os cartões, dividir menos o prêmio e saber quanto dá para gastar. Ele não garante lucro. Jogue com limite.

## Download

Baixe o instalador **`luckHarness-Setup.exe`** em [Releases](https://github.com/uilsonrios/LuckHarness/releases/latest). O app avisa sozinho quando sai uma versão nova.

O Windows pode mostrar o aviso "O Windows protegeu o computador", porque o executável não é assinado. Para instalar, clique em **Mais informações → Executar assim mesmo**.

## O que ele faz

| Aba | Para que serve |
|---|---|
| **Análise** | Frequência, atrasos e somas dos sorteios, e um modelo de aprendizado testado contra o acaso (mostra que não supera o acaso) |
| **Minhas apostas** | Salva as apostas e confere sozinho com os resultados oficiais: dezenas, trevos, Time do Coração, Mês da Sorte e os dois sorteios da Dupla Sena |
| **Fechamento** | Gera cartões com garantia, por exemplo "se 4 das minhas 15 saírem, um cartão faz 3". Uma busca local reduz o número de cartões |
| **Retorno** | Quanto cada jogo devolve em média agora, e alertas quando o prêmio acumulado melhora o retorno |
| **Planejamento** | Simulação de anos de apostas e histórico do retorno |
| **Otimizador** | Escolhe o jogo e o formato (cartões simples, bolão ou fechamento) para o seu orçamento. Também tem risco de ruína, teste no passado, auditoria de aleatoriedade e previsão do acumulado |
| **Banca** | Gastos, prêmios e limite mensal |
| **Harness IA** | Chat com um modelo local que recebe as contas do app como contexto |

### Otimizador: o que dá para melhorar de verdade

- **Escolha do jogo.** Com o mesmo dinheiro, a Lotofácil premia cerca de 245 vezes mais vezes que a Mega-Sena.
- **Cartões espalhados.** Os cartões repetem o mínimo de números entre si. Times, meses e trevos também não se repetem: com 12 cartões no Dia de Sorte, o Mês da Sorte sai em todo concurso.
- **Modelo de popularidade.** É uma rede de 1 camada que aprende o que o público mais joga (números e padrões de escolha: datas, sequências, desenhos no volante, repetir o último resultado), a partir do número real de ganhadores de cada concurso. O botão "Acumulados que saíram" mostra, nos prêmios que ficaram vários concursos sem ganhador, que a maioria era de combinações pouco jogadas. O app só usa o modelo de um jogo quando ele acerta em concursos que não viu no treino. Números pouco jogados dividem menos o prêmio, mas a chance de acertar continua a mesma.
- **Teste no passado.** O plano é jogado nos concursos reais, com os prêmios pagos de verdade, e comparado com cartões ao acaso.

Exemplo dos últimos 104 concursos, com o mesmo gasto:

| Jogo | Concursos com algum prêmio: plano do app | Cartões ao acaso |
|---|---|---|
| Dia de Sorte (12 cartões) | 104 | 76 |
| Timemania (8 cartões) | 42 | 24 |
| Lotofácil (10 cartões) | 76 | 68 |

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

### Estrutura

| Arquivo | Conteúdo |
|---|---|
| `core.py` | Dados (API da Caixa com espelho de reserva), banco SQLite, login, estatística, conferência, fechamento e alertas |
| `otimizador.py` | Probabilidades exatas, otimizador, cartões espalhados, modelo de popularidade, teste no passado, risco de ruína, auditoria e previsão |
| `luckharness.py` | Interface Tkinter |
| `tests/` | Testes pytest, incluindo a abertura de todas as abas |

Os dados ficam em `%APPDATA%\luckHarness\`: o banco, as preferências, o log e os modelos treinados. O botão **Backup** no topo do app salva uma cópia do banco.

## Publicar uma versão

1. Mude `__version__` em `core.py`.
2. Faça o commit.
3. Rode `git tag vX.Y.Z && git push --tags`.

O GitHub Actions roda os testes, gera o instalador e publica o Release.

## Licença

[MIT](LICENSE)
