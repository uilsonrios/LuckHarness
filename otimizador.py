"""Otimizador de apostas e modelo de popularidade.

Nada aqui muda a chance de um cartão: o sorteio é uniforme. O que dá para otimizar com matemática é
o jogo e a estrutura da aposta (chance de prêmio por real), a cobertura entre cartões (menos números
repetidos = mais chance de pelo menos um premiar) e a popularidade (números pouco jogados dividem
menos o prêmio). O modelo de popularidade aprende com o número real de ganhadores de cada concurso.
"""
import json, math, os, random, re, threading
from datetime import date

from collections import Counter

from core import (BOLAO, M, MODALIDADES, DATA, EXTRA, IR, IR_ISENTO, JOGOS, MAX_DEZ, MESES, _sorteios, brl, cotas_bolao, custo, db,
                  historico_retorno, hypergeom, log, premio, premios_medios, prob_faixa, valor_esperado)

OBJETIVOS = {"frequente": "ganhar algum prêmio com mais frequência",
             "grande": "maior chance do prêmio principal",
             "retorno": "melhor retorno do dinheiro"}
# jogos em que a faixa mais baixa é "N acertos" só com dezenas (dá para treinar a popularidade)
POP_JOGOS = tuple(JOGOS)


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


def prob_cartoes(jogo, p_dezenas_1, k, extra=True):
    """P(algum de k cartões espalhados premiar): dezenas quase independentes, time/mês sem repetir.
    extra=False: só prêmios das dezenas (o time/mês sozinho devolve pouco mais que o preço do cartão)."""
    p_dz = 1 - (1 - p_dezenas_1) ** k
    if M[jogo].get("sorteios") == 2:
        p_dz = 1 - (1 - p_dz) ** 2
    return 1 - (1 - p_dz) * (_sem_extra(jogo, k) if extra else 1)


def prob_premio(jogo, n=None, trevos=2, extra=True):
    """P(ganhar alguma faixa) com uma aposta de n dezenas, contando trevos, time/mês e os dois sorteios."""
    N, K, mn, fx, _ = JOGOS[jogo]
    n = n or mn
    if M[jogo].get("colunas"):
        p = sum(prob_faixa(jogo, h) for h in fx)
    elif M[jogo].get("trevos"):
        pt = _p_trevos(trevos)
        # 4+ acertos premiam com qualquer trevo; 2 e 3 acertos precisam de pelo menos 1 trevo
        p = sum(hypergeom(N, K, n, h) * (1 if h >= 4 else pt[1] + pt[2]) for h in fx)
    else:
        p = sum(hypergeom(N, K, n, h) for h in fx)
    if M[jogo].get("sorteios") == 2:
        p = 1 - (1 - p) ** 2
    if extra and jogo in EXTRA:
        p = 1 - (1 - p) * (1 - EXTRA[jogo][2])
    return p


def prob_principal(jogo, n=None, trevos=2):
    N, K, mn, fx, _ = JOGOS[jogo]
    n = n or mn
    if M[jogo].get("colunas"):
        return 0.1 ** 7
    p = hypergeom(N, K, n, K)
    if M[jogo].get("trevos"):
        p *= _p_trevos(trevos)[2]
    if M[jogo].get("sorteios") == 2:
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
def formatos_supersete():
    """Formatos oficiais da Super Sete: (total de números, quantos em cada coluna). 7 = um por coluna; 8 a 14 = 1 ou
    2 por coluna; 15 a 21 = 2 ou 3 por coluna (loterias.caixa.gov.br, 31/10/2026)."""
    return {t: ([2] * (t - 7) + [1] * (14 - t) if t <= 14 else [3] * (t - 14) + [2] * (21 - t)) for t in range(7, 22)}


def padroes_supersete(tam):
    """Para cada conjunto de colunas acertadas: (probabilidade, apostas simples com h colunas certas, h = 0..7).
    Cada coluna acerta com chance (números marcados nela)/10, independente das outras: conta exata, 128 casos."""
    from itertools import product
    out = []
    for padrao in product((True, False), repeat=7):
        p, dist = 1.0, [1]
        for s_c, acertou in zip(tam, padrao):
            p *= s_c / 10 if acertou else 1 - s_c / 10
            nova = [0] * (len(dist) + 1)
            for h, q in enumerate(dist):
                nova[h] += q * (s_c - acertou)
                nova[h + 1] += q * acertou
            dist = nova
        out.append((p, dist))
    return out


def cartoes_supersete(k, tam, rng=random):
    """k apostas da Super Sete com tam[c] números na coluna c, usando os dígitos de cada coluna por igual."""
    uso = [[0] * 10 for _ in range(7)]
    cards = []
    for _ in range(k):
        card = []
        for c in range(7):
            dig = sorted(range(10), key=lambda x: (uso[c][x], rng.random()))[:tam[c]]
            for x in dig:
                uso[c][x] += 1
            card.append(dig[0] if len(dig) == 1 else sorted(dig))
        cards.append(card)
    return cards


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
        for n in range(mn, (M[j]["escolha"][1] if M[j].get("colunas") else MAX_DEZ[j]) + 1):
            tv = [1, 2] if M[j].get("trevos") else []
            tam = formatos_supersete()[n] if M[j].get("colunas") else None
            c = JOGOS[j][4] * math.prod(tam) if tam else custo(j, list(range(1, n + 1)), tv)
            k = int(orcamento // c)
            if k < 1:
                break
            if tam and n > mn:   # Super Sete com mais números: conta exata por colunas
                pads = padroes_supersete(tam)
                p1 = sum(pp for pp, d in pads if any(d[h] for h in JOGOS[j][3]))
                p_dz = 1 - (1 - p1) ** k
                out.append({"jogo": j, "tipo": "simples", "n": n, "k": k, "custo": c, "gasto": k * c, "p_semana": p_dz,
                            "p_dezenas": p_dz, "tamanhos": tam,
                            "p_principal_ano": 1 - (1 - math.prod(t / 10 for t in tam)) ** (k * 52), "retorno": ret,
                            "plano": f"{k} aposta(s) de {n} números"})
                continue
            if 2 <= k <= COB_MAX_CARTOES and n == mn:
                p_dz = cobertura_dezenas(j, k)
            else:
                pd = prob_premio(j, n, extra=False)
                if M[j].get("sorteios") == 2:
                    pd = 1 - (1 - pd) ** 0.5   # prob_cartoes aplica os dois sorteios
                p_dz = prob_cartoes(j, pd, k, extra=False)
            ps = 1 - (1 - p_dz) * _sem_extra(j, k)
            out.append({"jogo": j, "tipo": "simples", "n": n, "k": k, "custo": c, "gasto": k * c, "p_semana": ps,
                        "p_dezenas": p_dz,
                        "p_principal_ano": 1 - (1 - prob_principal(j, n)) ** (k * 52), "retorno": ret,
                        "plano": f"{k} cartão(ões) de {n}"})
        out += _opcoes_bolao(j, orcamento, ret) + _opcoes_fechamento(j, orcamento, ret)
    return out


def _opcoes_bolao(j, orcamento, ret):
    """Uma cota por semana de um bolão com mais dezenas. Divide custo e prêmio: a chance de o bolão premiar é
    a da aposta grande inteira, mas você recebe 1/cotas de cada prêmio."""
    out = []
    for n in sorted(BOLAO.get(j, {})):
        c = custo(j, list(range(1, n + 1)), [1, 2] if M[j].get("trevos") else [])
        q = cotas_bolao(j, n, c, orcamento)   # regras da Caixa: cota mínima e máximo de cotas
        if not q:
            continue
        out.append({"jogo": j, "tipo": "bolão", "n": n, "k": 1, "cotas": q, "custo": c, "gasto": c / q,
                    "p_semana": prob_premio(j, n), "p_dezenas": prob_premio(j, n, extra=False), "p_principal_ano": 1 - (1 - prob_principal(j, n) / q) ** 52,
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
        if M[j].get("sorteios") == 2:
            p = 1 - (1 - p) ** 2
        p_dz = p
        p = 1 - (1 - p) * _sem_extra(j, len(cards))
        out.append({"jogo": j, "tipo": "fechamento", "n": mn, "k": len(cards), "v": v, "t": t, "custo": preco,
                    "gasto": gasto, "p_semana": p, "p_dezenas": p_dz, "p_garantia": p_gar, "p_principal_ano": 1 - (1 - prob_principal(j) * len(cards)) ** 52,
                    "retorno": ret, "cards_base": cards,
                    "plano": f"fechamento {v} dez., {len(cards)} cart."})
    return out


def otimizar(orcamento, objetivo="frequente", jogos=None, top=8):
    if orcamento <= 0:
        raise ValueError("Informe quanto quer gastar por semana.")
    ops = opcoes(orcamento, jogos)
    if not ops:
        raise ValueError(f"Com {brl(orcamento)} por semana não dá para fazer nenhuma aposta.")
    # "frequente" conta só prêmios das dezenas: 12 cartões de Dia de Sorte "premiam sempre" pelo Mês da Sorte,
    # mas gastam R$ 30 para receber R$ 2,50
    chave = {"frequente": lambda o: (o.get("p_dezenas", o["p_semana"]), -o["gasto"]),
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
        pd = o.get("p_dezenas", o["p_semana"])
        semanas = 1 / pd
        cada = f"{semanas:.1f} sem" if semanas < 100 else f"{semanas / 52:,.0f} anos".replace(",", ".")
        ret = f"{o['retorno']:.0%}" if o["retorno"] is not None else "-"
        out.append(f"{i:<3}{o['jogo']:<15}{plano:<28}{brl(o['gasto']):>11}{pd:>18.1%}{cada:>11}"
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
        out.append(f"'Prêmio na semana' conta só as dezenas. Com o {desc} (cada cartão com um diferente) a chance "
                   f"sobe para {b['p_semana']:.0%}, mas ele paga pouco" + (f": cerca de {brl(val)}." if val else "."))
    out += [
            f"Em um ano: gasta {brl(anual)}, a matemática devolve em média {brl(volta)} em prêmios "
            f"(saldo esperado {brl(volta - anual)}).",
            "Os cartões abaixo são espalhados (o mínimo de números repetidos entre eles) e, se houver modelo de "
            "popularidade treinado, evitam os números que o público mais joga.",
            "Bolão: 'Principal/ano' é o equivalente à sua cota (o bolão ganha mais vezes, mas você recebe só a "
            "sua parte).",
            "Nenhum plano aumenta a chance de cada cartão nem garante lucro: ele tira o máximo de cada real."]
    return "\n".join(out)


# ---------------- aposta mínima, prêmio maior ----------------
ALVOS = {"R$ 1 mil": 1e3, "R$ 10 mil": 1e4, "R$ 100 mil": 1e5, "R$ 1 milhão": 1e6}


def _liquido(v):
    return v * (1 - IR) if v > IR_ISENTO else v


def _pagamento(jogo, n, h, th, med):
    """Quanto um cartão de n dezenas com h acertos (e th trevos) recebe, com os valores típicos pagos."""
    N, K, mn, fx, _ = JOGOS[jogo]
    pr = list(med.items())
    if M[jogo].get("colunas"):
        p = premio(jogo, h, 0, pr) if h in fx else None
        return _liquido(p[1]) if p and p[1] else 0.0
    from core import contidas
    tot = 0.0
    for j, t2, qtd in contidas(jogo, n, h, 2 if M[jogo].get("trevos") else 0, th):
        p = premio(jogo, j, t2, pr)
        if p and p[1]:
            tot += qtd * _liquido(p[1])
    return tot


def _chance_alvo(jogo, n, alvo, med, cotas=1):
    """P(um cartão de n dezenas pagar pelo menos 'alvo' para quem tem 1 de 'cotas' partes) e o menor número de
    acertos que chega lá."""
    N, K, mn, fx, _ = JOGOS[jogo]
    ths = _p_trevos(2) if M[jogo].get("trevos") else {0: 1.0}
    p, acertos = 0.0, set()
    for h in range(0, (7 if M[jogo].get("colunas") else min(n, K)) + 1):
        ph = prob_faixa(jogo, h) if M[jogo].get("colunas") else hypergeom(N, K, n, h)
        for th, pt in ths.items():
            if _pagamento(jogo, n, h, th, med) / cotas >= alvo:
                p += ph * pt
                acertos.add(h)
    if M[jogo].get("sorteios") == 2:
        p = 1 - (1 - p) ** 2
    altos = sorted(h for h in acertos if h)
    txt = ((f"{altos[0]}+" if altos[0] < (7 if M[jogo].get("colunas") else K) else str(altos[0])) if altos else "")
    if 0 in acertos:   # Lotomania paga quem erra todos
        txt = f"{txt} ou 0" if txt else "0"
    return p, txt


def aposta_minima(orcamento, alvo):
    """Para cada jogo, a forma de gastar até 'orcamento' com mais chance de receber pelo menos 'alvo' de uma vez:
    cartões simples (os mais baratos ou com mais dezenas) ou uma cota de bolão. Usa os prêmios típicos pagos
    nos últimos 300 concursos, já sem o IR."""
    if orcamento <= 0:
        raise ValueError("Informe quanto quer gastar.")
    out = []
    for j in JOGOS:
        N, K, mn, fx, preco = JOGOS[j]
        med = premios_medios(j, 300)[0]
        if not med:
            continue
        tv = [1, 2] if M[j].get("trevos") else []
        cands = []
        for n in range(mn, (M[j]["escolha"][1] if M[j].get("colunas") else MAX_DEZ[j]) + 1):
            if M[j].get("colunas") and n > mn:
                tam = formatos_supersete()[n]
                c = preco * math.prod(tam)
                if c > orcamento:
                    break
                k = int(orcamento // c)
                ok = [(pp, d) for pp, d in padroes_supersete(tam)
                      if sum(q * _pagamento(j, 7, h, 0, med) for h, q in enumerate(d)) >= alvo]
                p1 = sum(pp for pp, _ in ok)
                hs = [min(h for h, q in enumerate(d) if q and _pagamento(j, 7, h, 0, med)) for _, d in ok
                      if any(q and _pagamento(j, 7, h, 0, med) for h, q in enumerate(d))]
                cands.append({"jogo": j, "tipo": "simples", "n": n, "k": k, "custo": c, "gasto": k * c,
                              "p": 1 - (1 - p1) ** k, "h_min": f"{min(hs)}+" if hs else "", "tamanhos": tam,
                              "plano": f"{k} aposta(s) de {n} números"})
                continue
            c = custo(j, list(range(1, n + 1)), tv)
            if c <= orcamento:
                k = int(orcamento // c)
                p1, h = _chance_alvo(j, n, alvo, med)
                cands.append({"jogo": j, "tipo": "simples", "n": n, "k": k, "custo": c, "gasto": k * c,
                              "p": 1 - (1 - p1) ** k, "h_min": h, "plano": f"{k} cartão(ões) de {n}"})
            else:
                q = cotas_bolao(j, n, c, orcamento)
                if not q:
                    continue
                p1, h = _chance_alvo(j, n, alvo, med, q)
                cands.append({"jogo": j, "tipo": "bolão", "n": n, "k": 1, "cotas": q, "custo": c, "gasto": c / q,
                              "p": p1, "h_min": h, "plano": f"bolão {n} dez., 1/{q} cota"})
        cands = [x for x in cands if x["p"] > 0]
        if cands:
            b = max(cands, key=lambda x: (x["p"], -x["gasto"]))
            try:
                b["retorno"] = valor_esperado(j)["retorno"]
            except Exception:
                b["retorno"] = None
            out.append(b)
    if not out:
        raise ValueError(f"Com {brl(orcamento)} nenhum jogo costuma pagar {brl(alvo)} de uma vez.")
    return sorted(out, key=lambda x: -x["p"])


def texto_aposta_minima(orcamento, alvo, ranking):
    def anos(p):
        a = 1 / p / 52
        return f"{a:,.0f} anos".replace(",", ".") if a >= 1.5 else f"{a * 52:.0f} semanas"
    out = [f"=== APOSTA MÍNIMA, PRÊMIO MAIOR: até {brl(orcamento)} por concurso, prêmio de pelo menos "
           f"{brl(alvo)} de uma vez ===", "",
           f"{'#':<3}{'Jogo':<15}{'Aposta':<27}{'Gasto':>10}{'Chance':>17}   {'Jogando 1 vez por semana':<28}"
           "Acertos"]
    for i, o in enumerate(ranking, 1):
        out.append(f"{i:<3}{o['jogo']:<15}{o['plano']:<27}{brl(o['gasto']):>10}{_um_em(o['p']):>17}   "
                   f"{'1 vez a cada ' + anos(o['p']):<28}{o['h_min']}")
    b = ranking[0]
    out += ["", f"Melhor chance: {b['jogo']}, {b['plano']} ({brl(b['gasto'])})."]
    if b["tipo"] == "bolão":
        out.append(f"No bolão a aposta grande inteira concorre e você recebe 1/{b['cotas']} do prêmio: a chance acima "
                   f"já conta só os prêmios em que a sua parte chega a {brl(alvo)}. Cotas e valores seguem as regras "
                   "de bolão da Caixa; cota pronta comprada na lotérica pode custar até 35% a mais (tarifa).")
    out += ["",
            "Os valores são os prêmios típicos pagos nos últimos 300 concursos (mediana), já sem o IR. Prêmio "
            "principal e faixas altas variam muito: acumulados pagam mais e prêmios divididos pagam menos.",
            "Gastar o mínimo não muda a chance de cada cartão: o que muda é qual jogo e qual formato, e é isso que a "
            "tabela compara. Números pouco jogados (modelo de popularidade) dividem menos as faixas altas."]
    return "\n".join(out)


# ---------------- cartões espalhados ----------------
def extras_espalhados(jogo, k, rng=random):
    """Trevos/time/mês sem repetir entre os cartões: com 12 cartões no Dia de Sorte, todos os meses ficam
    cobertos e o Mês da Sorte sai garantido em todo concurso."""
    if M[jogo].get("trevos"):
        pares = [[a, b] for a in range(1, 7) for b in range(a + 1, 7)]
        rng.shuffle(pares)
        return [pares[i % 15] for i in range(k)]
    if M[jogo].get("extra", ("",))[0] == "mes":
        meses = MESES[:]
        rng.shuffle(meses)
        return [[meses[i % 12]] for i in range(k)]
    if M[jogo].get("extra", ("",))[0] == "time":
        from core import times_validos
        times = times_validos()[0]
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
    if M[jogo].get("colunas"):
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
    if M[jogo].get("colunas"):
        from core import dist_colunas
        return any(q for h, q in enumerate(dist_colunas(card, sorteio)) if h in fx)
    h = len(set(card) & sorteio)
    if M[jogo].get("trevos"):
        th = len(set(trevos_card) & trevos_sorteio)
        return h >= 4 or (h in (2, 3) and th >= 1)
    return h in fx


def simular_cobertura(jogo, cards, n_sim=20000, rng=random, trevos=None):
    """P(pelo menos um cartão premiar nas dezenas) por Monte Carlo."""
    N, K = JOGOS[jogo][:2]
    trevos = trevos or [[1, 2]] * len(cards)
    ok = 0
    for _ in range(n_sim):
        if M[jogo].get("colunas"):
            s = [rng.randrange(10) for _ in range(7)]
        else:
            s = set(rng.sample(range(1, N + 1), K))
        ts = set(rng.sample(range(1, 7), 2)) if M[jogo].get("trevos") else set()
        ok_s = any(_premiou(jogo, c, s, t, ts) for c, t in zip(cards, trevos))
        if M[jogo].get("sorteios") == 2 and not ok_s:   # 2º sorteio do mesmo concurso
            s2 = set(rng.sample(range(1, N + 1), K))
            ok_s = any(_premiou(jogo, c, s2) for c in cards)
        ok += ok_s
    return ok / n_sim


COB_MAX_CARTOES = 60   # acima disso a simulação fica lenta; usa a aproximação de cartões independentes
_COB = {}


def cobertura_dezenas(jogo, k, n=None, n_sim=4000):
    """P(algum de k cartões espalhados premiar nas dezenas) simulando sorteios com os cartões que o app gera:
    considera a sobreposição entre eles (a conta de cartões independentes subestima até ~7 pontos)."""
    chave = (jogo, k, n)
    if chave not in _COB:
        rng = random.Random(7)
        cards = cartoes_espalhados(jogo, k, n, None, rng)
        tv = extras_espalhados(jogo, k, rng) if M[jogo].get("trevos") else None
        _COB[chave] = simular_cobertura(jogo, cards, n_sim, random.Random(8), tv)
    return _COB[chave]


def comparar_cobertura(jogo, k, n=None, n_sim=20000, seed=7):
    """Cartões espalhados x cartões ao acaso, com os mesmos sorteios simulados."""
    N, _, mn = JOGOS[jogo][:3]
    n = n or mn
    rng = random.Random(seed)
    esp = sugestoes(jogo, k, n, rng)   # mesma fonte do "Sugerir apostas"
    tv_esp = extras_espalhados(jogo, k, rng) if M[jogo].get("trevos") else None
    p_esp = simular_cobertura(jogo, esp, n_sim, random.Random(seed + 1), tv_esp)
    # cartões ao acaso são independentes dado o sorteio: conta exata, sem depender de um sorteio de cartões
    p1 = prob_premio(jogo, n, extra=False)
    if M[jogo].get("sorteios") == 2:
        p1 = 1 - (1 - p1) ** 0.5   # simular_cobertura confere um sorteio só
    return esp, tv_esp, p_esp, 1 - (1 - p1) ** k


# ---------------- modelo de popularidade ----------------
# Aprende o que o público joga a partir do número de ganhadores: se saem combinações populares, ganha mais
# gente do que o acaso prevê. Vários modelos e alvos são comparados; a escolha é feita numa validação e a nota
# final vem de concursos que nem o treino nem a escolha viram.

# colunas do volante de cada jogo (desenhos no bilhete: linhas, colunas)
VOLANTE = {j: m["volante"] for j, m in MODALIDADES.items() if m.get("volante")}
PADROES = ["até 31 (datas)", "todos até 31", "maior sequência", "vizinhos", "mesma linha do volante",
           "mesma coluna do volante", "mesma dezena", "múltiplos de 5", "progressão aritmética",
           "repetidos do concurso anterior", "soma fora do centro", "desequilíbrio par/ímpar", "espalhamento",
           "até 12 (meses)", "mesmo final", "borda do volante", "vizinhos do anterior"]
LAMBDAS = (3.0, 10.0, 30.0, 100.0, 300.0, 1000.0)


def _pop_path(jogo):
    return os.path.join(DATA, f"popularidade_{jogo}.json")


def _modelo(jogo):
    try:
        with open(_pop_path(jogo), encoding="utf8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def pesos_popularidade(jogo):
    """Peso de cada número (para escolher números pouco jogados nos cartões). None se não houver modelo confiável."""
    m = _modelo(jogo)
    if not m or not m.get("confiavel") or M[jogo].get("colunas"):
        return None
    return {int(k): v for k, v in m["pesos"].items()}


def pesos_ate(jogo, ate):
    """Pesos de popularidade treinados só com concursos anteriores a 'ate' (testes no passado sem olhar o futuro).
    {} se houver poucos concursos antes de 'ate'."""
    am = [a for a in _amostras(jogo) if a["concurso"] < ate]
    if len(am) < 200 or M[jogo].get("colunas"):
        return {}
    cfg = {"nome": "passado", "modelo": "ridge", "alvo": "multi", "padroes": False, "meia_vida": 500}
    c = int(len(am) * 0.8)
    escolhida = _ajusta(jogo, am[:c], cfg, am[c:])["cfg"]   # regularização escolhida na validação
    m = _ajusta(jogo, am, escolhida)
    return {i + 1: m["coef"][i] for i in range(JOGOS[jogo][0])}


def _padroes(jogo, dz, anterior):
    """Como as pessoas escolhem números: datas, sequências, desenhos no volante, repetir o último resultado."""
    N = JOGOS[jogo][0]
    s = sorted(dz)
    n = len(s)
    cols = VOLANTE.get(jogo, 10)
    difs = [b - a for a, b in zip(s, s[1:])]
    run = best = 1
    for d in difs:
        run = run + 1 if d == 1 else 1
        best = max(best, run)
    linhas = Counter((x - 1) // cols for x in s)
    colunas = Counter((x - 1) % cols for x in s)
    dezenas = Counter((x - 1) // 10 for x in s)
    datas = sum(x <= 31 for x in s) / n if N > 31 else 0.0
    media = n * (N + 1) / 2
    dp = math.sqrt(n * (N - n) * (N + 1) / 12) or 1
    pares = sum(x % 2 == 0 for x in s)
    esp_dif = (N + 1) / (n + 1)
    ant = set(anterior)
    return [datas, float(datas == 1.0), best / n, sum(d == 1 for d in difs) / n, max(linhas.values()) / n,
            max(colunas.values()) / n, max(dezenas.values()) / n, sum(x % 5 == 0 for x in s) / n,
            float(len(set(difs)) == 1), len(set(s) & set(anterior)) / n, abs(sum(s) - media) / dp,
            abs(pares - n / 2) / n, (sum((d - esp_dif) ** 2 for d in difs) / max(1, len(difs))) ** 0.5 / esp_dif,
            sum(x <= 12 for x in s) / n if N > 12 else 0.0, max(Counter(x % 10 for x in s).values()) / n,
            sum((x - 1) // cols in (0, (N - 1) // cols) or (x - 1) % cols in (0, cols - 1) for x in s) / n,
            sum((x - 1 in ant or x + 1 in ant) and x not in ant for x in s) / n]


def _base(jogo):
    """Quantas entradas 'um-quente' o jogo tem: números (ou coluna x dígito na Super Sete) e trevos."""
    if M[jogo].get("colunas"):
        return 70
    return JOGOS[jogo][0] + (6 if M[jogo].get("trevos") else 0)


def _ativos(jogo, a):
    """Índices das entradas ligadas para um sorteio/cartão."""
    if M[jogo].get("colunas"):
        return [c * 10 + d for c, d in enumerate(a["dz"])]
    N = JOGOS[jogo][0]
    tv = [int(t) for t in a.get("trevos", [])] if M[jogo].get("trevos") else []
    return [x - 1 for x in a["dz"]] + [N + t - 1 for t in tv]


def _faixas_alvo(jogo, meta, sorteio=1):
    """(chance de uma aposta simples cair na faixa, ganhadores) para cada faixa de acertos do concurso. Dupla Sena:
    a 1ª metade das faixas é do 1º sorteio e a 2ª, do 2º."""
    N, K, mn = JOGOS[jogo][:3]
    pr, gan = meta.get("premios", []), meta.get("ganhadores", [])
    if M[jogo].get("sorteios") == 2:
        h = len(pr) // 2
        pr, gan = (pr[:h], gan[:h]) if sorteio == 1 else (pr[h:], gan[h:])
    pt = _p_trevos(2)
    out = []
    for (desc, _), g in zip(pr, gan):
        d = desc.lower()
        mt = re.match(r"(\d+) acertos?", d)
        if not mt:
            continue
        h = int(mt.group(1))
        p = prob_faixa(jogo, h) if M[jogo].get("colunas") else hypergeom(N, K, mn, h)
        if M[jogo].get("trevos"):
            p *= (pt[2] if "2 trevos" in d else pt[0] + pt[1] if "1 ou nenhum" in d else pt[1] if "1 trevo" in d
                  else 1)
        out.append((p, g))
    return out


def _amostras(jogo):
    """Um exemplo por concurso. 'y_baixa': log(ganhadores/esperados) na faixa com mais ganhadores.
    'y_multi': o mesmo combinando todas as faixas, cada uma com peso pela precisão (mais ganhadores = menos
    ruído). 'prec': quanto o exemplo é confiável."""
    preco = JOGOS[jogo][4]
    rows = db().execute("SELECT concurso, dezenas, meta FROM draws WHERE jogo=? AND meta IS NOT NULL "
                        "ORDER BY concurso", (jogo,)).fetchall()
    out, anterior = [], []
    sorteios = []
    for conc, dz, meta in rows:   # Dupla Sena: cada sorteio é um exemplo, com os ganhadores da sua metade
        m = json.loads(meta)
        sorteios.append((conc, json.loads(dz), m, 1))
        if M[jogo].get("sorteios") == 2 and m.get("dezenas2"):
            sorteios.append((conc, m["dezenas2"], m, 2))
    for conc, dz, m, qual in sorteios:
        ant, anterior = anterior, dz
        vendas = m.get("arrecadado") or 0
        if not vendas or not ant or not m.get("ganhadores"):
            continue
        apostas = vendas / preco
        fx = [(apostas * p, g) for p, g in _faixas_alvo(jogo, m, qual) if apostas * p >= 2]
        if not fx:
            continue
        e0, g0 = max(fx)
        if e0 < 5:
            continue
        ys = [(min(e, 500.0), math.log((g + 0.5) / (e + 0.5))) for e, g in fx]
        peso = sum(w for w, _ in ys)
        out.append({"concurso": conc, "data": m.get("data"), "dz": dz, "ant": ant, "trevos": [int(t) for t in m.get("trevos", [])],
                    "y_baixa": math.log((g0 + 0.5) / e0), "y_multi": sum(w * y for w, y in ys) / peso,
                    "prec": min(1.0, peso / 100)})
    return out


def _r2(prev, ys):
    """Correlação² com sinal entre previsão e alvo: mede só o efeito dos números, não o nível de vendas."""
    mx, my = sum(prev) / len(prev), sum(ys) / len(ys)
    sxy = sum((a - mx) * (c - my) for a, c in zip(prev, ys))
    sxx, syy = sum((a - mx) ** 2 for a in prev), sum((c - my) ** 2 for c in ys)
    return (sxy * sxy / (sxx * syy) if sxx and syy else 0) * (1 if sxy > 0 else -1)


def _resolver(A, b):
    """Sistema linear A x = b por eliminação de Gauss com pivô parcial."""
    n = len(b)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(M[r][c]))
        M[c], M[p] = M[p], M[c]
        piv = M[c][c]
        for r in range(c + 1, n):
            f = M[r][c] / piv
            if f:
                for k in range(c, n + 1):
                    M[r][k] -= f * M[c][k]
    x = [0.0] * n
    for c in range(n - 1, -1, -1):
        x[c] = (M[c][n] - sum(M[c][k] * x[k] for k in range(c + 1, n))) / M[c][c]
    return x


def _entradas(jogo, dados, cfg, norm=None):
    """Entradas esparsas [(índice, valor)] por exemplo; padrões padronizados com média/desvio do treino."""
    B = _base(jogo)
    com_pad = cfg["padroes"] and not M[jogo].get("colunas")
    # modelos salvos antes dos padrões novos usam só os primeiros (os novos foram acrescentados no fim)
    F = (len(norm[0]) if norm is not None else len(PADROES)) if com_pad else 0
    feats = [_padroes(jogo, a["dz"], a["ant"])[:F] for a in dados] if com_pad else []
    if norm is None:
        med = [sum(f[i] for f in feats) / len(feats) for i in range(F)]
        dp = [max(1e-9, (sum((f[i] - med[i]) ** 2 for f in feats) / len(feats)) ** 0.5) for i in range(F)]
        norm = (med, dp)
    med, dp = norm
    xs = []
    for t, a in enumerate(dados):
        x = [(j, 1.0) for j in _ativos(jogo, a)]
        x += [(B + i, (feats[t][i] - med[i]) / dp[i]) for i in range(F)]
        xs.append(x)
    return xs, norm, B + F


def _pesos_amostra(dados, cfg):
    T = len(dados)
    return [a["prec"] * (math.exp(-(T - 1 - t) / cfg["meia_vida"]) if cfg["meia_vida"] else 1.0)
            for t, a in enumerate(dados)]


def _sistema(xs, ys, ws, D):
    """X'WX e X'Wy centrados, montados de forma esparsa (rápido) e reaproveitados para cada regularização."""
    W = sum(ws)
    mx, my = [0.0] * D, sum(w * y for w, y in zip(ws, ys)) / W
    for x, w in zip(xs, ws):
        for j, v in x:
            mx[j] += w * v / W
    A = [[0.0] * D for _ in range(D)]
    rhs = [0.0] * D
    for x, y, w in zip(xs, ys, ws):
        for j, v in x:
            rhs[j] += w * v * (y - my)
            row = A[j]
            for k, u in x:
                row[k] += w * v * u
    for j in range(D):
        for k in range(D):
            A[j][k] -= W * mx[j] * mx[k]
    return A, rhs


def _ridge(A, rhs, lam):
    A2 = [row[:] for row in A]
    for j in range(len(A2)):
        A2[j][j] += lam
    return _resolver(A2, rhs)


def _mlp(xs, ys, ws, D, oculto=8, epocas=25, lr=0.02, l2=1e-4, seed=1):
    """Rede neural de 2 camadas (tanh) treinada por gradiente: capta combinações que a ridge não vê."""
    rng = random.Random(seed)
    my = sum(w * y for w, y in zip(ws, ys)) / sum(ws)
    W1 = [[rng.gauss(0, 0.1) for _ in range(oculto)] for _ in range(D)]
    b1 = [0.0] * oculto
    v = [rng.gauss(0, 0.1) for _ in range(oculto)]
    ordem = list(range(len(xs)))
    for ep in range(epocas):
        rng.shuffle(ordem)
        passo = lr / (1 + ep * 0.2)
        for t in ordem:
            x, w = xs[t], ws[t]
            a = b1[:]
            for j, val in x:
                Wj = W1[j]
                for h in range(oculto):
                    a[h] += Wj[h] * val
            hs = [math.tanh(z) for z in a]
            erro = w * (sum(vh * hh for vh, hh in zip(v, hs)) - (ys[t] - my))
            erro = max(-3.0, min(3.0, erro))
            g = [erro * v[h] * (1 - hs[h] * hs[h]) for h in range(oculto)]
            for h in range(oculto):
                v[h] -= passo * (erro * hs[h] + l2 * v[h])
                b1[h] -= passo * g[h]
            for j, val in x:
                Wj = W1[j]
                for h in range(oculto):
                    Wj[h] -= passo * (g[h] * val + l2 * Wj[h])
    return {"W1": W1, "b1": b1, "v": v}


def _saida(mod, x):
    """Previsão (log do fator de divisão) de um modelo salvo para entradas esparsas x."""
    s = 0.0
    if mod.get("coef"):
        s += sum(mod["coef"][j] * v for j, v in x)
    if mod.get("mlp"):
        m = mod["mlp"]
        a = m["b1"][:]
        for j, val in x:
            for h, wjh in enumerate(m["W1"][j]):
                a[h] += wjh * val
        r = sum(vh * math.tanh(z) for vh, z in zip(m["v"], a))
        s = (s + r) / 2 if mod.get("coef") else r
    return s


def _ajusta(jogo, treino, cfg, val=None):
    """Treina um modelo com a configuração cfg. Com val, escolhe a regularização pela nota na validação."""
    if cfg.get("janela"):
        treino = treino[-cfg["janela"]:]
    alvo = "y_" + cfg["alvo"]
    xs, norm, D = _entradas(jogo, treino, cfg)
    ys, ws = [a[alvo] for a in treino], _pesos_amostra(treino, cfg)
    mod = {"cfg": cfg, "norm": norm, "D": D}
    if cfg["modelo"] in ("ridge", "media"):
        A, rhs = _sistema(xs, ys, ws, D)
        if val is not None and "lambda" not in cfg:
            xv, _, _ = _entradas(jogo, val, cfg, norm)
            yv = [a["y_multi"] for a in val]
            notas = {lam: _r2([sum(c[j] * v for j, v in x) for x in xv], yv)
                     for lam in LAMBDAS for c in [_ridge(A, rhs, lam)]}
            cfg = {**cfg, "lambda": max(notas, key=notas.get)}
            mod["cfg"] = cfg
        mod["coef"] = _ridge(A, rhs, cfg.get("lambda", 100.0))
    if cfg["modelo"] in ("mlp", "media"):
        mod["mlp"] = _mlp(xs, ys, ws, D)
    return mod


def _prever_lote(jogo, mod, dados):
    xs, _, _ = _entradas(jogo, dados, mod["cfg"], mod["norm"])
    return [_saida(mod, x) for x in xs]


def _prever(jogo, mod, a):
    return _prever_lote(jogo, mod, [a])[0]


# janela: treinar só com os últimos N concursos (o gosto do público muda; na curva de aprendizado ~500-1000
# concursos recentes renderam mais que o histórico inteiro em vários jogos)
CONFIGS = [{"nome": f"{m}|{al}|{'padrões' if p else 'números'}|"
                    + (f"recentes (meia-vida {mv})" if mv else "todos") + (f"|últimos {jn}" if jn else ""),
            "modelo": m, "alvo": al, "padroes": p, "meia_vida": mv, "janela": jn}
           for m in ("ridge",) for al in ("baixa", "multi") for p in (False, True) for mv in (0, 250, 500)
           for jn in (0, 500, 1000)]


# Em 31/10/2026 (v1.14.2) padrões e janelas foram escolhidos olhando a nota de teste: ela só volta a ser uma
# estimativa independente quando o período de teste começar depois dessa data.
POP_TESTE_CONSULTADO = date(2026, 10, 31)


def _data(s):
    try:
        d, m, a = map(int, s.split("/"))
        return date(a, m, d)
    except (AttributeError, ValueError):
        return date.min


def treinar_popularidade(jogo, teste=0.2, rapido=False):
    """Compara configurações (alvo, padrões, peso dos recentes, ridge x rede de 2 camadas x média dos dois).
    Escolha pela validação (último quarto do treino); nota final no teste (últimos 20% dos concursos), sempre
    contra o alvo combinado de todas as faixas. Só é usado se passar de 5% no teste."""
    dados = _amostras(jogo)
    if len(dados) < 200:
        raise ValueError(f"{jogo}: poucos concursos com número de ganhadores ({len(dados)}). "
                         "Baixe os resultados de novo e tente.")
    corte = int(len(dados) * (1 - teste))
    treino, prova = dados[:corte], dados[corte:]
    # ajuste | validação (escolhe a configuração) | reserva (decide se o modelo é usado) | teste (só relata)
    c1, c2 = int(len(treino) * 0.6), int(len(treino) * 0.8)
    fit, val, reserva = treino[:c1], treino[c1:c2], treino[c2:]
    yv, yp = [a["y_multi"] for a in val], [a["y_multi"] for a in prova]
    notas = {}
    for cfg in CONFIGS:
        m = _ajusta(jogo, fit, cfg, val)
        notas[cfg["nome"]] = (_r2(_prever_lote(jogo, m, val), yv), m["cfg"])
    melhor_ridge = max(notas.values(), key=lambda t: t[0])[1]
    if not rapido:
        for tipo in ("mlp", "media"):
            cfg = {**melhor_ridge, "modelo": tipo, "nome": melhor_ridge["nome"].replace("ridge", tipo, 1)}
            m = _ajusta(jogo, fit, cfg)
            notas[cfg["nome"]] = (_r2(_prever_lote(jogo, m, val), yv), cfg)
    nome, (nota_val, cfg) = max(notas.items(), key=lambda kv: kv[1][0])
    nota_reserva = _r2(_prever_lote(jogo, _ajusta(jogo, fit + val, cfg), reserva), [a["y_multi"] for a in reserva])
    final = _ajusta(jogo, treino, cfg)
    r2_teste = _r2(_prever_lote(jogo, final, prova), yp)
    base_cfg = {"nome": "v1", "modelo": "ridge", "alvo": "baixa", "padroes": False, "meia_vida": 0}
    base = _ajusta(jogo, treino, base_cfg, val)   # o modelo antigo, para comparar no mesmo teste
    r2_base = _r2(_prever_lote(jogo, base, prova), yp)
    B = _base(jogo)
    coef = final.get("coef") or [0.0] * final["D"]
    if M[jogo].get("colunas"):
        pesos = {}
    else:
        N = JOGOS[jogo][0]
        media = sum(coef[:N]) / N
        pesos = {i + 1: coef[i] - media for i in range(N)}
    modelo = {"jogo": jogo, "versao": nome, **final, "pesos": pesos, "r2_teste": r2_teste, "r2_base": r2_base,
              "nota_validacao": nota_val, "nota_reserva": nota_reserva, "confiavel": nota_reserva > 0.05,
              "teste_independente": _data(prova[0].get("data")) > POP_TESTE_CONSULTADO, "concursos": len(dados),
              "inicio_teste": prova[0]["concurso"], "anterior": dados[-1]["dz"], "base": B,
              "ranking": sorted(((n, v[0]) for n, v in notas.items()), key=lambda t: -t[1])}
    with open(_pop_path(jogo), "w", encoding="utf8") as f:
        json.dump(modelo, f)
    return modelo


def texto_popularidade(m):
    jogo = m["jogo"]
    out = [f"=== MODELO DE POPULARIDADE: {jogo.upper()} ({m['concursos']} concursos) ===",
           "Aprende o que o público mais joga comparando, em cada concurso, quantas pessoas ganharam com quantas "
           "deveriam ganhar pelo acaso.", "",
           f"Nota no teste (correlação² entre previsto e real, em concursos recentes que o treino não viu): "
           f"{m['r2_teste']:.1%}   |   modelo antigo no mesmo teste: {m['r2_base']:.1%}"
           + ("" if m.get("teste_independente", True) else
              "\n  (este período de teste já foi consultado ao escolher a configuração em 31/10/2026: a nota não é "
              "uma estimativa independente)"),
           f"Decisão de uso: nota {m.get('nota_reserva', m['r2_teste']):.1%} num trecho reservado (nem a escolha "
           "nem o teste entram nela).",
           f"Configuração escolhida: {m['versao']}",
           "  -> " + ("CONFIÁVEL: explica parte real da divisão de prêmios e é usado nos cartões."
                      if m["confiavel"] else
                      "NÃO CONFIÁVEL: abaixo de 5% no trecho reservado. O app não usa este modelo (seria ruído)."), "",
           "Configurações comparadas (nota na validação):"]
    out += [f"  {n:<40}{v:>7.1%}" for n, v in m["ranking"][:6]]
    w = {int(k): v for k, v in m.get("pesos", {}).items()}
    fx = lambda v: f"{math.exp(v):.2f}x"
    if w:
        mais = sorted(w, key=w.get, reverse=True)[:10]
        menos = sorted(w, key=w.get)[:10]
        out += ["", "Mais jogados (dividem mais o prêmio): " + ", ".join(f"{x:02d} ({fx(w[x])})" for x in mais),
                "Menos jogados (dividem menos):        " + ", ".join(f"{x:02d} ({fx(w[x])})" for x in menos)]
    coef = m.get("coef") or []
    if m["cfg"]["padroes"] and len(coef) > m["base"] and not M[jogo].get("colunas"):
        efeitos = sorted(zip(PADROES, coef[m["base"]:]), key=lambda p: -abs(p[1]))
        out += ["", "Padrões de escolha (efeito sobre o número de ganhadores):"]
        out += [f"  {nome:<34}{math.exp(v) - 1:+.0%}" for nome, v in efeitos if abs(v) >= 0.01]
    out += ["", "Isto não muda a chance de acertar: muda quantas pessoas dividem o prêmio com você se acertar."]
    return "\n".join(out)


def fator_divisao(jogo, card, trevos=()):
    """Quantas vezes mais (ou menos) gente que a média deve ter o mesmo cartão, pelo modelo treinado."""
    if M[jogo].get("colunas") and any(isinstance(c, list) for c in card):
        return None   # o modelo só conhece apostas de um número por coluna
    m = _modelo(jogo)
    if not m or not m.get("confiavel"):
        return None
    return math.exp(_prever(jogo, m, {"dz": list(card), "ant": m.get("anterior", []), "trevos": list(trevos)}))


MIN_ACUM = {"lotofacil": 2, "diadesorte": 6, "supersete": 6, "lotomania": 6}


def estudo_acumulados(jogo, min_acum=None):
    """Prêmios principais que acumularam por vários concursos: quão populares eram as dezenas que finalmente
    saíram, segundo os ganhadores reais das faixas menores e segundo o modelo."""
    min_acum = min_acum or MIN_ACUM.get(jogo, 8)
    m = _modelo(jogo) or treinar_popularidade(jogo)
    amostras = _amostras(jogo)
    if not amostras:
        raise ValueError(f"{jogo}: baixe os resultados primeiro.")
    dados = {a["concurso"]: a for a in amostras}
    nivel = sorted(a["y_multi"] for a in amostras)[len(amostras) // 2]   # nível típico, para comparar
    rows = db().execute("SELECT concurso, meta FROM draws WHERE jogo=? AND meta IS NOT NULL ORDER BY concurso",
                        (jogo,)).fetchall()
    casos, acum = [], 0
    for conc, meta in rows:
        top = (json.loads(meta).get("ganhadores") or [0])[0]
        if top:
            if acum >= min_acum and conc in dados:
                a = dados[conc]
                casos.append({"concurso": conc, "acumulou": acum, "ganhadores": top, "dz": a["dz"],
                              "real": math.exp(a["y_multi"] - nivel), "modelo": math.exp(_prever(jogo, m, a)),
                              "no_teste": conc >= m.get("inicio_teste", 10 ** 9)})
            acum = 0
        else:
            acum += 1
    return {"jogo": jogo, "versao": m.get("versao", "v1"), "casos": casos, "min_acum": min_acum}


def texto_acumulados(r):
    casos = r["casos"]
    out = [f"=== ACUMULADOS QUE SAÍRAM: {r['jogo'].upper()} ({r['min_acum']}+ concursos sem ganhador) ===",
           "Real = ganhadores de todas as faixas em relação ao típico (abaixo de 1,00 = combinação pouco jogada).",
           "Modelo = o que a rede estimou só olhando o resultado. Ela acerta a ORDEM (qual é mais ou menos jogada); "
           "os valores ficam mais perto de 1 porque a regularização encolhe a previsão de propósito.",
           "Teste = o concurso estava entre os que o modelo não viu no treino.", "",
           f"{'Concurso':>9}{'Acumulou':>10}{'Ganhad.':>9}  {'Resultado':<46}{'Real':>7}{'Modelo':>8}  Teste?"]
    for c in casos[-15:]:
        dz = " ".join(f"{x:02d}" if r["jogo"] != "supersete" else str(x)
                      for x in (c["dz"] if r["jogo"] == "supersete" else sorted(c["dz"])))
        out.append(f"{c['concurso']:>9}{c['acumulou']:>10}{c['ganhadores']:>9}  {dz[:45]:<46}{c['real']:>7.2f}"
                   f"{c['modelo']:>8.2f}  {'sim' if c['no_teste'] else 'não'}")
    if casos:
        reais = [c["real"] for c in casos]
        abaixo = sum(x < 1 for x in reais)
        out += ["", f"{len(casos)} prêmios que acumularam e saíram: em {abaixo} ({abaixo / len(casos):.0%}) a "
                    f"combinação era menos jogada que o típico (mediana {sorted(reais)[len(reais) // 2]:.2f}x)."]
        teste = [c for c in casos if c["no_teste"]]
        if len(teste) >= 5:
            r2 = _r2([math.log(c["modelo"]) for c in teste], [math.log(c["real"]) for c in teste])
            out.append(f"Nos casos que o modelo não viu no treino, ele explica {max(0, r2):.0%} da popularidade real.")
    else:
        out += ["", "Nenhum prêmio acumulou tanto e saiu no período com dados de ganhadores."]
    out += ["", "Faz sentido: combinação pouco jogada demora mais para ter ganhador, e quando sai, sai para "
                "poucos. A chance de cada combinação ser sorteada continua igual."]
    return "\n".join(out)


# ---------------- treino automático ----------------
_treinados = {}   # jogo -> último concurso com que o modelo foi treinado nesta sessão
_trava_treino = threading.Lock()   # atualização automática e "Baixar resultados" podem chamar ao mesmo tempo


def treinar_todos(avisar=print, forcar=False):
    """Retreina a popularidade dos jogos que ganharam concursos novos (roda depois de baixar resultados).
    Jogos sem novidade são pulados, inclusive entre aberturas do app (o modelo guarda o último concurso usado).
    Uma trava impede dois treinos simultâneos: o segundo espera e depois pula o que o primeiro já fez."""
    from core import last_concurso
    ok = []
    with _trava_treino:
        for j in POP_JOGOS:
            ultimo = last_concurso(j)
            m = _modelo(j)
            if not forcar and m and ultimo in (_treinados.get(j), m.get("ultimo_concurso")):
                continue
            try:
                m = treinar_popularidade(j)
                _treinados[j] = ultimo
                with open(_pop_path(j), "w", encoding="utf8") as f:
                    json.dump({**m, "ultimo_concurso": ultimo}, f)
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
    if M[jogo].get("colunas"):
        from core import dist_colunas
        for h, qtd in enumerate(dist_colunas(card, d)):
            p = premio(jogo, h, 0, pr) if h in fx and qtd else None
            if p and p[1]:
                ganho += qtd * p[1]
                faixas.append(p[0])
        return ganho, faixas
    from core import contidas
    h = len(set(card) & set(d))
    th = len(set(tv) & set(meta.get("trevos", []))) if M[jogo].get("trevos") else 0
    for j, t2, qtd in contidas(jogo, len(card), h, len(tv) if M[jogo].get("trevos") else 0, th):
        p = premio(jogo, j, t2, pr)
        if p and p[1]:
            ganho += qtd * p[1]
            faixas.append(p[0])
    if jogo in EXTRA and tv and meta.get(EXTRA[jogo][0]) == tv[0]:
        val = dict((a, b) for a, b in pr).get(EXTRA[jogo][1]) or 0
        ganho += val
        faixas.append(EXTRA[jogo][1])
    return ganho, faixas


def _extra_acaso(jogo, rng, times=None):
    if M[jogo].get("extra", ("",))[0] == "mes":
        return [rng.choice(MESES)]
    if times is None:
        from core import times_validos
        times = times_validos()[0]
    return [rng.choice(times)] if times else []


def testar_no_passado(jogo, k, n=None, concursos=104, retorno_min=0.0, seed=7, nulos=100):
    """Joga o plano (k cartões espalhados de n dezenas) em cada um dos últimos concursos, com os prêmios
    reais pagos. Com retorno_min, só joga quando o retorno estimado antes do sorteio passava do mínimo.
    Compara com a distribuição de 'nulos' sequências de cartões ao acaso (mesmos concursos e gasto), não com uma
    só: uma sequência isolada varia muito com a semente."""
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
    tam = formatos_supersete()[n] if M[jogo].get("colunas") and n > 7 else None
    preco = (JOGOS[jogo][4] * math.prod(tam) if tam
             else custo(jogo, list(range(1, n + 1)), [1, 2] if M[jogo].get("trevos") else []))
    # o modelo salvo pode ter visto os concursos testados: retreina só com os anteriores ao 1º deles
    pop_ate = rows[0][0] if pesos_popularidade(jogo) else None
    pesos = (pesos_ate(jogo, pop_ate) or None) if pop_ate else None

    sorteios = {conc: list(_sorteios(jogo, [r])) for r in rows for conc in [r[0]]}
    if M[jogo].get("extra", ("",))[0] == "time":
        from core import times_validos
        times = times_validos()[0]
    else:
        times = None

    def plano(espalhado, rng):
        res = {"jogados": 0, "gasto": 0.0, "ganho": 0.0, "com_premio": 0, "maior": 0.0, "faixas": Counter()}
        for conc, _, _ in rows:
            if filtro is not None and filtro.get(conc, 0) < retorno_min:
                continue
            if espalhado:
                cards = cartoes_supersete(k, tam, rng) if tam else cartoes_espalhados(jogo, k, n, pesos, rng)
            elif M[jogo].get("colunas"):
                cards = [[(rng.randrange(10) if t == 1 else sorted(rng.sample(range(10), t))) for t in (tam or [1] * 7)]
                         for _ in range(k)]
            else:
                cards = [sorted(rng.sample(range(1, N + 1), n)) for _ in range(k)]
            if espalhado:
                tvs = extras_espalhados(jogo, k, rng)
            elif M[jogo].get("trevos"):
                tvs = [sorted(rng.sample(range(1, 7), 2)) for _ in cards]
            elif jogo in EXTRA:
                tvs = [_extra_acaso(jogo, rng, times) for _ in cards]
            else:
                tvs = [[] for _ in cards]
            g_conc = 0.0
            for _, d, meta in sorteios[conc]:
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

    from core import preco_vigente
    sem_preco = sum(not preco_vigente(jogo, conc)[1] for conc, _, _ in rows)
    return {"jogo": jogo, "k": k, "n": n, "concursos": len(rows), "retorno_min": retorno_min, "sem_preco": sem_preco,
            "pop_ate": pop_ate if pesos else None, "esp": plano(True, rng), "acaso": plano(False, rng),
            "nulo": [(x["com_premio"], x["ganho"] / x["gasto"] if x["gasto"] else 0.0)
                     for x in (plano(False, random.Random(seed * 1000 + i)) for i in range(nulos))]}


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
           "(o sorteio ainda não era conhecido)."
           + (f" Números pouco jogados: modelo treinado só com concursos anteriores ao {r['pop_ate']}."
              if r.get("pop_ate") else ""), ""]
    out += bloco("Plano do Otimizador (cartões espalhados)", r["esp"]) + [""]
    out += bloco("Mesmo gasto com cartões ao acaso (uma das sequências)", r["acaso"])
    nulo = r.get("nulo") or []
    if nulo and r["esp"]["jogados"]:
        cp, rt = sorted(x[0] for x in nulo), sorted(x[1] for x in nulo)
        e, ret = r["esp"]["com_premio"], r["esp"]["ganho"] / r["esp"]["gasto"]
        q = lambda v: (v[len(v) // 20], v[len(v) * 19 // 20])
        out += ["", f"Comparação com {len(nulo)} sequências ao acaso (mesmos concursos e gasto):",
                f"  concursos com prêmio: plano {e}; acaso {sum(cp) / len(cp):.1f} em média, 90% entre {q(cp)[0]} e "
                f"{q(cp)[1]}; o plano superou {sum(x < e for x in cp) / len(cp):.0%} das sequências",
                f"  retorno: plano {ret:.0%}; acaso {sum(rt) / len(rt):.0%} em média, 90% entre {q(rt)[0]:.0%} e "
                f"{q(rt)[1]:.0%}; o plano superou {sum(x < ret for x in rt) / len(rt):.0%} das sequências"]
    if r.get("sem_preco"):
        out += ["", f"Preço: {r['sem_preco']} dos {r['concursos']} concursos são anteriores à vigência registrada do "
                    "preço atual; o preço da época não está no app, então foi usado o atual."]
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
    tv = [1, 2] if M[j].get("trevos") else []
    n_simples = (math.prod(plano["tamanhos"]) if plano.get("tamanhos") else apostas_simples(
        j, list(range(plano["n"])), tv)) * (1 if plano["tipo"] == "bolão" else plano["k"])
    fator = 1 / plano["cotas"] if plano["tipo"] == "bolão" else 1.0
    if M[j].get("sorteios") == 2:
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
    N, K = JOGOS[jogo][:2]
    rows = db().execute("SELECT concurso, dezenas, meta FROM draws WHERE jogo=? ORDER BY concurso", (jogo,)).fetchall()
    draws = []
    for _, d, meta in rows:   # Dupla Sena: os dois sorteios de cada concurso entram, em ordem
        draws.append(json.loads(d))
        d2 = json.loads(meta or "{}").get("dezenas2") if M[jogo].get("sorteios") == 2 else None
        if d2:
            draws.append(d2)
    from core import datas_fora_de_ordem
    T = len(draws)
    if T < 100:
        raise ValueError(f"{jogo}: baixe os resultados primeiro (há {T} concursos).")
    testes = []
    if M[jogo].get("colunas"):
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
    return {"jogo": jogo, "concursos": len(rows), "sorteios": T, "testes": [(*t, a) for t, a in zip(testes, ajust)],
            "datas_fora_de_ordem": datas_fora_de_ordem(jogo)}


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
    if r.get("sorteios", r["concursos"]) != r["concursos"]:
        out.append(f"Dupla Sena: os {r['sorteios']} sorteios ({r['concursos']} concursos x 2) entram nos testes.")
    if r.get("datas_fora_de_ordem"):
        out.append("Datas que recuam em relação ao concurso anterior (provável erro na fonte): "
                   + ", ".join(f"{c} ({d})" for c, d in r["datas_fora_de_ordem"]))
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
    ev2 = (resto + p_top * val) * M[jogo].get("sorteios", 1)
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


# ---------------- uma fonte só para sugestões e para a IA ----------------
def sugestoes(jogo, qtd=5, n=None, rng=None):
    """Única fonte de apostas sugeridas do app (aba Análise, Otimizador e IA): cartões espalhados e, com modelo
    de popularidade confiável, com os números menos jogados. Nenhum número 'quente' ou 'atrasado': o
    Laboratório mostrou que isso não acerta mais que o acaso.
    Com modelo de popularidade, gera vários conjuntos igualmente espalhados e fica com o que divide menos o
    prêmio (média e pior cartão): espalhar decide a cobertura, a popularidade decide entre os empates."""
    rng = rng or random
    pesos = pesos_popularidade(jogo)
    if M[jogo].get("colunas") and n and n > 7:
        return cartoes_supersete(qtd, formatos_supersete()[n], rng)
    if not pesos or M[jogo].get("colunas"):
        return cartoes_espalhados(jogo, qtd, n, pesos, rng)
    m = _modelo(jogo)
    ant = m.get("anterior", [])
    melhor, nota_melhor = None, None
    for _ in range(TENTATIVAS_SUGESTAO):
        cs = cartoes_espalhados(jogo, qtd, n, pesos, rng)
        fs = [_prever(jogo, m, {"dz": c, "ant": ant, "trevos": []}) for c in cs]
        nota = (sum(fs) / len(fs), max(fs))
        if nota_melhor is None or nota < nota_melhor:
            melhor, nota_melhor = cs, nota
    return _menos_divididos(jogo, melhor, m, rng)


TENTATIVAS_SUGESTAO = 20
TROCAS_SUGESTAO = 2000


def _sobreposicao(cs):
    s = [len(set(a) & set(b)) for i, a in enumerate(cs) for b in cs[i + 1:]]
    return max(s, default=0), sum(x * x for x in s)


def _menos_divididos(jogo, cs, m, rng, trocas=TROCAS_SUGESTAO):
    """Busca local: troca um número de um cartão por outro sem mexer na cobertura (o uso de cada número continua
    equilibrado e a sobreposição entre cartões não piora) e aceita a troca quando o conjunto divide menos o
    prêmio (pior cartão + média). As tentativas de 'sugestoes' quase sempre saem iguais quando há pesos, então
    é esta busca que de fato escolhe os números menos jogados."""
    N = JOGOS[jogo][0]
    ant = m.get("anterior", [])
    cs = [sorted(c) for c in cs]
    fs = [_prever(jogo, m, {"dz": c, "ant": ant, "trevos": []}) for c in cs]
    uso = Counter(x for c in cs for x in c)
    for x in range(1, N + 1):
        uso.setdefault(x, 0)
    faixa = max(uso.values()) - min(uso.values())
    sob = _sobreposicao(cs)
    nota = max(fs) + sum(fs) / len(fs)
    def prev(c):
        return _prever(jogo, m, {"dz": c, "ant": ant, "trevos": []})

    for _ in range(trocas):
        i = rng.randrange(len(cs))
        x = rng.choice(cs[i])
        if len(cs) > 1 and rng.random() < 0.5:
            # troca entre dois cartões: x vai de i para j e y de j para i (o uso de cada número não muda)
            j = rng.randrange(len(cs) - 1)
            j += j >= i
            y = rng.choice(cs[j])
            if y in cs[i] or x in cs[j]:
                continue
            mud = {i: sorted(y if v == x else v for v in cs[i]), j: sorted(x if v == y else v for v in cs[j])}
            dif = None
        else:
            # troca por um número de fora do cartão, mantendo o uso dos números equilibrado
            y = rng.randrange(1, N + 1)
            if y in cs[i]:
                continue
            uso[x] -= 1; uso[y] += 1
            if max(uso.values()) - min(uso.values()) > faixa:
                uso[x] += 1; uso[y] -= 1
                continue
            mud, dif = {i: sorted(y if v == x else v for v in cs[i])}, (x, y)
        teste = [mud.get(t, c) for t, c in enumerate(cs)]
        s = _sobreposicao(teste)
        fs2 = [prev(mud[t]) if t in mud else f for t, f in enumerate(fs)]
        n2 = max(fs2) + sum(fs2) / len(fs2)
        if s[0] <= sob[0] and s[1] <= sob[1] and n2 < nota - 1e-12:
            cs, fs, nota, sob = teste, fs2, n2, s
        elif dif:
            uso[dif[0]] += 1; uso[dif[1]] -= 1
    return cs


TESTADO_NAO_FUNCIONA = (
    "Testado com os resultados reais e SEM efeito (igual ao acaso): números quentes, frios/atrasados, repetir o "
    "último resultado, rede neural de previsão, 'aprender com erros e acertos' e treino por tentativa e erro "
    "(mais de 5.000 concursos, testes cegos com impressão digital). Testes exploratórios com redes grandes e com "
    "a ordem das bolas também não mostraram vantagem, mas reaproveitaram o mesmo período de teste: não são "
    "confirmatórios (ver experimentos.py). Nenhum método prevê os números sorteados.")
TESTADO_FUNCIONA = (
    "Testado e COM efeito: escolher o jogo pela chance de prêmio por real; cartões espalhados (mais concursos "
    "com prêmio pelo mesmo gasto); time/mês/trevos sem repetir entre cartões; números pouco jogados pelo público "
    "(não mudam a chance, mas dividem menos o prêmio); fechamentos com garantia condicional; cota de bolão "
    "para mirar um valor de prêmio gastando pouco (Otimizador, 'Aposta mínima, prêmio maior'); jogar quando o "
    "prêmio anunciado está alto devolve mais por real (Mega e Quina quase dobram, mas seguem abaixo de 100%).")


def fatos_app(jogo):
    """Bloco de fatos calculados pelo app, enviado à IA como fonte de verdade."""
    linhas = [f"FATOS DO APP (fonte de verdade; não contrarie): jogo selecionado {jogo}."]
    try:
        linhas.append(f"- Chance de algum prêmio numa aposta simples: {_um_em(prob_premio(jogo))}; "
                      f"prêmio principal: {_um_em(prob_principal(jogo))}.")
    except Exception:
        log.warning("fatos: chances", exc_info=True)
    try:
        ev = valor_esperado(jogo)
        if ev["retorno"] > 0:   # banco vazio dá 0%: melhor não dizer nada
            linhas.append(f"- Retorno médio hoje: {ev['retorno']:.0%} do valor apostado (abaixo de 100% = perde "
                          "em média).")
    except Exception:
        pass
    m = _modelo(jogo)
    if m:
        linhas.append(f"- Modelo de popularidade: explica cerca de {m['r2_teste']:.0%} da variação do número de "
                      "ganhadores em concursos de teste (correlação²; é a nota do modelo, NÃO a popularidade de um "
                      f"cartão; {'usado' if m.get('confiavel') else 'não confiável, não usado'}"
                      + ("" if m.get("teste_independente", True) else "; período de teste já consultado") + ").")
    melhores = sorted(JOGOS, key=lambda j: -prob_premio(j) / JOGOS[j][4])[:3]
    precos = ", ".join(f"{j} R$ {JOGOS[j][4]:.2f}".replace(".", ",") for j in JOGOS)
    linhas.append(f"- Preço da aposta simples: {precos}. Todos os cartões têm a MESMA chance de acerto; "
                  "o fechamento custa um cartão por linha (não sai pelo preço de uma aposta).")
    linhas += [f"- Mais fáceis de premiar por real: {', '.join(melhores)}.", "- " + TESTADO_NAO_FUNCIONA,
               "- " + TESTADO_FUNCIONA]
    return "\n".join(linhas)


_ALERTAS = [
    (r"\bprev(ê|e|er|endo|isão|isões|isto|ista)\b.{0,40}\b(n[uú]mero|dezena|resultado|sorteio)",
     "falou em prever números/resultados: nenhum método prevê sorteios (testado no Laboratório)."),
    (r"\b(quentes?|frios?|atrasad[oa]s?)\b",
     "citou números quentes/frios/atrasados: o Laboratório mostrou que isso não acerta mais que o acaso."),
    (r"est(á|ao|ão) (para|pra) sair|vai sair|deve sair|tend[eê]ncia de sair|ciclo",
     "sugeriu que algum número 'está para sair': cada sorteio é independente do anterior."),
    (r"\bgarant(e|ido|ida|ia de) (o |a )?(lucro|ganh|acert|pr[eê]mio)",
     "falou em garantia de ganho: só fechamentos têm garantia, e ela é condicional (se seus números saírem)."),
    (r"\b(lucro certo|ganhar sempre|estrat[eé]gia infal[ií]vel|segredo)\b",
     "prometeu ganho certo: o retorno médio de todas as loterias fica abaixo de 100%."),
]


_NEGA = (r"\bn[ãa]o\b|\bnenhum|\bnem\b|\bnunca\b|sem efeito|igual ao acaso|\bmito\b|imposs[íi]vel|ningu[ée]m|\bnada\b|indisting|"
         r"\bmesmo (usando|jogando|com)\b|independente|aleat[óo]ri|\btest(ou|ado|e)\b|\bevite\b|em vez de")
# contextos em que o alerta não se aplica: prever a POPULARIDADE (o que o público joga) é legítimo, e a
# garantia de fechamento é verdadeira quando dita como condicional
_ALERTA_OK = {0: r"popular|p[úu]blico|(mais|menos|muito|pouco) jogad",
              3: r"condicional|fechamento|se .{0,40}\bsa[íi]rem\b"}


def fatos_sugestoes(jogo, cartoes):
    """Cartões sugeridos já ordenados pelo app, com o melhor definido: a IA explica, não compara números
    (modelos pequenos erram comparações, ex.: chamar 1,08x de 'divide menos')."""
    fs = [(c, fator_divisao(jogo, c)) for c in cartoes]
    if not all(f for _, f in fs):
        return "Apostas sugeridas agora pelo app (cartões espalhados; todas com a mesma chance):\n" + "\n".join(
            f"  {' '.join(f'{x:02d}' for x in c)}" for c, _ in fs)
    fs.sort(key=lambda t: t[1])
    linhas = ["Apostas sugeridas agora pelo app, JÁ ORDENADAS da que divide MENOS o prêmio para a que divide MAIS "
              "(todas têm exatamente a mesma chance de acerto e o mesmo valor esperado):"]
    for i, (c, f) in enumerate(fs, 1):
        rot = "abaixo da média: divide MENOS" if f < 1 else "acima da média: divide MAIS"
        linhas.append(f"  {i}. {' '.join(f'{x:02d}' for x in c)} | {f:.2f}x ({rot})")
    melhor = fs[0]
    linhas.append(f"MELHOR CARTÃO (decidido pelo app; recomende este se perguntarem): "
                  f"{' '.join(f'{x:02d}' for x in melhor[0])} ({melhor[1]:.2f}x).")
    return "\n".join(linhas)


def checar_resposta(texto, jogo, permitidos=(), sugestoes=None):
    """Confere a resposta da IA contra o que o app testou. Devolve avisos (vazio se estiver tudo certo).
    sugestoes: [(cartão, fator de divisão)] para conferir se a IA recomendou o cartão certo."""
    avisos = []
    texto = re.sub(r"\d+[.,]\d+", " ", texto)   # 1,08x / 8.77% não são números de aposta
    # frase a frase: "números quentes NÃO funcionam" é a resposta certa, não uma contradição
    # itens de lista sob um título negado ("Sem efeito:" / "**Testado e sem efeito**") herdam a negação
    herda = False
    frases = []
    for linha in texto.lower().splitlines():
        if not re.match(r"\s*([-*•]|\d+[.)])\s", linha):
            titulo = re.search(r"(:|\*\*)\s*$", linha) or linha.lstrip().startswith("#")
            herda = bool(titulo and re.search(_NEGA, linha))
        elif herda:
            continue
        frases += re.split(r"(?<=[.!?;:])\s+", linha)
    for k, frase in enumerate(frases):
        # "**Números quentes**: não afetam a chance" — o rótulo antes do ':' herda a negação que vem depois
        seguinte = frases[k + 1] if frase.rstrip().endswith(":") and k + 1 < len(frases) else ""
        if re.search(_NEGA, frase) or re.search(_NEGA, seguinte):
            continue
        for i, (padrao, msg) in enumerate(_ALERTAS):
            if re.search(padrao, frase) and not (i in _ALERTA_OK and re.search(_ALERTA_OK[i], frase)):
                avisos.append(msg)
    # sequências de números que parecem uma aposta e não vieram do app
    N, _, mn = JOGOS[jogo][:3]
    ok = {tuple(sorted(p)) for p in permitidos}
    for linha in texto.splitlines():
        nums = [int(x) for x in re.findall(r"\b\d{1,2}\b", linha)]
        nums = [x for x in nums if 1 <= x <= N]
        if len(set(nums)) >= max(5, mn - 1) and not any(set(nums) <= set(p) or set(p) <= set(nums) for p in ok):
            avisos.append("sugeriu números que não vieram do app: use as apostas geradas pelo Otimizador.")
            break
    # recomendou o cartão que divide mais?
    fs = [(set(c), f) for c, f in (sugestoes or []) if f]
    if fs:
        melhor_c, melhor_f = min(fs, key=lambda t: t[1])
        linhas = texto.lower().splitlines()
        for i, linha in enumerate(linhas):
            if not re.search(r"recomend|melhor (escolha|aposta|cart)|aposte\b", linha):
                continue
            trecho = " ".join(linhas[i:i + 3])
            seq = [int(x) for x in re.findall(r"\b\d{1,2}\b", trecho)]
            # só o PRIMEIRO cartão citado depois da palavra é a recomendação; os seguintes são "outras opções"
            achados = [(p, c, f) for c, f in fs for p in range(len(seq))
                       if set(seq[p:p + len(c)]) == c]
            if achados:
                _, c, f = min(achados, key=lambda t: t[0])
                if f > melhor_f + 0.02:
                    avisos.append(f"recomendou o cartão que divide MAIS o prêmio ({f:.2f}x); o melhor é "
                                  f"{' '.join(f'{x:02d}' for x in sorted(melhor_c))} ({melhor_f:.2f}x).")
                    break
    return list(dict.fromkeys(avisos))


def texto_chances_cartoes(jogo, cartoes, n_sim=20000):
    """Chance de cada faixa por cartão (exata) e do conjunto de cartões (simulada; a principal é exata)."""
    fx = sorted(h for h in JOGOS[jogo][3] if h > 0)
    um = lambda p: (f"{p:.1%}" if p >= .05 else "1 em " + f"{1 / p:,.0f}".replace(",", ".")) if p else "-"
    n = len(cartoes[0])
    out = ["Chances de cada cartão (são iguais para qualquer cartão; nenhuma escolha de números muda isso):",
           "  " + " | ".join(f"{h} acertos: {um(prob_faixa(jogo, h, n))}" for h in reversed(fx))]
    p1 = prob_premio(jogo, n, extra=False)
    if p1 * len(cartoes) < 0.05:
        # chance pequena: a simulação teria poucos casos; com cartões espalhados a conta direta é precisa
        p_conj = 1 - (1 - p1) ** len(cartoes)
    else:
        p_conj = simular_cobertura(jogo, cartoes, n_sim, random.Random(11))
    top = prob_faixa(jogo, max(fx), n) * len({tuple(c) for c in cartoes})
    out += [f"Com os {len(cartoes)} cartões juntos, por concurso:",
            f"  algum prêmio: {um(p_conj)} (um cartão sozinho: {um(p1)})",
            f"  prêmio principal ({max(fx)} acertos): {um(top)}"]
    return "\n".join(out)
