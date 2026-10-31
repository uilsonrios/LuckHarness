"""Laboratório de estratégias: testa ideias de aposta às cegas, com os resultados reais.

Regra de ouro: cada estratégia só recebe os concursos ANTERIORES ao que está apostando. No teste cego as
apostas são gravadas e ganham uma impressão digital (SHA-256) antes de qualquer resultado ser lido; no
registro para sorteios futuros, os concursos ainda nem aconteceram.
"""
import hashlib, json, math, random
from collections import Counter
from datetime import datetime

from core import JOGOS, M, __version__, fmt_bet, versao_regra, db, hash_dados, hypergeom, last_concurso, learn, load_draws, parse_bet
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
# métrica de cada modalidade: dezenas em comum; Super Sete = colunas certas; +Milionária = números (trevos à parte);
# Dupla Sena = os dois sorteios do concurso contam
JOGOS_LAB = list(JOGOS)


def _referencia(jogo):
    """(acertos esperados por aposta, desvio, chance de prêmio, faixas usadas, rótulo) pelo acaso, por modalidade."""
    N, K, mn, fx, _ = JOGOS[jogo]
    if M[jogo].get("colunas"):   # 7 colunas independentes, 1 chance em 10 cada
        p_col = 0.1
        p_premio = sum(math.comb(7, h) * p_col ** h * (1 - p_col) ** (7 - h) for h in fx)
        return 7 * p_col, math.sqrt(7 * p_col * (1 - p_col)), p_premio, fx, "3+ colunas"
    if M[jogo].get("trevos"):   # só os números; 4+ premia com qualquer trevo
        fx = [h for h in fx if h >= 4]
    sd = math.sqrt(mn * (K / N) * (1 - K / N) * (N - mn) / (N - 1))
    p_premio = sum(hypergeom(N, K, mn, h) for h in fx)
    pmin = min(h for h in fx if h > 0)
    rot = f"{pmin}+" + (" ou 0" if 0 in fx else "") + (" números (trevos à parte)" if M[jogo].get("trevos") else "")
    return mn * K / N, sd, p_premio, fx, rot


def _acaso(jogo, rng):
    N, _, mn = JOGOS[jogo][:3]
    return [rng.randrange(10) for _ in range(7)] if M[jogo].get("colunas") else sorted(rng.sample(range(1, N + 1), mn))


def _acertos(jogo, card, sorteio):
    return sum(a == b for a, b in zip(card, sorteio)) if M[jogo].get("colunas") else len(set(card) & set(sorteio))


def _sorteios_de(jogo):
    """{concurso: [sorteios]}: a Dupla Sena tem dois por concurso."""
    out = {}
    for conc, dz, meta in db().execute("SELECT concurso, dezenas, meta FROM draws WHERE jogo=?", (jogo,)):
        d2 = json.loads(meta or "{}").get("dezenas2") if M[jogo].get("sorteios") == 2 else None
        out[conc] = [json.loads(dz)] + ([d2] if d2 else [])
    return out


def _sql_lab():
    with db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS lab(id INTEGER PRIMARY KEY, user TEXT, jogo TEXT, concurso INT,
                     estrategia TEXT, numeros TEXT, criado TEXT, hash TEXT)""")


# ---------------- estratégias (só enxergam o passado) ----------------
def _pop_passado(jogo, ate):
    """Pesos de popularidade treinados só com concursos anteriores a 'ate' (sem olhar o futuro)."""
    return o.pesos_ate(jogo, ate)


def _apostas(jogo, passado, ctx):
    """Aposta de cada estratégia para o próximo concurso, usando só 'passado' (lista de (concurso, dezenas))."""
    N, K, mn = JOGOS[jogo][:3]
    rng = ctx["rng"]
    draws = [d for _, d in passado]
    out = {}
    sel = ctx["estrategias"]
    if M[jogo].get("colunas"):   # estratégias por coluna (a posição faz parte do resultado)
        cols = [Counter(d[c] for d in draws[-50:]) for c in range(7)]
        ultimo = [{d[c]: i for i, d in enumerate(draws)} for c in range(7)]
        regras = {"ao acaso": lambda: _acaso(jogo, rng),
                  "quentes": lambda: [max(range(10), key=lambda x: (cols[c][x], -x)) for c in range(7)],
                  "frios": lambda: [min(range(10), key=lambda x: (ultimo[c].get(x, -1), x)) for c in range(7)],
                  "repetir último": lambda: list(draws[-1])}
        for nome, f in regras.items():
            if nome in sel:
                out[nome] = [f()]
        if "otimizador" in sel:
            out["otimizador"] = o.cartoes_espalhados(jogo, 10, rng=rng)
        if "minha aposta" in sel and ctx.get("minha"):
            out["minha aposta"] = [list(ctx["minha"])]
        return out
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
    """registros: {estratégia: [(acertos de cada cartão), ...]} -> tabela com z, valor-p e correção de Holm.
    Hipótese nula: a estratégia acerta em média o mesmo que uma aposta ao acaso (n*K/N por cartão). A unidade
    é o concurso: compara a média de acertos por cartão em cada concurso com o acaso."""
    esp, sd, p_premio, fx, faixa = _referencia(jogo)   # inclui o zero acerto da Lotomania
    linhas = []
    for nome, por_conc in registros.items():
        hits = [h for cartoes in por_conc for h in cartoes]
        n = len(hits)
        if not n:
            continue
        media = sum(hits) / n
        n_ef = len(por_conc)
        media_conc = [sum(c) / len(c) for c in por_conc]
        m_conc = sum(media_conc) / n_ef
        # cartões do mesmo concurso enfrentam o mesmo sorteio e podem ter dezenas em comum: o desvio vem da
        # variação real entre concursos (com poucos concursos, do acaso com cartões independentes)
        if n_ef >= 30:
            sd_conc = math.sqrt(sum((x - m_conc) ** 2 for x in media_conc) / (n_ef - 1))
        else:
            sd_conc = sd / math.sqrt(len(por_conc[0]))
        z = (m_conc - esp) / (sd_conc / math.sqrt(n_ef)) if sd_conc else 0.0
        p = math.erfc(abs(z) / math.sqrt(2))
        premios = sum(h in fx for h in hits)
        se = sd_conc / math.sqrt(n_ef) if sd_conc else 0.0
        # estabilidade temporal: diferença para o acaso em blocos de 100 concursos
        blocos = [sum(media_conc[i:i + 100]) / len(media_conc[i:i + 100]) - esp for i in range(0, n_ef, 100)]
        linhas.append({"nome": nome, "concursos": n_ef, "apostas": n, "media": media, "dif": media / esp - 1,
                       "z": z, "p": p, "premios": premios, "premios_esp": n * p_premio, "melhor": max(hits),
                       "ic": (m_conc - esp - 1.96 * se, m_conc - esp + 1.96 * se), "mde": 2.8 * se, "blocos": blocos})
    ordem = sorted(range(len(linhas)), key=lambda i: linhas[i]["p"])
    maior = 0.0
    for r, i in enumerate(ordem):   # correção de Holm: várias estratégias testadas ao mesmo tempo
        maior = max(maior, min(1.0, (len(linhas) - r) * linhas[i]["p"]))
        linhas[i]["p_ajust"] = maior
    return {"jogo": jogo, "esperado": esp, "faixa": faixa, "p_premio": p_premio, "linhas": linhas}


# ---------------- registro de experimentos ----------------
def _holm(ps):
    ordem = sorted(range(len(ps)), key=lambda i: ps[i])
    out, maior = [0.0] * len(ps), 0.0
    for r, i in enumerate(ordem):
        maior = max(maior, min(1.0, (len(ps) - r) * ps[i]))
        out[i] = maior
    return out


def _registrar(r, modo, params):
    """Grava a execução (parâmetros, versão, hash dos dados, valores-p) e recalcula a correção de Holm sobre
    TODAS as hipóteses já testadas neste jogo: repetir o teste até 'dar certo' não engana o valor-p."""
    jogo = r["jogo"]
    anteriores = [p for (res,) in db().execute("SELECT resultado FROM experimentos WHERE jogo=?", (jogo,))
                  for p in json.loads(res).get("p", [])]
    atuais = [li["p"] for li in r["linhas"]]
    aj = _holm(anteriores + atuais)[len(anteriores):]
    for li, a in zip(r["linhas"], aj):
        li["p_familia"] = a
    with db() as c:
        c.execute("INSERT INTO experimentos(quando, jogo, modo, params, versao, hash_dados, resultado) "
                  "VALUES(?,?,?,?,?,?,?)",
                  (datetime.now().isoformat(timespec="seconds"), jogo, modo, json.dumps(params, sort_keys=True),
                   __version__, hash_dados(jogo),
                   json.dumps({"p": atuais, "estrategias": [li["nome"] for li in r["linhas"]],
                               "dif": [li["media"] - r["esperado"] for li in r["linhas"]]})))
    r.update(hipoteses=len(anteriores) + len(atuais), execucoes=1 + db().execute(
        "SELECT COUNT(*) FROM experimentos WHERE jogo=?", (jogo,)).fetchone()[0] - 1, versao=__version__,
        hash_dados=hash_dados(jogo), params=params)
    return r


# ---------------- testes ----------------
def _teste_cego(jogo, escondidos=100, estrategias=None, minha=None, seed=2026):
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
            a["ao acaso"] = [_acaso(jogo, ctx["rng"])]
        apostas[conc] = a
    txt = json.dumps(apostas, sort_keys=True)
    selo = hashlib.sha256(txt.encode()).hexdigest()
    # ---- revelação: só a partir daqui os resultados são lidos ----
    assert hashlib.sha256(txt.encode()).hexdigest() == selo
    reais = _sorteios_de(jogo)
    reg = {}
    for conc, a in apostas.items():
        for nome, cartoes in a.items():
            reg.setdefault(nome, []).append([_acertos(jogo, c, s) for c in cartoes for s in reais[conc]])
    r = _avaliar(jogo, reg)
    r.update(modo=f"teste cego: {escondidos} concursos escondidos ({futuro[0][0]} a {futuro[-1][0]}), "
                  f"apostas feitas de uma vez com dados até o concurso {passado[-1][0]}", hash=selo)
    return r, {"escondidos": escondidos, "seed": seed, "estrategias": sorted(ctx["estrategias"]),
               "teste": [futuro[0][0], futuro[-1][0]], "minha": minha}


def _teste_continuo(jogo, concursos=300, estrategias=None, minha=None, seed=2026, eta=0.3):
    """Cada concurso é apostado só com os anteriores; inclui o 'aprende com erros e acertos' (Hedge), que dá
    mais peso às estratégias que vêm acertando mais."""
    if jogo not in JOGOS_LAB:
        raise ValueError(f"Laboratório disponível para: {', '.join(JOGOS_LAB)}.")
    todos = sorted(load_draws(jogo, 100000))
    if len(todos) < concursos + 200:
        raise ValueError(f"{jogo}: baixe os resultados primeiro (há {len(todos)} concursos).")
    sel = set(estrategias or ESTRATEGIAS)
    ini = len(todos) - concursos
    ctx = {"rng": random.Random(seed), "estrategias": sel, "minha": minha,
           "pop": _pop_passado(jogo, todos[ini][0]) if sel & {"menos jogados", "otimizador"} else {}}
    simples = [s for s in sel if s != "otimizador" and (s != "minha aposta" or minha)]
    peso = {s: 1.0 for s in simples}
    reg, esp = {}, _referencia(jogo)[0]
    reais = _sorteios_de(jogo)
    for t in range(ini, len(todos)):
        a = _apostas(jogo, todos[:t], ctx)
        if peso:
            escolhida = max((s for s in peso if s in a), key=lambda s: (peso[s], ctx["rng"].random()), default=None)
            if escolhida:
                a["aprende (Hedge)"] = a[escolhida]
        hits = {nome: [_acertos(jogo, c, s) for c in cartoes for s in reais[todos[t][0]]]
                for nome, cartoes in a.items()}
        for nome, h in hits.items():
            reg.setdefault(nome, []).append(h)
        for s in peso:   # aprende com o resultado
            if s in hits:
                peso[s] *= math.exp(eta * (sum(hits[s]) / len(hits[s]) - esp))
        tot = sum(peso.values()) or 1
        peso = {s: v / tot for s, v in peso.items()}
    r = _avaliar(jogo, reg)
    r.update(modo=f"teste contínuo: últimos {concursos} concursos, cada um apostado só com os anteriores",
             pesos_hedge=peso)
    return r, {"concursos": concursos, "seed": seed, "eta": eta, "estrategias": sorted(sel),
               "teste": [todos[ini][0], todos[-1][0]], "minha": minha}


def _com_sementes(fn, modo, jogo, n, estrategias, minha, seed, sementes, **kw):
    """Roda com 'sementes' sementes: a primeira é o resultado; as outras medem a estabilidade (estratégias com
    sorteio interno, como 'ao acaso' e o otimizador, mudam; as determinísticas não)."""
    r, params = fn(jogo, n, estrategias, minha, seed, **kw)
    extras = [fn(jogo, n, estrategias, minha, seed + i, **kw)[0] for i in range(1, sementes)]
    r["sementes"] = {li["nome"]: [li["media"] - r["esperado"]] + [x["media"] - r["esperado"] for e in extras
                                                                  for x in e["linhas"] if x["nome"] == li["nome"]]
                     for li in r["linhas"]}
    return _registrar(r, modo, {**params, "sementes": sementes})


def teste_cego(jogo, escondidos=100, estrategias=None, minha=None, seed=2026, sementes=3):
    """Esconde os últimos 'escondidos' concursos, aposta em todos de uma vez só com o passado, grava o hash das
    apostas e só então revela os resultados. Repete com outras sementes para medir a estabilidade."""
    return _com_sementes(_teste_cego, "cego", jogo, escondidos, estrategias, minha, seed, sementes)


def teste_continuo(jogo, concursos=300, estrategias=None, minha=None, seed=2026, eta=0.3, sementes=1):
    """Cada concurso apostado só com os anteriores, com o Hedge. Mais sementes = mais tempo (refaz tudo)."""
    return _com_sementes(_teste_continuo, "contínuo", jogo, concursos, estrategias, minha, seed, sementes, eta=eta)


def texto_resultado(r):
    """Painel de generalização: cada dimensão separada (desempenho com IC, comparação com o acaso, incerteza,
    estabilidade no tempo, integridade). Sem nota única: ela pareceria prova de previsão."""
    out = [f"=== LABORATÓRIO: {r['jogo'].upper()} ===", r["modo"]]
    if r.get("hash"):
        out.append(f"Impressão digital das apostas (gravada antes de ver os resultados): {r['hash'][:16]}…")
    out += [f"Pelo acaso: {r['esperado']:.2f} acertos por aposta; prêmio ({r['faixa']}) em {r['p_premio']:.1%} "
            "das apostas.", "",
            f"{'Estratégia':<18}{'Apostas':>8}{'Média':>8}{'vs acaso':>10}{'IC 95% (acertos)':>20}{'valor-p':>9}"
            f"{'ajust.':>8}{'Prêmios':>9}{'esperado':>10}  Veredito"]
    reais = 0
    for li in sorted(r["linhas"], key=lambda x: -x["media"]):
        pa = li.get("p_familia", li["p_ajust"])
        real = pa < 0.05
        reais += real and li["dif"] > 0
        ver = ("ACIMA do acaso" if real and li["dif"] > 0 else "abaixo do acaso" if real
               else "sem evidência de diferença")
        out.append(f"{li['nome']:<18}{li['apostas']:>8}{li['media']:>8.2f}{li['dif']:>+10.0%}"
                   f"{li['ic'][0]:>+10.3f} a {li['ic'][1]:<+7.3f}{li['p']:>9.3f}{pa:>8.3f}{li['premios']:>9}"
                   f"{li['premios_esp']:>10.1f}  {ver}")
    mde = max(li["mde"] for li in r["linhas"])
    out += ["", f"Poder: com estes concursos, só diferenças a partir de ~{mde:.3f} acerto por aposta "
                f"({mde / r['esperado']:.0%}) seriam detectadas. 'Sem evidência' não prova que é igual ao acaso."]
    multi = [li for li in r["linhas"] if len(li["blocos"]) > 1]
    if multi:
        out += ["", "Estabilidade no tempo (diferença para o acaso a cada 100 concursos):"]
        out += [f"  {li['nome']:<18}" + " ".join(f"{b:+.2f}" for b in li["blocos"]) for li in multi]
    sem = {n: v for n, v in (r.get("sementes") or {}).items() if len(v) > 1}
    if sem:
        out += ["", f"Estabilidade entre sementes ({len(next(iter(sem.values())))} sementes; diferença para o acaso):"]
        out += [f"  {n:<18}" + " ".join(f"{x:+.2f}" for x in v) + ("   (igual em todas: estratégia sem sorteio)"
                                                                   if max(v) - min(v) < 1e-12 else "")
                for n, v in sem.items()]
    elif r.get("sementes"):
        out += ["", "Estabilidade entre sementes: rodado com 1 semente (o teste cego usa 3)."]
    if r.get("pesos_hedge"):
        out += ["", "Pesos finais do 'aprende (Hedge)': " + ", ".join(
            f"{n} {v:.0%}" for n, v in sorted(r["pesos_hedge"].items(), key=lambda kv: -kv[1]))]
    out += ["", versao_regra(r["jogo"])]
    if r.get("hipoteses"):
        out += ["", f"Integridade: cada estratégia só viu concursos anteriores (verificado por teste automático); "
                    f"execução nº {r['execucoes']} neste jogo, {r['hipoteses']} hipóteses testadas no total (o "
                    f"'ajust.' corrige por todas); versão {r['versao']}, dados {r['hash_dados']}, "
                    f"semente {r['params'].get('seed')}."]
    out += ["", ("Alguma estratégia ficou acima do acaso mesmo com a correção: só vale como evidência se repetir em "
                 "concursos futuros ('Registrar para o próximo sorteio')." if reais else
                 "Nenhuma estratégia mostrou evidência de acertar mais que o acaso."),
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
    for nome, aposta in extras_acompanhamento(jogo).items():
        a[nome] = [aposta]
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


# Configuração da rede por modalidade escolhida no período de desenvolvimento da bateria 1.0 (congelada em
# experimentos/aprendizado/congelado_v1.0.json). A rede ao vivo usa exatamente esta: nada é reajustado aos
# resultados novos, que servem só para conferir as previsões.
CONFIG_CONGELADA = {"megasena": (1e-3, 32, "basico"), "lotofacil": (1e-3, 32, "basico"), "quina": (1e-2, 8, "completo"),
                    "lotomania": (3e-3, 32, "basico"), "duplasena": (3e-3, 32, "basico"),
                    "timemania": (1e-2, 8, "basico"), "diadesorte": (1e-3, 8, "completo"),
                    "maismilionaria": (3e-3, 32, "basico"), "supersete": (1e-3, 8, "basico")}
STATUS = {"rede online": "experimental", "modelo neural": "experimental", "ao acaso": "referência",
          "quentes": "referência", "frios": "referência", "repetir último": "referência",
          "menos jogados": "referência", "otimizador": "referência"}
ACOMPANHAMENTO = {("diadesorte", "rede online"): "acompanhamento especial (p = 0,03 sem correção na bateria 1.0; "
                                                 "não sobreviveu à correção para 9 loterias)",
                  ("lotofacil", "frequência histórica"): "acompanhamento especial (p = 0,003 sem correção na bateria "
                                                         "1.0; 0,13 com a correção para 45 testes)"}
STATUS["frequência histórica"] = "referência"


def painel_hoje(user):
    """Seções separadas: (2) previsões registradas antes do sorteio que ainda aguardam resultado; (3) placar só com
    concursos já sorteados, todas as estratégias (inclusive as que perdem do acaso), com intervalo de confiança."""
    _sql_lab()
    rows = db().execute("SELECT jogo, concurso, estrategia, numeros, criado, hash FROM lab WHERE user=? "
                        "ORDER BY jogo, concurso, estrategia", (user,)).fetchall()
    pendentes, conf = {}, {}
    sorteios = {}
    for jogo, conc, est, nums, criado, selo in rows:
        if jogo not in sorteios:
            sorteios[jogo] = _sorteios_de(jogo)
        if conc not in sorteios[jogo]:
            pendentes.setdefault((jogo, conc), []).append((est, json.loads(nums)[0], criado, selo))
            continue
        hits = [_acertos(jogo, c, s) for c in json.loads(nums) for s in sorteios[jogo][conc]]
        conf.setdefault((jogo, est), []).append(hits)
    out = ["=== PREVISÕES REGISTRADAS ANTES DO SORTEIO (ainda sem resultado) ===",
           "Feitas só com resultados anteriores e seladas; nenhuma é resultado confirmado.", ""]
    for (jogo, conc), regs in sorted(pendentes.items()):
        rede = next((r for r in regs if r[0] == "rede online"), None)
        out.append(f"  {NOMES_CURTOS.get(jogo, jogo):<13} concurso {conc}: {len(regs)} estratégias registradas "
                   f"(primeira em {min(r[2] for r in regs)}) — AGUARDANDO SORTEIO")
        if rede:
            out.append(f"      rede online: {fmt_bet(jogo, rede[1], [])}   (selo {rede[3][:12]}, {rede[2]})")
    if not pendentes:
        out.append("  (nenhuma pendente)")
    out += ["", "=== PLACAR: CONCURSOS JÁ SORTEADOS (todas as estratégias, inclusive as que perdem do acaso) ===",
            f"{'Loteria':<13}{'Estratégia':<16}{'Status':<14}{'Concursos':>10}{'Média':>8}{'Acaso':>8}{'Diferença':>11}"
            f"{'IC 95%':>20}"]
    for (jogo, est), regs in sorted(conf.items()):
        esp, sd = _referencia(jogo)[:2]
        medias = [sum(h) / len(h) for h in regs]
        m = sum(medias) / len(medias)
        se = sd / math.sqrt(len(medias) * len(regs[0]))
        st = ACOMPANHAMENTO.get((jogo, est), STATUS.get(est, "referência"))
        out.append(f"{NOMES_CURTOS.get(jogo, jogo):<13}{est:<16}{st.split(' (')[0]:<14}{len(regs):>10}{m:>8.2f}"
                   f"{esp:>8.2f}{m - esp:>+11.2f}{m - esp - 1.96 * se:>+10.2f} a {m - esp + 1.96 * se:<+7.2f}")
    if not conf:
        out.append("  (nenhum concurso conferido ainda)")
    out += ["", "Status: 'referência' = estratégia simples usada como régua; 'experimental' = rede ou modelo em teste; "
                "'acompanhamento especial' = " + "; ".join(f"{e} no {NOMES_CURTOS.get(j, j)}: {t}"
                                                           for (j, e), t in ACOMPANHAMENTO.items()) + ".",
            "Com poucos concursos o intervalo é largo: um resultado bom ou ruim isolado não prova nada."]
    return "\n".join(out)


NOMES_CURTOS = {j: M[j]["nome"] for j in JOGOS}


def frequencia_historica(jogo):
    """Os números que mais saíram em toda a história (Super Sete: o dígito mais frequente de cada coluna)."""
    todos = [d for _, d in load_draws(jogo, 100000)]
    N, _, mn = JOGOS[jogo][:3]
    if M[jogo].get("colunas"):
        return [max(range(10), key=lambda x: (sum(d[c] == x for d in todos), -x)) for c in range(7)]
    cont = Counter(x for d in todos for x in d)
    return sorted(sorted(range(1, N + 1), key=lambda x: (-cont[x], x))[:mn])


def extras_acompanhamento(jogo, pular=()):
    """Estratégias registradas todo dia além das do Laboratório: a rede online (versão 1.0 congelada) e a
    frequência histórica (o resultado mais forte da bateria, na Lotofácil, ainda não confirmado)."""
    out = {}
    if "frequência histórica" not in pular:
        out["frequência histórica"] = frequencia_historica(jogo)
    if "rede online" not in pular:
        rede = rede_online(jogo)
        if rede:
            out["rede online"] = rede
    return out


def rede_online(jogo):
    """Aposta da rede online (a mesma da auditoria da 2ª etapa) para o próximo concurso: ela percorre todo o
    histórico prevendo e aprendendo com cada erro, e só então prevê o concurso que ainda não aconteceu.
    None se o numpy não estiver disponível."""
    try:
        import numpy as np
        import aprendizado as ap
    except ImportError:
        return None
    s = ap.serie(jogo)
    T = len(s["Y"])
    ext = {**s, "Y": np.vstack([s["Y"], np.zeros((1, s["Y"].shape[1]), np.float32)]),
           "concursos": s["concursos"] + [s["concursos"][-1] + 1]}   # linha vazia: o concurso futuro
    for k in ("Y2", "TR"):
        if s.get(k) is not None:
            ext[k] = np.vstack([s[k], np.zeros((1, s[k].shape[1]), np.float32)])
    lr, oculta, conjunto = CONFIG_CONGELADA.get(jogo, (3e-3, 16, "completo"))   # versão 1.0 congelada
    mod = ap.Rede(ext, seed=0, lr=lr, oculta=oculta, conjunto=conjunto)
    for t in range(ext.get("ini", ap.INI), T):
        mod.atualizar(t)
    esc = ap.escolher(ext, mod.prever(T), np.random.default_rng(0))
    if M[jogo].get("colunas"):   # Super Sete: unidade c*10+d -> dígito d da coluna c
        return [int(u) % 10 for u in esc]
    return sorted(int(u) + 1 for u in esc)


def ciclo_diario(user, log=print):
    """Rotina de cada atualização: registra (com selo) as apostas de todas as estratégias, inclusive a rede
    online, para o próximo concurso de cada loteria que ainda não tem registro, e devolve o placar dos registros
    já conferidos."""
    _sql_lab()
    novos = []
    for jogo in JOGOS_LAB:
        alvo = last_concurso(jogo) + 1
        feitos = {e for (e,) in db().execute("SELECT estrategia FROM lab WHERE user=? AND jogo=? AND concurso=?",
                                              (user, jogo, alvo))}
        try:
            if alvo > 1 and not feitos:
                registrar_proximo(user, jogo, [e for e in ESTRATEGIAS if e != "minha aposta"])
                novos.append(f"{jogo} {alvo}")
            elif feitos:   # registro antigo sem as estratégias em acompanhamento: o sorteio ainda não houve
                for nome, aposta in extras_acompanhamento(jogo, pular=feitos).items():
                    criado = datetime.now().isoformat(timespec="seconds")
                    selo = hashlib.sha256(json.dumps({"jogo": jogo, "concurso": alvo, nome: aposta,
                                                      "criado": criado}, sort_keys=True).encode()).hexdigest()
                    with db() as c:
                        c.execute("INSERT INTO lab(user, jogo, concurso, estrategia, numeros, criado, hash) "
                                  "VALUES(?,?,?,?,?,?,?)", (user, jogo, alvo, nome, json.dumps([aposta]), criado, selo))
                    novos.append(f"{jogo} {alvo} ({nome})")
        except Exception as e:
            log(f"  {jogo}: registro não feito ({e})")
    return {"registrados": novos, "placar": painel_hoje(user)}


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
        hits = [_acertos(jogo, c, s) for c in json.loads(nums) for s in _sorteios_de(jogo)[conc]]
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
    if len(nums) != JOGOS[jogo][2] or any(isinstance(x, list) for x in nums):
        raise ValueError(f"Para o laboratório, informe exatamente {JOGOS[jogo][2]} números.")
    return nums
