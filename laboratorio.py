"""Laboratório de estratégias: testa ideias de aposta às cegas, com os resultados reais.

Regra de ouro: cada estratégia só recebe os concursos ANTERIORES ao que está apostando. No teste cego as
apostas são gravadas e ganham uma impressão digital (SHA-256) antes de qualquer resultado ser lido; no
registro para sorteios futuros, os concursos ainda nem aconteceram.
"""
import hashlib, json, math, random
from collections import Counter
from datetime import datetime

from core import JOGOS, db, hypergeom, last_concurso, learn, load_draws, parse_bet
import otimizador as o

ESTRATEGIAS = {
    "ao acaso": "números sorteados ao acaso (referência)",
    "quentes": "os que mais saíram nos últimos 50 concursos",
    "frios": "os mais atrasados",
    "repetir último": "o resultado do último concurso",
    "menos jogados": "os que o público menos joga (modelo de popularidade)",
    "modelo neural": "os de maior chance segundo o neurônio treinado no histórico",
    "otimizador": "10 cartões espalhados do Otimizador",
    "minha aposta": "os números que você digitar",
}
JOGOS_LAB = [j for j in JOGOS if j not in ("supersete", "maismilionaria")]   # acertos = dezenas em comum


def _sql_lab():
    with db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS lab(id INTEGER PRIMARY KEY, user TEXT, jogo TEXT, concurso INT,
                     estrategia TEXT, numeros TEXT, criado TEXT, hash TEXT)""")


# ---------------- estratégias (só enxergam o passado) ----------------
def _pop_passado(jogo, ate):
    """Pesos de popularidade treinados só com concursos anteriores a 'ate' (sem olhar o futuro)."""
    am = [a for a in o._amostras(jogo) if a["concurso"] < ate]
    if len(am) < 200:
        return {}
    cfg = {"nome": "lab", "modelo": "ridge", "alvo": "multi", "padroes": False, "meia_vida": 500}
    c = int(len(am) * 0.8)
    escolhida = o._ajusta(jogo, am[:c], cfg, am[c:])["cfg"]   # regularização escolhida na validação
    m = o._ajusta(jogo, am, escolhida)
    N = JOGOS[jogo][0]
    return {i + 1: m["coef"][i] for i in range(N)}


def _apostas(jogo, passado, ctx):
    """Aposta de cada estratégia para o próximo concurso, usando só 'passado' (lista de (concurso, dezenas))."""
    N, K, mn = JOGOS[jogo][:3]
    rng = ctx["rng"]
    draws = [d for _, d in passado]
    out = {}
    sel = ctx["estrategias"]
    if "ao acaso" in sel:
        out["ao acaso"] = [sorted(rng.sample(range(1, N + 1), mn))]
    if "quentes" in sel:
        freq = Counter(x for d in draws[-50:] for x in d)
        out["quentes"] = [sorted(sorted(range(1, N + 1), key=lambda x: (-freq[x], x))[:mn])]
    if "frios" in sel:
        ultimo = {}
        for i, d in enumerate(draws):
            for x in d:
                ultimo[x] = i
        out["frios"] = [sorted(sorted(range(1, N + 1), key=lambda x: (ultimo.get(x, -1), x))[:mn])]
    if "repetir último" in sel:
        u = sorted(draws[-1])
        resto = [x for x in range(1, N + 1) if x not in u]
        out["repetir último"] = [sorted((u + rng.sample(resto, max(0, mn - len(u))))[:mn])]
    if "menos jogados" in sel:
        pop = ctx.get("pop") or {}
        out["menos jogados"] = [sorted(sorted(range(1, N + 1), key=lambda x: (pop.get(x, 0), rng.random()))[:mn])]
    if "modelo neural" in sel:
        if ctx.get("neural") is None or ctx.get("neural_idade", 99) >= 25:
            _, pick = learn(jogo, passado[-600:], mn)
            ctx["neural"], ctx["neural_idade"] = pick, 0
        ctx["neural_idade"] = ctx.get("neural_idade", 0) + 1
        if ctx["neural"]:
            out["modelo neural"] = [sorted(ctx["neural"])]
    if "otimizador" in sel:
        out["otimizador"] = o.cartoes_espalhados(jogo, 10, mn, ctx.get("pop") or None, rng)
    if "minha aposta" in sel and ctx.get("minha"):
        out["minha aposta"] = [sorted(ctx["minha"])]
    return out


# ---------------- estatística ----------------
def _avaliar(jogo, registros):
    """registros: {estratégia: [(acertos de cada cartão), ...]} -> tabela com z, valor-p e correção de Holm."""
    N, K, mn, fx, _ = JOGOS[jogo]
    pmin = min(h for h in fx if h > 0)
    esp = mn * K / N
    sd = math.sqrt(mn * (K / N) * (1 - K / N) * (N - mn) / (N - 1))
    p_premio = sum(hypergeom(N, K, mn, h) for h in fx if h >= pmin)
    linhas = []
    for nome, por_conc in registros.items():
        hits = [h for cartoes in por_conc for h in cartoes]
        n = len(hits)
        if not n:
            continue
        media = sum(hits) / n
        # cartões do mesmo concurso não são independentes: o erro padrão usa o número de concursos
        n_ef = len(por_conc)
        media_conc = [sum(c) / len(c) for c in por_conc]
        sd_conc = sd / math.sqrt(len(por_conc[0]))
        z = (sum(media_conc) / n_ef - esp) / (sd_conc / math.sqrt(n_ef))
        p = math.erfc(abs(z) / math.sqrt(2))
        premios = sum(h >= pmin for h in hits)
        linhas.append({"nome": nome, "concursos": n_ef, "apostas": n, "media": media, "dif": media / esp - 1,
                       "z": z, "p": p, "premios": premios, "premios_esp": n * p_premio, "melhor": max(hits)})
    ordem = sorted(range(len(linhas)), key=lambda i: linhas[i]["p"])
    maior = 0.0
    for r, i in enumerate(ordem):   # correção de Holm: várias estratégias testadas ao mesmo tempo
        maior = max(maior, min(1.0, (len(linhas) - r) * linhas[i]["p"]))
        linhas[i]["p_ajust"] = maior
    return {"jogo": jogo, "esperado": esp, "faixa": pmin, "p_premio": p_premio, "linhas": linhas}


# ---------------- testes ----------------
def teste_cego(jogo, escondidos=100, estrategias=None, minha=None, seed=2026):
    """Esconde os últimos 'escondidos' concursos, aposta em todos de uma vez só com o passado, grava o hash das
    apostas e só então revela os resultados."""
    if jogo not in JOGOS_LAB:
        raise ValueError(f"Laboratório disponível para: {', '.join(JOGOS_LAB)}.")
    todos = sorted(load_draws(jogo, 100000))
    if len(todos) < escondidos + 200:
        raise ValueError(f"{jogo}: baixe os resultados primeiro (há {len(todos)} concursos).")
    passado, futuro = todos[:-escondidos], todos[-escondidos:]
    ctx = {"rng": random.Random(seed), "estrategias": set(estrategias or ESTRATEGIAS), "minha": minha,
           "pop": _pop_passado(jogo, futuro[0][0]) if "menos jogados" in (estrategias or ESTRATEGIAS)
           or "otimizador" in (estrategias or ESTRATEGIAS) else {}}
    apostas = {}
    base = _apostas(jogo, passado, ctx)   # estratégias fixas: uma aposta feita antes de tudo
    for conc, _ in futuro:
        a = dict(base)
        if "ao acaso" in ctx["estrategias"]:
            a["ao acaso"] = [sorted(ctx["rng"].sample(range(1, JOGOS[jogo][0] + 1), JOGOS[jogo][2]))]
        apostas[conc] = a
    txt = json.dumps(apostas, sort_keys=True)
    selo = hashlib.sha256(txt.encode()).hexdigest()
    # ---- revelação: só a partir daqui os resultados são lidos ----
    assert hashlib.sha256(txt.encode()).hexdigest() == selo
    reais = {c: set(d) for c, d in futuro}
    reg = {}
    for conc, a in apostas.items():
        for nome, cartoes in a.items():
            reg.setdefault(nome, []).append([len(set(c) & reais[conc]) for c in cartoes])
    r = _avaliar(jogo, reg)
    r.update(modo=f"teste cego: {escondidos} concursos escondidos ({futuro[0][0]} a {futuro[-1][0]}), "
                  f"apostas feitas de uma vez com dados até o concurso {passado[-1][0]}", hash=selo)
    return r


def teste_continuo(jogo, concursos=300, estrategias=None, minha=None, seed=2026, eta=0.3):
    """Cada concurso é apostado só com os anteriores; inclui o 'aprende com erros e acertos' (Hedge), que dá
    mais peso às estratégias que vêm acertando mais."""
    if jogo not in JOGOS_LAB:
        raise ValueError(f"Laboratório disponível para: {', '.join(JOGOS_LAB)}.")
    todos = sorted(load_draws(jogo, 100000))
    if len(todos) < concursos + 200:
        raise ValueError(f"{jogo}: baixe os resultados primeiro (há {len(todos)} concursos).")
    N, K, mn = JOGOS[jogo][:3]
    sel = set(estrategias or ESTRATEGIAS)
    ini = len(todos) - concursos
    ctx = {"rng": random.Random(seed), "estrategias": sel, "minha": minha,
           "pop": _pop_passado(jogo, todos[ini][0]) if sel & {"menos jogados", "otimizador"} else {}}
    simples = [s for s in sel if s != "otimizador" and (s != "minha aposta" or minha)]
    peso = {s: 1.0 for s in simples}
    reg, esp = {}, mn * K / N
    for t in range(ini, len(todos)):
        a = _apostas(jogo, todos[:t], ctx)
        if peso:
            escolhida = max((s for s in peso if s in a), key=lambda s: (peso[s], ctx["rng"].random()), default=None)
            if escolhida:
                a["aprende (Hedge)"] = a[escolhida]
        sorteio = set(todos[t][1])
        hits = {nome: [len(set(c) & sorteio) for c in cartoes] for nome, cartoes in a.items()}
        for nome, h in hits.items():
            reg.setdefault(nome, []).append(h)
        for s in peso:   # aprende com o resultado
            if s in hits:
                peso[s] *= math.exp(eta * (hits[s][0] - esp))
        tot = sum(peso.values()) or 1
        peso = {s: v / tot for s, v in peso.items()}
    r = _avaliar(jogo, reg)
    r.update(modo=f"teste contínuo: últimos {concursos} concursos, cada um apostado só com os anteriores",
             pesos_hedge=peso)
    return r


def texto_resultado(r):
    out = [f"=== LABORATÓRIO: {r['jogo'].upper()} ===", r["modo"]]
    if r.get("hash"):
        out.append(f"Impressão digital das apostas (gravada antes de ver os resultados): {r['hash'][:16]}…")
    out += [f"Pelo acaso: {r['esperado']:.2f} acertos por aposta; prêmio ({r['faixa']}+) em {r['p_premio']:.1%} "
            "das apostas.", "",
            f"{'Estratégia':<18}{'Apostas':>8}{'Média':>8}{'vs acaso':>10}{'valor-p':>9}{'ajust.':>8}"
            f"{'Prêmios':>9}{'esperado':>10}{'Melhor':>8}  Veredito"]
    reais = 0
    for li in sorted(r["linhas"], key=lambda x: -x["media"]):
        real = li["p_ajust"] < 0.05
        reais += real and li["dif"] > 0
        ver = ("ACIMA do acaso (real)" if real and li["dif"] > 0 else "abaixo do acaso (real)" if real
               else "igual ao acaso")
        out.append(f"{li['nome']:<18}{li['apostas']:>8}{li['media']:>8.2f}{li['dif']:>+10.0%}{li['p']:>9.3f}"
                   f"{li['p_ajust']:>8.3f}{li['premios']:>9}{li['premios_esp']:>10.1f}{li['melhor']:>8}  {ver}")
    if r.get("pesos_hedge"):
        out += ["", "Pesos finais do 'aprende (Hedge)': " + ", ".join(
            f"{n} {v:.0%}" for n, v in sorted(r["pesos_hedge"].items(), key=lambda kv: -kv[1]))]
    out += ["", ("Alguma estratégia ficou acima do acaso mesmo com a correção: repita o teste com outros "
                 "concursos antes de acreditar (registre a hipótese primeiro)." if reais else
                 "Nenhuma estratégia acertou mais números que o acaso. Diferenças de alguns % são sorte da amostra: "
                 "o valor-p ajustado (abaixo de 0,05 = diferença real) já desconta o fato de testar várias ideias."),
            "Mais cartões dão mais prêmios na mesma proporção do gasto; isso aparece em 'Prêmios' x 'esperado'."]
    return "\n".join(out)


# ---------------- registro para sorteios futuros ----------------
def registrar_proximo(user, jogo, estrategias=None, minha=None):
    """Grava as apostas para o próximo concurso, que ainda não aconteceu, com impressão digital."""
    if jogo not in JOGOS_LAB:
        raise ValueError(f"Laboratório disponível para: {', '.join(JOGOS_LAB)}.")
    _sql_lab()
    todos = sorted(load_draws(jogo, 100000))
    alvo = last_concurso(jogo) + 1
    ctx = {"rng": random.Random(), "estrategias": set(estrategias or ESTRATEGIAS), "minha": minha,
           "pop": _pop_passado(jogo, alvo)}
    a = _apostas(jogo, todos, ctx)
    criado = datetime.now().isoformat(timespec="seconds")
    selo = hashlib.sha256(json.dumps({"jogo": jogo, "concurso": alvo, "apostas": a, "criado": criado},
                                     sort_keys=True).encode()).hexdigest()
    with db() as c:
        c.executemany("INSERT INTO lab(user, jogo, concurso, estrategia, numeros, criado, hash) VALUES(?,?,?,?,?,?,?)",
                      [(user, jogo, alvo, n, json.dumps(cs), criado, selo) for n, cs in a.items()])
    out = [f"=== REGISTRADO: {jogo.upper()}, concurso {alvo} (ainda não sorteado) ===",
           f"Registrado em {criado} | impressão digital: {selo[:16]}…", ""]
    for n, cs in a.items():
        out.append(f"  {n:<16} " + (" | ".join(" ".join(f"{x:02d}" for x in c) for c in cs[:3])
                                    + (f" … (+{len(cs) - 3} cartões)" if len(cs) > 3 else "")))
    out += ["", "Depois do sorteio, baixe os resultados e clique em 'Conferir registros'."]
    return "\n".join(out)


def conferir_registros(user):
    _sql_lab()
    rows = db().execute("SELECT jogo, concurso, estrategia, numeros, criado, hash FROM lab WHERE user=? "
                        "ORDER BY concurso, id", (user,)).fetchall()
    if not rows:
        return "Nenhum registro ainda. Use 'Registrar para o próximo sorteio'."
    feitos, pend, reg = [], [], {}
    for jogo, conc, est, nums, criado, selo in rows:
        d = db().execute("SELECT dezenas FROM draws WHERE jogo=? AND concurso=?", (jogo, conc)).fetchone()
        if not d:
            pend.append(f"  {jogo} {conc} registrado em {criado} (selo {selo[:8]}): aguardando sorteio")
            continue
        sorteio = set(json.loads(d[0]))
        hits = [len(set(c) & sorteio) for c in json.loads(nums)]
        reg.setdefault(jogo, {}).setdefault(est, []).append(hits)
        feitos.append(f"  {jogo} {conc} {est:<16} acertos {max(hits)}" + (f" (melhor de {len(hits)} cartões)"
                                                                          if len(hits) > 1 else "")
                      + f"  | registrado em {criado}, selo {selo[:8]}")
    out = ["=== REGISTROS PARA SORTEIOS FUTUROS ===", ""] + feitos[-40:]
    if pend:
        out += ["", "Pendentes:"] + sorted(set(pend))
    for jogo, r in reg.items():
        if sum(len(v) for v in r.values()) >= 5:
            out += ["", texto_resultado({**_avaliar(jogo, r), "modo": "registros conferidos"})]
    return "\n".join(out)


def minha_aposta(jogo, txt):
    nums, _ = parse_bet(jogo, txt)
    if len(nums) != JOGOS[jogo][2]:
        raise ValueError(f"Para o laboratório, informe exatamente {JOGOS[jogo][2]} números.")
    return nums
