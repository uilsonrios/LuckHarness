"""H3 — popularidade da Lotofácil: teste prospectivo pré-registrado (experimentos/criterios_h3_popularidade.md).

Antes de cada concurso t (a partir do 3819), o modelo de popularidade é treinado só com concursos anteriores a t
pelo procedimento congelado, e seus pesos são selados (hash encadeado). Depois do sorteio, o modelo selado avalia a
combinação que saiu e o resultado é comparado com os ganhadores oficiais de 14 e de 15 acertos. O modelo nunca
aprende com o concurso que avalia. Isto não prevê o sorteio (H1): mede o comportamento dos apostadores.
"""
import ast, hashlib, json, math, os, sys
from datetime import datetime

import core
import otimizador as o
from core import JOGOS, M, db

JOGO = "lotofacil"
PRIMEIRO, HORIZONTE = 3819, 150
CFG = {"nome": "h3-congelado", "modelo": "media", "alvo": "multi", "padroes": True, "meia_vida": 500,
       "janela": 1000, "lambda": 3.0}
NIVEL = 100                       # concursos anteriores usados para os ganhadores esperados pelas vendas
PERMUTACOES, SEMENTE = 100_000, 3819
FAIXAS = (14, 15)
CONGELADAS = {"otimizador": ("_amostras", "_faixas_alvo", "_p_trevos", "_padroes", "PADROES", "_ativos", "_base",
                             "_entradas", "_pesos_amostra", "_sistema", "_ridge", "_resolver", "_mlp", "_saida",
                             "_ajusta", "_prever_lote", "_prever"),
              "core": ("hypergeom",)}
AQUI = os.path.dirname(os.path.abspath(__file__))
PASTA = os.path.join(core.DATA, "h3")
REGISTRO = os.path.join(PASTA, "registro.jsonl")


def _sha(b):
    return hashlib.sha256(b if isinstance(b, bytes) else b.encode()).hexdigest()


def _fonte(nome):
    """Texto-fonte de um módulo: ao lado deste arquivo ou, no executável, na cópia empacotada em 'fontes'."""
    for base in (AQUI, os.path.join(getattr(sys, "_MEIPASS", AQUI), "fontes")):
        p = os.path.join(base, nome + ".py")
        if os.path.exists(p):
            with open(p, encoding="utf8") as f:
                return f.read()
    raise OSError(f"fonte de {nome}.py não encontrada")


def _criterios():
    for base in (AQUI, getattr(sys, "_MEIPASS", AQUI)):
        p = os.path.join(base, "experimentos", "criterios_h3_popularidade.md")
        if os.path.exists(p):
            with open(p, "rb") as f:
                return f.read()
    raise OSError("pré-registro do H3 não encontrado")


def codigo_sha():
    """Hash do procedimento congelado: as funções do modelo (texto exato), as regras da Lotofácil que elas usam
    e este arquivo inteiro (avaliação e estatística)."""
    partes = []
    for mod, nomes in CONGELADAS.items():
        src = _fonte(mod)
        arv = {(n.name if isinstance(n, ast.FunctionDef) else n.targets[0].id): n for n in ast.parse(src).body
               if isinstance(n, ast.FunctionDef) or (isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name))}
        partes += [ast.get_source_segment(src, arv[n]) for n in nomes]
    partes.append(json.dumps([JOGOS[JOGO], o.VOLANTE.get(JOGO), M[JOGO]], sort_keys=True, default=str))
    partes.append(_fonte("popularidade_h3"))
    return _sha("\n\x00\n".join(partes))


def verificar_codigo():
    """O código atual tem de ser o pré-registrado; senão nada é selado (exige emenda registrada)."""
    txt = _criterios().decode("utf8")
    reg = txt.split("Código congelado (SHA-256): `")[1].split("`")[0]
    atual = codigo_sha()
    if atual != reg:
        raise RuntimeError(f"H3: código alterado depois do pré-registro ({atual[:12]} ≠ {reg[:12]}); nada foi selado")
    return atual


def _registros():
    if not os.path.exists(REGISTRO):
        return []
    with open(REGISTRO, encoding="utf8") as f:
        return [json.loads(x) for x in f if x.strip()]


def verificar_cadeia():
    anterior = "0" * 64
    for r in _registros():
        r = dict(r)
        h = r.pop("hash")
        r.pop("quando", None)
        if r["anterior"] != anterior or _sha(json.dumps(r, sort_keys=True)) != h:
            return False
        anterior = h
    return True


def _modelo_arquivo(t):
    return os.path.join(PASTA, f"modelo_{t}.json")


def selar(log=print):
    """Treina com os concursos anteriores ao próximo e sela o modelo. Devolve o concurso selado (ou None)."""
    ultimo = core.last_concurso(JOGO)
    t = ultimo + 1
    if t < PRIMEIRO or any(r["concurso"] == t for r in _registros()):
        return None
    codigo = verificar_codigo()
    treino = [a for a in o._amostras(JOGO) if a["concurso"] < t]
    mod = o._ajusta(JOGO, treino, CFG)
    corpo = json.dumps(mod, sort_keys=True)
    os.makedirs(PASTA, exist_ok=True)
    with open(_modelo_arquivo(t), "w", encoding="utf8") as f:
        f.write(corpo)
    regs = _registros()
    reg = {"concurso": t, "treinado_ate": treino[-1]["concurso"], "amostras": len(treino),
           "dados": _sha(json.dumps([(a["concurso"], a["dz"], a["y_multi"]) for a in treino]))[:16],
           "modelo": _sha(corpo), "codigo": codigo[:16], "criterios": _sha(_criterios())[:16],
           "anterior": regs[-1]["hash"] if regs else "0" * 64}
    h = _sha(json.dumps(reg, sort_keys=True))
    with open(REGISTRO, "a", encoding="utf8") as f:
        f.write(json.dumps({**reg, "hash": h, "quando": datetime.now().isoformat(timespec="seconds")}) + "\n")
    log(f"  H3: modelo de popularidade selado para o concurso {t} (selo {h[:12]})")
    return t


def _ganhadores(meta):
    d = {}
    for (desc, _), g in zip(meta.get("premios", []), meta.get("ganhadores", [])):
        k = desc.split()[0]
        if k.isdigit():
            d[int(k)] = g
    return d


def avaliar():
    """Concursos selados que já têm resultado oficial. Nenhum é excluído pelo resultado observado; só por falta
    de dado oficial (vendas ou ganhadores), sem olhar o número de ganhadores."""
    rows = db().execute("SELECT concurso, dezenas, meta FROM draws WHERE jogo=? ORDER BY concurso",
                        (JOGO,)).fetchall()
    dz = {c: json.loads(d) for c, d, _ in rows}
    info = {}
    for c, _, meta in rows:
        m = json.loads(meta) if meta else {}
        g = _ganhadores(m)
        if m.get("arrecadado") and all(h in g for h in FAIXAS):
            info[c] = (g, float(m["arrecadado"]))
    linhas, faltas = [], []
    for r in _registros():
        t = r["concurso"]
        if t not in dz:
            continue                                  # ainda não sorteado
        anteriores = [c for c in sorted(info) if c < t][-NIVEL:]
        if t not in info or t - 1 not in dz or len(anteriores) < NIVEL:
            faltas.append(t)
            continue
        with open(_modelo_arquivo(t), encoding="utf8") as f:
            corpo = f.read()
        if _sha(corpo) != r["modelo"]:
            raise RuntimeError(f"H3: modelo do concurso {t} não confere com o selo")
        mod = json.loads(corpo)
        f = o._prever(JOGO, mod, {"dz": dz[t], "ant": dz[t - 1], "trevos": []})
        g, arrec = info[t]
        lin = {"concurso": t, "fator": math.exp(f), "log_fator": f}
        for h in FAIXAS:
            nivel = sum(info[c][0][h] for c in anteriores) / sum(info[c][1] for c in anteriores)
            esp = arrec * nivel
            lin[f"g{h}"], lin[f"e{h}"], lin[f"r{h}"] = g[h], esp, g[h] / esp
        linhas.append(lin)
    return {"linhas": linhas[:HORIZONTE], "faltas": faltas, "completo": len(linhas) >= HORIZONTE}


def _postos(x):
    """Postos médios (empates, como os zeros de R15, recebem a média dos postos)."""
    ordem = sorted(range(len(x)), key=lambda i: x[i])
    p = [0.0] * len(x)
    i = 0
    while i < len(ordem):
        j = i
        while j + 1 < len(ordem) and x[ordem[j + 1]] == x[ordem[i]]:
            j += 1
        for k in range(i, j + 1):
            p[ordem[k]] = (i + j) / 2 + 1
        i = j + 1
    return p


def spearman(a, b):
    ra, rb = _postos(a), _postos(b)
    n = len(a)
    ma, mb = sum(ra) / n, sum(rb) / n
    sab = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    saa, sbb = sum((x - ma) ** 2 for x in ra), sum((y - mb) ** 2 for y in rb)
    return sab / math.sqrt(saa * sbb) if saa and sbb else 0.0


def razao_extremos(f, g, e):
    """(ganhadores/esperados nos 20% mais populares) / (o mesmo nos 20% menos populares), pelo fator previsto."""
    n = len(f)
    k = n // 5
    ordem = sorted(range(n), key=lambda i: (f[i], i))
    lo, hi = ordem[:k], ordem[-k:]
    r_lo = sum(g[i] for i in lo) / sum(e[i] for i in lo)
    r_hi = sum(g[i] for i in hi) / sum(e[i] for i in hi)
    return r_hi / r_lo if r_lo else math.inf


def _permutacoes(n, B, semente, lote=5000):
    """Permutações do fator previsto entre os concursos (sob H0 o fator não tem relação com os ganhadores)."""
    import numpy as np
    rng = np.random.default_rng(semente)
    feitas = 0
    while feitas < B:
        m = min(lote, B - feitas)
        yield np.argsort(rng.random((m, n)), axis=1)
        feitas += m


def p_spearman(f, r, B=PERMUTACOES, semente=SEMENTE):
    """ρ de Spearman e p unilateral (H1: ρ > 0) por permutação: (1 + nº de permutações com ρ >= observado) / (B + 1)."""
    import numpy as np
    ra, rb = np.array(_postos(f)), np.array(_postos(r))
    ra, rb = ra - ra.mean(), rb - rb.mean()
    den = math.sqrt(float((ra ** 2).sum() * (rb ** 2).sum())) or 1.0
    obs = float(ra @ rb) / den
    maior = sum(int(((ra[P] @ rb) / den >= obs - 1e-12).sum()) for P in _permutacoes(len(f), B, semente))
    return obs, (1 + maior) / (B + 1)


def p_extremos(f, g, e, B=PERMUTACOES, semente=SEMENTE):
    """Razão 20% mais populares / 20% menos populares e p unilateral (H1: razão > 1) por permutação."""
    import numpy as np
    obs = razao_extremos(f, g, e)
    g, e = np.array(g, float), np.array(e, float)
    k = len(f) // 5
    maior = 0
    for P in _permutacoes(len(f), B, semente):
        lo, hi = P[:, :k], P[:, -k:]
        r_lo = g[lo].sum(1) / e[lo].sum(1)
        q = np.where(r_lo > 0, (g[hi].sum(1) / e[hi].sum(1)) / np.where(r_lo > 0, r_lo, 1), np.inf)
        maior += int((q >= obs - 1e-12).sum())
    return obs, (1 + maior) / (B + 1)


def _holm(ps):
    ordem = sorted(range(len(ps)), key=lambda i: ps[i])
    out, acum = [0.0] * len(ps), 0.0
    for k, i in enumerate(ordem):
        acum = max(acum, min(1.0, (len(ps) - k) * ps[i]))
        out[i] = acum
    return out


def teste(linhas, B=PERMUTACOES):
    """Teste confirmatório (só com os 150 concursos completos)."""
    f = [x["log_fator"] for x in linhas]
    r14, r15 = [x["r14"] for x in linhas], [x["r15"] for x in linhas]
    rho14, p14 = p_spearman(f, r14, B=B)
    rho15, p15 = p_spearman(f, r15, B=B)
    g14, e14 = [x["g14"] for x in linhas], [x["e14"] for x in linhas]
    g15, e15 = [x["g15"] for x in linhas], [x["e15"] for x in linhas]
    q14, pq14 = p_extremos(f, g14, e14, B=B)
    q15, pq15 = p_extremos(f, g15, e15, B=B)
    sec = _holm([p15, pq14, pq15])
    return {"n": len(linhas), "primaria": {"rho14": rho14, "p": p14, "confirmada": p14 < 0.05},
            "secundarias": {"rho15": (rho15, p15, sec[0]), "extremos14": (q14, pq14, sec[1]),
                            "extremos15": (q15, pq15, sec[2])},
            "calibracao": calibracao(linhas)}


def calibracao(linhas):
    """H3b, só descritiva: inclinação de log(real/esperado) sobre log(fator) nos 14 acertos e por quintil."""
    f = [x["log_fator"] for x in linhas]
    y = [math.log((x["g14"] + 0.5) / (x["e14"] + 0.5)) for x in linhas]
    mf, my = sum(f) / len(f), sum(y) / len(y)
    sff = sum((a - mf) ** 2 for a in f)
    incl = sum((a - mf) * (b - my) for a, b in zip(f, y)) / sff if sff else 0.0
    ordem = sorted(range(len(f)), key=lambda i: (f[i], i))
    k = max(1, len(f) // 5)
    quint = []
    for q in range(5):
        idx = ordem[q * k:(q + 1) * k] if q < 4 else ordem[4 * k:]
        if idx:
            quint.append({"previsto": sum(linhas[i]["fator"] for i in idx) / len(idx),
                          "real14": sum(linhas[i]["g14"] for i in idx) / sum(linhas[i]["e14"] for i in idx),
                          "real15": sum(linhas[i]["g15"] for i in idx) / sum(linhas[i]["e15"] for i in idx)})
    return {"inclinacao14": incl, "quintis": quint}


def texto():
    """Resumo para a aba Hoje. Antes do 150º concurso é só acompanhamento: nenhum teste é feito nem decide."""
    try:
        r = avaliar()
    except Exception as e:
        return f"H3 (popularidade, Lotofácil): indisponível ({e})"
    regs = _registros()
    n = len(r["linhas"])
    out = ["=== H3: POPULARIDADE DA LOTOFÁCIL (teste prospectivo pré-registrado) ===",
           "Não prevê o sorteio: mede se o modelo congelado sabe quais resultados, caso saiam, terão mais ou menos "
           "ganhadores de 14 e 15 pontos.",
           f"Selados: {len(regs)} concurso(s) (último {regs[-1]['concurso'] if regs else '-'}); cadeia "
           f"{'íntegra' if verificar_cadeia() else 'ALTERADA'}. Avaliados: {n}/{HORIZONTE}."]
    if r["faltas"]:
        out.append(f"Sem dado oficial (fora da conta, sem olhar ganhadores): {', '.join(map(str, r['faltas']))}")
    if r["completo"]:
        t = teste(r["linhas"])
        p = t["primaria"]
        out.append(f"TESTE CONFIRMATÓRIO: ρ(14 pontos) = {p['rho14']:+.3f}, p = {p['p']:.4f} → "
                   + ("H3a CONFIRMADA" if p["confirmada"] else "H3a NÃO confirmada"))
    elif n >= 3:
        f = [x["log_fator"] for x in r["linhas"]]
        out.append(f"Acompanhamento (não é o teste; nada é decidido antes do {HORIZONTE}º): "
                   f"ρ14 = {spearman(f, [x['r14'] for x in r['linhas']]):+.2f}, "
                   f"ρ15 = {spearman(f, [x['r15'] for x in r['linhas']]):+.2f}")
    return "\n".join(out)
