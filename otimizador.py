"""Otimizador de apostas e modelo de popularidade.

Nada aqui muda a chance de um cartão: o sorteio é uniforme. O que dá para otimizar com matemática é
o jogo e a estrutura da aposta (chance de prêmio por real), a cobertura entre cartões (menos números
repetidos = mais chance de pelo menos um premiar) e a popularidade (números pouco jogados dividem
menos o prêmio). O modelo de popularidade aprende com o número real de ganhadores de cada concurso.
"""
import json, math, os, random

from core import (DATA, EXTRA, JOGOS, MAX_DEZ, brl, custo, db, hypergeom, log, prob_faixa, valor_esperado)

OBJETIVOS = {"frequente": "ganhar algum prêmio com mais frequência",
             "grande": "maior chance do prêmio principal",
             "retorno": "melhor retorno do dinheiro"}
# jogos em que a faixa mais baixa é "N acertos" só com dezenas (dá para treinar a popularidade)
POP_JOGOS = ("megasena", "lotofacil", "quina", "duplasena", "timemania", "diadesorte")


# ---------------- probabilidades exatas ----------------
def _p_trevos(t):
    """+Milionária: P(acertar 0, 1, 2 trevos) marcando t trevos (2 sorteados de 6)."""
    return {k: math.comb(t, k) * math.comb(6 - t, 2 - k) / 15 for k in (0, 1, 2)}


def prob_premio(jogo, n=None, trevos=2):
    """P(ganhar alguma faixa) com uma aposta de n dezenas, contando trevos, time/mês e os dois sorteios."""
    N, K, mn, fx, _ = JOGOS[jogo]
    n = n or mn
    if jogo == "supersete":
        p = sum(prob_faixa(jogo, h) for h in fx)
    elif jogo == "maismilionaria":
        pt = _p_trevos(trevos)
        # 4+ acertos premiam com qualquer trevo; 2 e 3 acertos precisam de pelo menos 1 trevo
        p = sum(hypergeom(N, K, n, h) * (1 if h >= 4 else pt[1] + pt[2]) for h in fx)
    else:
        p = sum(hypergeom(N, K, n, h) for h in fx)
    if jogo in EXTRA:
        p = 1 - (1 - p) * (1 - EXTRA[jogo][2])
    if jogo == "duplasena":
        p = 1 - (1 - p) ** 2
    return p


def prob_principal(jogo, n=None, trevos=2):
    N, K, mn, fx, _ = JOGOS[jogo]
    n = n or mn
    if jogo == "supersete":
        return 0.1 ** 7
    p = hypergeom(N, K, n, K)
    if jogo == "maismilionaria":
        p *= _p_trevos(trevos)[2]
    if jogo == "duplasena":
        p = 1 - (1 - p) ** 2
    return p


def tabela_chances():
    out = [f"{'Jogo':<16}{'Aposta':>9}{'Algum prêmio':>18}{'Prêmio principal':>22}{'Chance por R$ 10':>18}"]
    for j in JOGOS:
        preco, p, pp = JOGOS[j][4], prob_premio(j), prob_principal(j)
        por10 = 1 - (1 - p) ** (10 / preco)
        out.append(f"{j:<16}{brl(preco):>9}{_um_em(p):>18}{_um_em(pp):>22}{por10:>18.1%}")
    return "\n".join(out)


def _um_em(p):
    return "1 em " + (f"{1 / p:,.1f}" if 1 / p < 100 else f"{1 / p:,.0f}").replace(",", "X").replace(
        ".", ",").replace("X", ".")


# ---------------- otimizador ----------------
def opcoes(orcamento, jogos=None):
    """Todas as formas de gastar até 'orcamento' por semana: jogo, dezenas por cartão e quantidade de cartões."""
    out = []
    for j in jogos or JOGOS:
        mn = JOGOS[j][2]
        try:
            ret = valor_esperado(j)["retorno"]
        except Exception:
            log.warning("sem retorno para %s", j, exc_info=True)
            ret = None
        for n in range(mn, (mn if j == "supersete" else MAX_DEZ[j]) + 1):
            tv = [1, 2] if j == "maismilionaria" else []
            c = custo(j, list(range(1, n + 1)), tv)
            k = int(orcamento // c)
            if k < 1:
                break
            p1 = prob_premio(j, n)
            # cartões espalhados ficam perto da independência; é a estimativa usada para comparar opções
            ps = 1 - (1 - p1) ** k
            out.append({"jogo": j, "n": n, "k": k, "custo": c, "gasto": k * c, "p_semana": ps,
                        "p_principal_ano": 1 - (1 - prob_principal(j, n)) ** (k * 52), "retorno": ret})
    return out


def otimizar(orcamento, objetivo="frequente", jogos=None, top=5):
    if orcamento <= 0:
        raise ValueError("Informe quanto quer gastar por semana.")
    ops = opcoes(orcamento, jogos)
    if not ops:
        raise ValueError(f"Com {brl(orcamento)} por semana não dá para fazer nenhuma aposta.")
    chave = {"frequente": lambda o: (o["p_semana"], -o["gasto"]),
             "grande": lambda o: (o["p_principal_ano"], o["p_semana"]),
             "retorno": lambda o: ((o["retorno"] or 0), o["p_semana"])}[objetivo]
    melhor_por_jogo = {}
    for o in sorted(ops, key=chave, reverse=True):
        melhor_por_jogo.setdefault(o["jogo"], o)
    return list(melhor_por_jogo.values())[:top]


def texto_plano(orcamento, objetivo, ranking):
    out = [f"=== OTIMIZADOR: {brl(orcamento)} por semana | objetivo: {OBJETIVOS[objetivo]} ===", "",
           f"{'#':<3}{'Jogo':<15}{'Plano':<26}{'Gasto':>11}{'Prêmio na semana':>18}{'1 a cada':>11}"
           f"{'Principal/ano':>15}{'Retorno':>9}"]
    for i, o in enumerate(ranking, 1):
        plano = f"{o['k']} cartão(ões) de {o['n']}"
        semanas = 1 / o["p_semana"]
        cada = f"{semanas:.1f} sem" if semanas < 100 else f"{semanas / 52:,.0f} anos".replace(",", ".")
        ret = f"{o['retorno']:.0%}" if o["retorno"] is not None else "-"
        out.append(f"{i:<3}{o['jogo']:<15}{plano:<26}{brl(o['gasto']):>11}{o['p_semana']:>18.1%}{cada:>11}"
                   f"{_um_em(o['p_principal_ano']) if o['p_principal_ano'] < .01 else format(o['p_principal_ano'], '.1%'):>15}"
                   f"{ret:>9}")
    b = ranking[0]
    anual = b["gasto"] * 52
    volta = (b["retorno"] or 0) * anual
    out += ["", f"Recomendado: {b['jogo']}, {b['k']} cartão(ões) de {b['n']} dezenas por semana.",
            f"Em um ano: gasta {brl(anual)}, a matemática devolve em média {brl(volta)} em prêmios "
            f"(saldo esperado {brl(volta - anual)}).",
            "Os cartões abaixo são espalhados (o mínimo de números repetidos entre eles) e, se houver modelo de "
            "popularidade treinado, evitam os números que o público mais joga.",
            "Nenhum plano aumenta a chance de cada cartão nem garante lucro: ele tira o máximo de cada real."]
    return "\n".join(out)


# ---------------- cartões espalhados ----------------
def cartoes_espalhados(jogo, k, n=None, pesos=None, rng=random):
    """k cartões de n dezenas usando cada número o mais igualmente possível (cobertura balanceada).
    Empates são decididos pelos números menos populares e, por fim, ao acaso."""
    N, _, mn = JOGOS[jogo][:3]
    n = n or mn
    pesos = pesos or {}
    if jogo == "supersete":
        uso = [[0] * 10 for _ in range(7)]
        cards = []
        for _ in range(k):
            c = []
            for col in range(7):
                d = min(range(10), key=lambda x: (uso[col][x], rng.random()))
                uso[col][d] += 1
                c.append(d)
            cards.append(c)
        return cards
    uso = {i: 0 for i in range(1, N + 1)}
    pares = {}
    cards = []
    for _ in range(k):
        c = []
        for _ in range(n):
            livres = [i for i in uso if i not in c]
            # menos usado > menos par repetido com o que já está no cartão > menos popular > acaso
            d = min(livres, key=lambda x: (uso[x], sum(pares.get((min(x, y), max(x, y)), 0) for y in c),
                                           pesos.get(x, 0), rng.random()))
            c.append(d)
        for x in c:
            uso[x] += 1
        for i, x in enumerate(c):
            for y in c[i + 1:]:
                pares[(min(x, y), max(x, y))] = pares.get((min(x, y), max(x, y)), 0) + 1
        cards.append(sorted(c))
    return cards


def _premiou(jogo, card, sorteio, trevos_card=(), trevos_sorteio=()):
    fx = JOGOS[jogo][3]
    if jogo == "supersete":
        return sum(a == b for a, b in zip(card, sorteio)) in fx
    h = len(set(card) & sorteio)
    if jogo == "maismilionaria":
        th = len(set(trevos_card) & trevos_sorteio)
        return h >= 4 or (h in (2, 3) and th >= 1)
    return h in fx


def simular_cobertura(jogo, cards, n_sim=20000, rng=random, trevos=None):
    """P(pelo menos um cartão premiar nas dezenas) por Monte Carlo."""
    N, K = JOGOS[jogo][:2]
    trevos = trevos or [[1, 2]] * len(cards)
    ok = 0
    for _ in range(n_sim):
        if jogo == "supersete":
            s = [rng.randrange(10) for _ in range(7)]
        else:
            s = set(rng.sample(range(1, N + 1), K))
        ts = set(rng.sample(range(1, 7), 2)) if jogo == "maismilionaria" else set()
        ok += any(_premiou(jogo, c, s, t, ts) for c, t in zip(cards, trevos))
    return ok / n_sim


def comparar_cobertura(jogo, k, n=None, n_sim=20000, seed=7):
    """Cartões espalhados x cartões ao acaso, com os mesmos sorteios simulados."""
    N, _, mn = JOGOS[jogo][:3]
    n = n or mn
    rng = random.Random(seed)
    esp = cartoes_espalhados(jogo, k, n, pesos_popularidade(jogo), rng)
    if jogo == "supersete":
        aleat = [[rng.randrange(10) for _ in range(7)] for _ in range(k)]
    else:
        aleat = [sorted(rng.sample(range(1, N + 1), n)) for _ in range(k)]
    tv = [sorted(rng.sample(range(1, 7), 2)) for _ in range(k)] if jogo == "maismilionaria" else None
    tv_esp = [[1 + (2 * i) % 6, 1 + (2 * i + 1) % 6] for i in range(k)] if jogo == "maismilionaria" else None
    p_esp = simular_cobertura(jogo, esp, n_sim, random.Random(seed + 1), tv_esp)
    p_ale = simular_cobertura(jogo, aleat, n_sim, random.Random(seed + 1), tv)
    return esp, tv_esp, p_esp, p_ale


# ---------------- modelo de popularidade ----------------
def _pop_path(jogo):
    return os.path.join(DATA, f"popularidade_{jogo}.json")


def pesos_popularidade(jogo):
    try:
        with open(_pop_path(jogo), encoding="utf8") as f:
            m = json.load(f)
        return {int(k): v for k, v in m["pesos"].items()} if m.get("confiavel") else None
    except (OSError, ValueError, KeyError):
        return None


def _amostras(jogo):
    """(dezenas sorteadas, alvo) por concurso: log(ganhadores observados / ganhadores esperados pelo acaso)
    na faixa com mais ganhadores. Se os números sorteados forem populares, sai mais gente ganhando."""
    N, K, mn, fx, preco = JOGOS[jogo]
    rows = db().execute("SELECT dezenas, meta FROM draws WHERE jogo=? AND meta IS NOT NULL ORDER BY concurso",
                        (jogo,)).fetchall()
    out = []
    faixa = min(h for h in fx if h > 0)
    for dz, meta in rows:
        m = json.loads(meta)
        pr, gan, vendas = m.get("premios", []), m.get("ganhadores", []), m.get("arrecadado") or 0
        if not gan or not vendas:
            continue
        g = next((g for (desc, _), g in zip(pr, gan) if desc.lower().startswith(f"{faixa} acerto")), None)
        if g is None:
            continue
        esperado = vendas / preco * hypergeom(N, K, mn, faixa)
        if esperado < 5:
            continue
        out.append((json.loads(dz), math.log((g + 0.5) / esperado)))
    return out


def treinar_popularidade(jogo, epocas=200, lr=0.1, l2=1e-3, teste=0.2):
    """Rede de 1 camada (regressão log-linear) treinada em ordem cronológica. A nota é a correlação, nos
    concursos mais recentes que o modelo não viu, entre a soma dos pesos das dezenas sorteadas e o excesso de
    ganhadores: mede só o efeito dos números, não o nível geral de vendas. Só é usada se passar no teste."""
    if jogo not in POP_JOGOS:
        raise ValueError(f"Popularidade só para: {', '.join(POP_JOGOS)}.")
    dados = _amostras(jogo)
    if len(dados) < 200:
        raise ValueError(f"{jogo}: poucos concursos com número de ganhadores ({len(dados)}). "
                         "Baixe os resultados de novo e tente.")
    N, K = JOGOS[jogo][:2]
    lr /= K   # cada concurso ativa K pesos de uma vez
    corte = int(len(dados) * (1 - teste))
    treino, prova = dados[:corte], dados[corte:]
    b = sum(y for _, y in treino) / len(treino)
    w = {i: 0.0 for i in range(1, N + 1)}
    rng = random.Random(1)
    ordem = list(range(len(treino)))
    for _ in range(epocas):
        rng.shuffle(ordem)
        for t in ordem:
            dz, y = treino[t]
            erro = b + sum(w[x] for x in dz) - y
            b -= lr * erro
            for x in dz:
                w[x] -= lr * (erro + l2 * w[x])

    def r2(conj):
        xs = [sum(w[x] for x in dz) for dz, _ in conj]
        ys = [y for _, y in conj]
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        sxy = sum((a - mx) * (c - my) for a, c in zip(xs, ys))
        sxx, syy = sum((a - mx) ** 2 for a in xs), sum((c - my) ** 2 for c in ys)
        return (sxy * sxy / (sxx * syy) if sxx and syy else 0) * (1 if sxy > 0 else -1)

    r2_teste = r2(prova)
    # com ~400 concursos de teste, r² > 5% corresponde a p < 0,0001: dificilmente é acaso
    confiavel = r2_teste > 0.05
    media = sum(w.values()) / N
    w = {i: v - media for i, v in w.items()}
    datas = (sum(w[i] for i in range(1, min(N, 31) + 1)) / min(N, 31) -
             (sum(w[i] for i in range(32, N + 1)) / (N - 31) if N > 31 else 0))
    modelo = {"jogo": jogo, "pesos": w, "r2_teste": r2_teste, "r2_treino": r2(treino), "confiavel": confiavel,
              "concursos": len(dados), "efeito_datas": datas}
    with open(_pop_path(jogo), "w", encoding="utf8") as f:
        json.dump(modelo, f)
    return modelo


def texto_popularidade(m):
    jogo, w = m["jogo"], m["pesos"]
    mais = sorted(w, key=w.get, reverse=True)[:10]
    menos = sorted(w, key=w.get)[:10]
    fx = lambda v: f"{math.exp(v):.2f}x"
    out = [f"=== MODELO DE POPULARIDADE: {jogo.upper()} ({m['concursos']} concursos) ===",
           "Aprende quais números o público mais joga comparando, em cada concurso, quantas pessoas ganharam "
           "com quantas deveriam ganhar pelo acaso.", "",
           f"Poder explicativo no teste (r², concursos recentes que o modelo não viu): {m['r2_teste']:.1%}"
           f"  |  no treino: {m['r2_treino']:.1%}",
           "  -> " + ("CONFIÁVEL: o modelo explica parte real da divisão de prêmios e passa a ser usado nos cartões."
                      if m["confiavel"] else
                      "NÃO CONFIÁVEL: abaixo de 5% no teste. O app não usa estes pesos (seria ruído)."), "",
           "Mais jogados (dividem mais o prêmio): " + ", ".join(f"{x:02d} ({fx(w[x])})" for x in mais),
           "Menos jogados (dividem menos):        " + ", ".join(f"{x:02d} ({fx(w[x])})" for x in menos)]
    if JOGOS[jogo][0] > 31:
        out.append(f"Efeito 'datas de aniversário' (1 a 31 x 32+): {fx(m['efeito_datas'])} mais ganhadores por número.")
    out += ["", "Isto não muda a chance de acertar: muda quantas pessoas dividem o prêmio com você se acertar."]
    return "\n".join(out)


def fator_divisao(jogo, card):
    """Quantas vezes mais (ou menos) gente que a média deve ter o mesmo cartão, pelo modelo treinado."""
    w = pesos_popularidade(jogo)
    if not w or jogo == "supersete":
        return None
    return math.exp(sum(w.get(x, 0) for x in card))
