"""Otimizador de apostas e modelo de popularidade.

Nada aqui muda a chance de um cartão: o sorteio é uniforme. O que dá para otimizar com matemática é
o jogo e a estrutura da aposta (chance de prêmio por real), a cobertura entre cartões (menos números
repetidos = mais chance de pelo menos um premiar) e a popularidade (números pouco jogados dividem
menos o prêmio). O modelo de popularidade aprende com o número real de ganhadores de cada concurso.
"""
import json, math, os, random

from collections import Counter

from core import (DATA, EXTRA, JOGOS, MAX_DEZ, MESES, _sorteios, brl, custo, db, historico_retorno, hypergeom, log,
                  premio, premios_medios, prob_faixa, valor_esperado)

OBJETIVOS = {"frequente": "ganhar algum prêmio com mais frequência",
             "grande": "maior chance do prêmio principal",
             "retorno": "melhor retorno do dinheiro"}
# jogos em que a faixa mais baixa é "N acertos" só com dezenas (dá para treinar a popularidade)
POP_JOGOS = ("megasena", "lotofacil", "quina", "duplasena", "timemania", "diadesorte")


# ---------------- probabilidades exatas ----------------
def _p_trevos(t):
    """+Milionária: P(acertar 0, 1, 2 trevos) marcando t trevos (2 sorteados de 6)."""
    return {k: math.comb(t, k) * math.comb(6 - t, 2 - k) / 15 for k in (0, 1, 2)}


def _sem_extra(jogo, k):
    """P(nenhum de k cartões acertar o time/mês), com times/meses diferentes em cada cartão."""
    if jogo not in EXTRA:
        return 1.0
    opcoes_extra = round(1 / EXTRA[jogo][2])
    return max(0.0, 1 - min(k, opcoes_extra) / opcoes_extra)


def prob_cartoes(jogo, p_dezenas_1, k):
    """P(algum de k cartões espalhados premiar): dezenas quase independentes, time/mês sem repetir."""
    p_dz = 1 - (1 - p_dezenas_1) ** k
    if jogo == "duplasena":
        p_dz = 1 - (1 - p_dz) ** 2
    return 1 - (1 - p_dz) * _sem_extra(jogo, k)


def prob_premio(jogo, n=None, trevos=2, extra=True):
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
    if jogo == "duplasena":
        p = 1 - (1 - p) ** 2
    if extra and jogo in EXTRA:
        p = 1 - (1 - p) * (1 - EXTRA[jogo][2])
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
            pd = prob_premio(j, n, extra=False)
            if j == "duplasena":
                pd = 1 - (1 - pd) ** 0.5   # prob_cartoes aplica os dois sorteios
            ps = prob_cartoes(j, pd, k)
            out.append({"jogo": j, "tipo": "simples", "n": n, "k": k, "custo": c, "gasto": k * c, "p_semana": ps,
                        "p_principal_ano": 1 - (1 - prob_principal(j, n)) ** (k * 52), "retorno": ret,
                        "plano": f"{k} cartão(ões) de {n}"})
        out += _opcoes_bolao(j, orcamento, ret) + _opcoes_fechamento(j, orcamento, ret)
    return out


def _opcoes_bolao(j, orcamento, ret):
    """Uma cota por semana de um bolão com mais dezenas. Divide custo e prêmio: a chance de o bolão premiar é
    a da aposta grande inteira, mas você recebe 1/cotas de cada prêmio."""
    if j == "supersete":
        return []
    mn, out = JOGOS[j][2], []
    for n in range(mn + 1, MAX_DEZ[j] + 1):
        c = custo(j, list(range(1, n + 1)), [1, 2] if j == "maismilionaria" else [])
        q = max(2, math.ceil(c / orcamento))
        if q > 100:   # limite prático de cotas
            break
        out.append({"jogo": j, "tipo": "bolão", "n": n, "k": 1, "cotas": q, "custo": c, "gasto": c / q,
                    "p_semana": prob_premio(j, n), "p_principal_ano": 1 - (1 - prob_principal(j, n) / q) ** 52,
                    "retorno": ret, "plano": f"bolão {n} dez., 1/{q} cota"})
    return out


def _opcoes_fechamento(j, orcamento, ret):
    """Fechamento com garantia na faixa mais baixa: escolhe v dezenas e cobre todas as combinações de t
    sorteadas entre elas. Só entra se o cálculo for rápido e couber no orçamento."""
    if j in ("supersete", "lotomania", "maismilionaria"):   # +Milionária depende dos trevos
        return []
    N, K, mn, fx, preco = JOGOS[j]
    t = min(h for h in fx if h > 0)
    out = []
    for v in range(mn + 1, min(N, mn + 6) + 1):
        if math.comb(v, t) > 3000:
            break
        try:
            from core import fechamento
            cards = fechamento(list(range(1, v + 1)), mn, t, t)
        except ValueError:
            break
        gasto = len(cards) * preco
        if gasto > orcamento:
            break
        # garantia: se t das v dezenas saírem, um cartão faz t acertos; fora disso ainda pode premiar
        p_gar = sum(hypergeom(N, K, v, h) for h in range(t, min(v, K) + 1))
        p = simular_cobertura(j, cards, 4000, random.Random(v))
        if j == "duplasena":
            p = 1 - (1 - p) ** 2
        p = 1 - (1 - p) * _sem_extra(j, len(cards))
        out.append({"jogo": j, "tipo": "fechamento", "n": mn, "k": len(cards), "v": v, "t": t, "custo": preco,
                    "gasto": gasto, "p_semana": p, "p_garantia": p_gar, "p_principal_ano": 1 - (1 - prob_principal(j) * len(cards)) ** 52,
                    "retorno": ret, "cards_base": cards,
                    "plano": f"fechamento {v} dez., {len(cards)} cart."})
    return out


def otimizar(orcamento, objetivo="frequente", jogos=None, top=8):
    if orcamento <= 0:
        raise ValueError("Informe quanto quer gastar por semana.")
    ops = opcoes(orcamento, jogos)
    if not ops:
        raise ValueError(f"Com {brl(orcamento)} por semana não dá para fazer nenhuma aposta.")
    chave = {"frequente": lambda o: (o["p_semana"], -o["gasto"]),
             "grande": lambda o: (o["p_principal_ano"], o["p_semana"]),
             "retorno": lambda o: ((o["retorno"] or 0), o["p_semana"])}[objetivo]
    melhor = {}
    for o in sorted(ops, key=chave, reverse=True):
        melhor.setdefault((o["jogo"], o["tipo"]), o)
    return list(melhor.values())[:top]


def texto_plano(orcamento, objetivo, ranking):
    out = [f"=== OTIMIZADOR: {brl(orcamento)} por semana | objetivo: {OBJETIVOS[objetivo]} ===", "",
           f"{'#':<3}{'Jogo':<15}{'Plano':<28}{'Gasto':>11}{'Prêmio na semana':>18}{'1 a cada':>11}"
           f"{'Principal/ano':>15}{'Retorno':>9}"]
    for i, o in enumerate(ranking, 1):
        plano = o["plano"]
        semanas = 1 / o["p_semana"]
        cada = f"{semanas:.1f} sem" if semanas < 100 else f"{semanas / 52:,.0f} anos".replace(",", ".")
        ret = f"{o['retorno']:.0%}" if o["retorno"] is not None else "-"
        out.append(f"{i:<3}{o['jogo']:<15}{plano:<28}{brl(o['gasto']):>11}{o['p_semana']:>18.1%}{cada:>11}"
                   f"{_um_em(o['p_principal_ano']) if o['p_principal_ano'] < .01 else format(o['p_principal_ano'], '.1%'):>15}"
                   f"{ret:>9}")
    b = ranking[0]
    anual = b["gasto"] * 52
    volta = (b["retorno"] or 0) * anual
    if b["tipo"] == "bolão":
        rec = (f"uma cota (1/{b['cotas']}) de um bolão de {b['n']} dezenas, {brl(b['gasto'])} por semana. "
               f"Se o bolão ganhar, você recebe 1/{b['cotas']} do prêmio")
    elif b["tipo"] == "fechamento":
        rec = (f"fechamento de {b['v']} dezenas em {b['k']} cartões: se {b['t']} das {b['v']} saírem, um cartão "
               f"faz {b['t']} acertos (garantido; isso acontece em {b['p_garantia']:.0%} dos concursos)")
    else:
        rec = f"{b['k']} cartão(ões) de {b['n']} dezenas por semana"
    out += ["", f"Recomendado: {b['jogo']}, {rec}."]
    if b["jogo"] in EXTRA:
        desc = EXTRA[b["jogo"]][1]
        try:
            val = premios_medios(b["jogo"])[0].get(desc)
        except Exception:
            val = None
        out.append(f"Atenção: boa parte da frequência vem do {desc} (cada cartão com um diferente), que paga pouco"
                   + (f": cerca de {brl(val)}." if val else "."))
    out += [
            f"Em um ano: gasta {brl(anual)}, a matemática devolve em média {brl(volta)} em prêmios "
            f"(saldo esperado {brl(volta - anual)}).",
            "Os cartões abaixo são espalhados (o mínimo de números repetidos entre eles) e, se houver modelo de "
            "popularidade treinado, evitam os números que o público mais joga.",
            "Bolão: 'Principal/ano' é o equivalente à sua cota (o bolão ganha mais vezes, mas você recebe só a "
            "sua parte).",
            "Nenhum plano aumenta a chance de cada cartão nem garante lucro: ele tira o máximo de cada real."]
    return "\n".join(out)


# ---------------- cartões espalhados ----------------
def extras_espalhados(jogo, k, rng=random):
    """Trevos/time/mês sem repetir entre os cartões: com 12 cartões no Dia de Sorte, todos os meses ficam
    cobertos e o Mês da Sorte sai garantido em todo concurso."""
    if jogo == "maismilionaria":
        pares = [[a, b] for a in range(1, 7) for b in range(a + 1, 7)]
        rng.shuffle(pares)
        return [pares[i % 15] for i in range(k)]
    if jogo == "diadesorte":
        meses = MESES[:]
        rng.shuffle(meses)
        return [[meses[i % 12]] for i in range(k)]
    if jogo == "timemania":
        from core import times_conhecidos
        times = times_conhecidos()
        if not times:
            return [[] for _ in range(k)]
        esc = rng.sample(times, min(k, len(times)))
        return [[esc[i % len(esc)]] for i in range(k)]
    return [[] for _ in range(k)]



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
    tv_esp = extras_espalhados(jogo, k, rng) if jogo == "maismilionaria" else None
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


# ---------------- treino automático ----------------
def treinar_todos(avisar=print):
    """Retreina a popularidade de todos os jogos (roda depois de baixar resultados)."""
    ok = []
    for j in POP_JOGOS:
        try:
            m = treinar_popularidade(j)
            ok.append(f"{j} {'ok' if m['confiavel'] else 'sem padrão'} ({m['r2_teste']:.0%})")
        except ValueError as e:
            log.info("popularidade %s: %s", j, e)
        except Exception:
            log.warning("popularidade %s falhou", j, exc_info=True)
    if ok:
        avisar("Modelo de popularidade atualizado: " + ", ".join(ok))
    return ok


# ---------------- teste no passado ----------------
def _ganho_cartao(jogo, card, tv, d, meta):
    """Prêmio real de um cartão num sorteio, com os valores pagos naquele concurso.
    Cartão com mais dezenas = todas as apostas simples contidas nele."""
    N, K, mn, fx, _ = JOGOS[jogo]
    pr = meta.get("premios", [])
    ganho, faixas = 0.0, []
    if jogo == "supersete":
        h = sum(a == b for a, b in zip(card, d))
        p = premio(jogo, h, 0, pr) if h in fx else None
        return (p[1] or 0, [p[0]]) if p else (0.0, [])
    h = len(set(card) & set(d))
    th = len(set(tv) & set(meta.get("trevos", []))) if jogo == "maismilionaria" else 0
    n = len(card)
    for j in fx:
        qtd = math.comb(h, j) * math.comb(n - h, mn - j)
        if not qtd:
            continue
        p = premio(jogo, j, th, pr)
        if p and p[1]:
            ganho += qtd * p[1]
            faixas.append(p[0])
    if jogo in EXTRA and tv and meta.get(EXTRA[jogo][0]) == tv[0]:
        val = dict((a, b) for a, b in pr).get(EXTRA[jogo][1]) or 0
        ganho += val
        faixas.append(EXTRA[jogo][1])
    return ganho, faixas


def _extra_acaso(jogo, rng):
    if jogo == "diadesorte":
        return [rng.choice(MESES)]
    from core import times_conhecidos
    times = times_conhecidos()
    return [rng.choice(times)] if times else []


def testar_no_passado(jogo, k, n=None, concursos=104, retorno_min=0.0, seed=7):
    """Joga o plano (k cartões espalhados de n dezenas) em cada um dos últimos concursos, com os prêmios
    reais pagos. Com retorno_min, só joga quando o retorno estimado antes do sorteio passava do mínimo.
    Compara com cartões ao acaso e com 'jogar sempre'."""
    N, K, mn, _, _ = JOGOS[jogo]
    n = n or mn
    rows = db().execute("SELECT concurso, dezenas, meta FROM draws WHERE jogo=? AND meta IS NOT NULL "
                        "ORDER BY concurso DESC LIMIT ?", (jogo, concursos)).fetchall()[::-1]
    if len(rows) < 10:
        raise ValueError(f"{jogo}: baixe os resultados primeiro.")
    filtro = None
    if retorno_min > 0:
        filtro = {c: r for c, _, r in historico_retorno(jogo, concursos + 1)}
    rng = random.Random(seed)
    preco = custo(jogo, list(range(1, n + 1)), [1, 2] if jogo == "maismilionaria" else [])

    def plano(espalhado):
        res = {"jogados": 0, "gasto": 0.0, "ganho": 0.0, "com_premio": 0, "maior": 0.0, "faixas": Counter()}
        for conc, _, _ in rows:
            if filtro is not None and filtro.get(conc, 0) < retorno_min:
                continue
            if espalhado:
                cards = cartoes_espalhados(jogo, k, n, pesos_popularidade(jogo), rng)
            elif jogo == "supersete":
                cards = [[rng.randrange(10) for _ in range(7)] for _ in range(k)]
            else:
                cards = [sorted(rng.sample(range(1, N + 1), n)) for _ in range(k)]
            if espalhado:
                tvs = extras_espalhados(jogo, k, rng)
            elif jogo == "maismilionaria":
                tvs = [sorted(rng.sample(range(1, 7), 2)) for _ in cards]
            elif jogo in EXTRA:
                tvs = [e for _ in cards for e in [_extra_acaso(jogo, rng)]]
            else:
                tvs = [[] for _ in cards]
            g_conc = 0.0
            for _, d, meta in _sorteios(jogo, [r for r in rows if r[0] == conc]):
                for c, t in zip(cards, tvs):
                    g, fxs = _ganho_cartao(jogo, c, t, d, meta)
                    g_conc += g
                    res["faixas"].update(fxs)
            res["jogados"] += 1
            res["gasto"] += k * preco
            res["ganho"] += g_conc
            res["com_premio"] += g_conc > 0
            res["maior"] = max(res["maior"], g_conc)
        return res

    return {"jogo": jogo, "k": k, "n": n, "concursos": len(rows), "retorno_min": retorno_min,
            "esp": plano(True), "acaso": plano(False)}


def texto_teste(r):
    def bloco(nome, x):
        if not x["jogados"]:
            return [f"{nome}: nenhum concurso passou do retorno mínimo."]
        saldo = x["ganho"] - x["gasto"]
        return [f"{nome}:",
                f"  jogou {x['jogados']} concursos | gastou {brl(x['gasto'])} | ganhou {brl(x['ganho'])} | "
                f"saldo {brl(saldo)} ({x['ganho'] / x['gasto']:.0%} de volta)",
                f"  premiou em {x['com_premio']} concursos ({x['com_premio'] / x['jogados']:.0%}) | "
                f"maior prêmio num concurso: {brl(x['maior'])}",
                "  faixas: " + (", ".join(f"{f} x{q}" for f, q in x["faixas"].most_common()) or "nenhuma")]
    filtro = (f", só quando o retorno estimado passava de {r['retorno_min']:.0%}" if r["retorno_min"] else "")
    out = [f"=== TESTE NO PASSADO: {r['jogo'].upper()}, {r['k']} cartão(ões) de {r['n']} dezenas por concurso, "
           f"últimos {r['concursos']} concursos{filtro} ===", "",
           "Prêmios reais pagos em cada concurso. Os cartões são gerados como o app geraria naquele dia "
           "(o sorteio ainda não era conhecido).", ""]
    out += bloco("Plano do Otimizador (cartões espalhados)", r["esp"]) + [""]
    out += bloco("Mesmo gasto com cartões ao acaso", r["acaso"])
    out += ["", "Um teste no passado mostra o que teria acontecido, não o que vai acontecer. Variações entre os "
                "dois planos em poucos concursos são em grande parte sorte; o que se mantém no longo prazo é o "
                "retorno médio de cada jogo (aba Retorno)."]
    return "\n".join(out)


# ---------------- risco de ruína ----------------
def _faixas_plano(plano):
    """(prob, prêmio) por aposta simples, quantas apostas simples o plano faz por semana e a fração do prêmio
    que é sua (bolão)."""
    from core import _sorteia_ganho, apostas_simples
    j = plano["jogo"]
    ev = valor_esperado(j)
    faixas = [(p, v) for _, p, v, _ in ev["linhas"]]
    tv = [1, 2] if j == "maismilionaria" else []
    n_simples = apostas_simples(j, list(range(plano["n"])), tv) * (1 if plano["tipo"] == "bolão" else plano["k"])
    fator = 1 / plano["cotas"] if plano["tipo"] == "bolão" else 1.0
    if j == "duplasena":
        n_simples *= 2
    return faixas, n_simples, fator, _sorteia_ganho


def risco_ruina(plano, banca, anos=1, n_sim=2000, seed=3):
    """Simula semana a semana: paga o plano, recebe os prêmios sorteados. Ruína = a banca não paga mais a
    aposta da semana."""
    faixas, n_simples, fator, sorteia = _faixas_plano(plano)
    gasto, semanas = plano["gasto"], 52 * anos
    estado = random.getstate()
    random.seed(seed)
    try:
        quebras, finais = [], []
        for _ in range(n_sim):
            b, quebrou = banca, None
            for s in range(1, semanas + 1):
                if b < gasto:
                    quebrou = s
                    break
                b += sorteia(faixas, n_simples) * fator - gasto
            quebras.append(quebrou)
            finais.append(b)
    finally:
        random.setstate(estado)
    q = sorted(x for x in quebras if x)
    finais.sort()
    return {"banca": banca, "gasto": gasto, "semanas": semanas, "p_ruina": len(q) / n_sim,
            "mediana_quebra": q[len(q) // 2] if q else None, "sem_premio": int(banca // gasto),
            "final_mediana": finais[n_sim // 2], "final_p10": finais[n_sim // 10],
            "final_p90": finais[n_sim * 9 // 10], "p_acima": sum(x > banca for x in finais) / n_sim}


def texto_ruina(plano, r):
    out = [f"=== RISCO DE RUÍNA: banca de {brl(r['banca'])}, {plano['jogo']} ({plano['plano']}), "
           f"{brl(r['gasto'])} por semana, {r['semanas']} semanas ===", "",
           f"Sem nenhum prêmio a banca duraria {r['sem_premio']} semanas.",
           f"Chance de a banca acabar antes de {r['semanas']} semanas: {r['p_ruina']:.0%}"
           + (f" (metade dos casos quebra até a semana {r['mediana_quebra']})" if r["mediana_quebra"] else ""),
           f"Banca no fim: típico {brl(r['final_mediana'])} | 10% piores {brl(r['final_p10'])} ou menos | "
           f"10% melhores {brl(r['final_p90'])} ou mais",
           f"Chance de terminar com mais do que começou: {r['p_acima']:.1%}", "",
           "Critério de Kelly (a fórmula clássica de quanto apostar): com retorno abaixo de 100%, a fração ideal "
           "da banca é zero. Trate o gasto como entretenimento com limite fixo, não como investimento."]
    return "\n".join(out)


# ---------------- auditoria de aleatoriedade ----------------
def _gamma_q(a, x):
    """Função gama incompleta superior regularizada Q(a, x) (Numerical Recipes)."""
    if x <= 0:
        return 1.0
    gln = math.lgamma(a)
    if x < a + 1:
        s = term = 1 / a
        ap = a
        for _ in range(1000):
            ap += 1
            term *= x / ap
            s += term
            if abs(term) < abs(s) * 1e-12:
                break
        return 1 - s * math.exp(-x + a * math.log(x) - gln)
    b, c, d = x + 1 - a, 1e300, 1 / (x + 1 - a)
    h = d
    for i in range(1, 1000):
        an = -i * (i - a)
        b += 2
        d = an * d + b
        d = 1e-300 if abs(d) < 1e-300 else d
        c = b + an / c
        c = 1e-300 if abs(c) < 1e-300 else c
        d = 1 / d
        h *= d * c
        if abs(d * c - 1) < 1e-12:
            break
    return math.exp(-x + a * math.log(x) - gln) * h


def qui_quadrado(obs, esp):
    """Estatística, valor-p e graus de liberdade do qui-quadrado (classes com esperado < 5 são agrupadas)."""
    o2, e2, ao, ae = [], [], 0.0, 0.0
    for o_, e_ in zip(obs, esp):
        ao, ae = ao + o_, ae + e_
        if ae >= 5:
            o2.append(ao)
            e2.append(ae)
            ao = ae = 0.0
    if ae and e2:
        o2[-1] += ao
        e2[-1] += ae
    if len(e2) < 2:
        return 0.0, 1.0, 0
    x2 = sum((a - b) ** 2 / b for a, b in zip(o2, e2))
    gl = len(e2) - 1
    return x2, _gamma_q(gl / 2, x2 / 2), gl


def auditoria(jogo):
    """Testes clássicos de aleatoriedade nos sorteios reais, com correção de Holm para vários testes."""
    from core import load_draws
    N, K = JOGOS[jogo][:2]
    draws = [d for _, d in sorted(load_draws(jogo, 100000))]
    T = len(draws)
    if T < 100:
        raise ValueError(f"{jogo}: baixe os resultados primeiro (há {T} concursos).")
    testes = []
    if jogo == "supersete":
        for col in range(7):
            obs = [sum(d[col] == x for d in draws) for x in range(10)]
            testes.append((f"coluna {col + 1}: cada dígito sai igual", *qui_quadrado(obs, [T / 10] * 10)))
    else:
        obs = [sum(x in d for d in draws) for x in range(1, N + 1)]
        testes.append(("frequência de cada dezena", *qui_quadrado(obs, [T * K / N] * N)))
        pares = [sum(1 for x in d if x % 2 == 0) for d in draws]
        esp = [T * hypergeom(N, N // 2, K, h) for h in range(K + 1)]
        testes.append(("quantidade de pares por sorteio", *qui_quadrado([pares.count(h) for h in range(K + 1)], esp)))
        rep = [len(set(a) & set(b)) for a, b in zip(draws, draws[1:])]
        esp = [(T - 1) * hypergeom(N, K, K, h) for h in range(K + 1)]
        testes.append(("dezenas repetidas do concurso anterior",
                       *qui_quadrado([rep.count(h) for h in range(K + 1)], esp)))
        gaps, ultimo = [], {}
        for t, d in enumerate(draws):
            for x in d:
                if x in ultimo:
                    gaps.append(t - ultimo[x])
                ultimo[x] = t
        p = K / N
        mx = 4 * round(1 / p) + 1
        obs = [sum(1 for g in gaps if g == i) for i in range(1, mx)] + [sum(1 for g in gaps if g >= mx)]
        esp = [len(gaps) * p * (1 - p) ** (i - 1) for i in range(1, mx)] + [len(gaps) * (1 - p) ** (mx - 1)]
        testes.append(("intervalo entre aparições (atraso)", *qui_quadrado(obs, esp)))
        somas = [sum(d) for d in draws]
        media, var = K * (N + 1) / 2, K * (N - K) * (N + 1) / 12
        z = (sum(somas) / T - media) / math.sqrt(var / T)
        testes.append(("soma média das dezenas", z * z, math.erfc(abs(z) / math.sqrt(2)), 1))
    ordem = sorted(range(len(testes)), key=lambda i: testes[i][2])
    m, ajust, maior = len(testes), [0.0] * len(testes), 0.0
    for r, i in enumerate(ordem):   # correção de Holm
        maior = max(maior, min(1.0, (m - r) * testes[i][2]))
        ajust[i] = maior
    return {"jogo": jogo, "concursos": T, "testes": [(*t, a) for t, a in zip(testes, ajust)]}


def texto_auditoria(r):
    out = [f"=== AUDITORIA DE ALEATORIEDADE: {r['jogo'].upper()} ({r['concursos']} concursos) ===",
           "Cada teste pergunta: os sorteios reais se comportam como sorteios perfeitamente aleatórios?", "",
           f"{'Teste':<44}{'valor-p':>10}{'ajustado':>10}  Resultado"]
    desvios = 0
    for nome, _, p, _, pa in r["testes"]:
        ok = pa >= 0.05
        desvios += not ok
        out.append(f"{nome:<44}{p:>10.3f}{pa:>10.3f}  {'normal' if ok else 'DESVIO'}")
    out += ["", ("CONCLUSÃO: nenhum desvio. Os sorteios se comportam como aleatórios; nenhum padrão de "
                 "frequência, atraso, paridade ou repetição pode ser explorado." if not desvios else
                 f"CONCLUSÃO: {desvios} teste(s) com desvio estatístico. Pode vir de mudanças de regra ou de "
                 "volante ao longo dos anos; desvio estatístico não é o mesmo que previsão útil (veja o teste "
                 "no passado)."),
            "valor-p ajustado (Holm) corrige o fato de fazermos vários testes: abaixo de 0,05 indica desvio."]
    return "\n".join(out)


# ---------------- previsão do acumulado ----------------
def previsao_acumulado(jogo):
    """Chance de o prêmio principal acumular no próximo concurso e o retorno se acumular."""
    from core import IR, IR_ISENTO
    preco = JOGOS[jogo][4]
    rows = db().execute("SELECT meta FROM draws WHERE jogo=? AND meta IS NOT NULL ORDER BY concurso DESC LIMIT 300",
                        (jogo,)).fetchall()
    metas = [json.loads(r[0]) for r in rows][::-1]
    if len(metas) < 20:
        raise ValueError(f"{jogo}: baixe os resultados primeiro.")
    base = valor_esperado(jogo)
    tops = [p for d, p, _, _ in base["linhas"] if "estimado" in d]
    if not tops or not base["estimado"]:
        return None
    p_top = tops[0]
    resto = sum(c for d, _, _, c in base["linhas"] if "estimado" not in d)
    # quanto o prêmio estimado cresce quando ninguém ganha a faixa principal
    saltos = [b["estimado"] - a["estimado"] for a, b in zip(metas, metas[1:])
              if a.get("estimado") and b.get("estimado") and (b.get("ganhadores") or [1])[0] == 0
              and b["estimado"] > a["estimado"]]
    if not saltos:
        return None
    J = base["estimado"]
    J2 = J + sorted(saltos)[len(saltos) // 2]
    apostas = base["apostas"] or 1
    p_acumula = math.exp(-apostas * p_top)
    lam = apostas * (J2 / J) ** 0.5 * p_top   # vendas sobem com o prêmio (aproximação)
    val = J2 * (1 - math.exp(-lam)) / lam
    if val > IR_ISENTO:
        val *= 1 - IR
    ev2 = (resto + p_top * val) * (2 if jogo == "duplasena" else 1)
    return {"jogo": jogo, "estimado": J, "p_acumula": p_acumula, "proximo": J2, "retorno_atual": base["retorno"],
            "retorno_se_acumular": ev2 / preco}


def previsoes():
    out = []
    for j in JOGOS:
        try:
            p = previsao_acumulado(j)
        except Exception:
            log.warning("previsão %s", j, exc_info=True)
            p = None
        if p:
            out.append(p)
    return sorted(out, key=lambda p: -p["retorno_se_acumular"])


def texto_previsao(prev):
    out = ["=== PREVISÃO DO ACUMULADO (próximo concurso) ===", "",
           f"{'Jogo':<16}{'Prêmio agora':>18}{'Chance de acumular':>20}{'Se acumular':>18}{'Retorno agora':>15}"
           f"{'Retorno depois':>16}"]
    for p in prev:
        out.append(f"{p['jogo']:<16}{brl(p['estimado']):>18}{p['p_acumula']:>20.0%}{brl(p['proximo']):>18}"
                   f"{p['retorno_atual']:>15.0%}{p['retorno_se_acumular']:>16.0%}")
    out += ["", "Se acumular, o prêmio cresce o salto típico dos últimos 300 concursos e as vendas sobem junto, "
                "o que divide mais o prêmio. É uma estimativa: o valor oficial sai depois do sorteio."]
    return "\n".join(out)
