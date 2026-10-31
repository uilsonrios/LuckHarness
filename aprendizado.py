"""Bateria de aprendizado sequencial — Auditoria da Rede Neural, 2ª etapa (ferramenta de pesquisa; requer numpy).

Protocolo: cada concurso é previsto só com os anteriores; a previsão é gravada num registro encadeado por hash
ANTES de o resultado ser lido; depois vêm as métricas e a atualização dos modelos. Todos os modelos recebem
exatamente a mesma informação. Critérios de aprovação: experimentos/criterios_aprendizado.md (pré-registrados).

Uso: python aprendizado.py bateria   (roda tudo e grava o resumo em experimentos/aprendizado/)
"""
import hashlib, json, math, os, time
import numpy as np

VERSAO = "aprendizado-1.0"
AQUI = os.path.dirname(os.path.abspath(__file__))
SAIDA = os.path.join(AQUI, "experimentos", "aprendizado")
CRITERIOS = os.path.join(AQUI, "experimentos", "criterios_aprendizado.md")
INI, AQUECIMENTO = 200, 500        # 200 concursos só de histórico; 500 de aquecimento (só aprende)
JANELAS = (5, 10, 20, 50, 100, 200)


# =============================== dados por modalidade ===============================
def serie(jogo):
    """Resultados na representação da modalidade:
    Y[t, u] = 1 se a 'unidade' u saiu no concurso t; Y2 = 2º sorteio (Dupla Sena); 'grupos' = colunas da Super
    Sete (escolhe-se 1 unidade por grupo); 'escolhe' = números da aposta mínima; T2 = trevos (+Milionária)."""
    import core
    m = core.M[jogo]
    rows = core.db().execute("SELECT concurso, dezenas, meta FROM draws WHERE jogo=? ORDER BY concurso",
                             (jogo,)).fetchall()
    T = len(rows)
    if m.get("colunas"):
        Y = np.zeros((T, 70), np.float32)
        for i, (_, d, _) in enumerate(rows):
            for c, x in enumerate(json.loads(d)):
                Y[i, c * 10 + x] = 1
        return _curta({"jogo": jogo, "Y": Y, "grupos": [list(range(c * 10, c * 10 + 10)) for c in range(7)],
                       "escolhe": 7, "sorteados": 7, "concursos": [r[0] for r in rows]})
    N = m["N"]
    Y = np.zeros((T, N), np.float32)
    Y2 = np.zeros((T, N), np.float32) if m.get("sorteios") == 2 else None
    TR = np.zeros((T, 6), np.float32) if m.get("trevos") else None
    for i, (_, d, meta) in enumerate(rows):
        Y[i, np.array(json.loads(d)) - 1] = 1
        mt = json.loads(meta or "{}")
        if Y2 is not None and mt.get("dezenas2"):
            Y2[i, np.array(mt["dezenas2"]) - 1] = 1
        if TR is not None and mt.get("trevos"):
            TR[i, np.array(mt["trevos"]) - 1] = 1
    return _curta({"jogo": jogo, "Y": Y, "Y2": Y2, "TR": TR, "escolhe": m["escolha"][0], "sorteados": m["sorteados"],
                   "concursos": [r[0] for r in rows]})


def _curta(s):
    """Emenda 1 dos critérios: séries com menos de 1.400 concursos usam 50 de histórico e 100 de aquecimento."""
    if len(s["Y"]) < 1400:
        s["ini"], s["aq"] = 50, 100
    return s


def serie_trevos(s):
    """+Milionária: os trevos (2 de 6) como série própria (secundária)."""
    return _curta({"jogo": "maismilionaria-trevos", "Y": s["TR"], "Y2": None, "TR": None, "escolhe": 2,
                   "sorteados": 2, "concursos": s["concursos"]})


def sintetica(tipo, forca=1.0, seed=0, T=3200, N=25, K=15, escolhe=15):
    """Loterias artificiais. tipo: 'nulo' (acaso puro), 'repetir' (quem saiu volta mais: padrão INFORMADO, pois a
    rede recebe 'saiu no anterior'), 'vizinho' (quem é vizinho de um sorteado volta mais: NÃO informado
    diretamente), 'periodico' (um terço dos números volta a cada 9 concursos), 'deriva' (popularidade que muda
    devagar), 'frio' (quem saiu tende a não voltar)."""
    r = np.random.default_rng(seed)
    Y = np.zeros((T, N), np.float32)
    deriva = r.normal(0, 1, N)
    alvo_per = r.choice(N, N // 3, replace=False)
    for t in range(T):
        lw = np.zeros(N)
        if t and tipo == "repetir":
            lw += math.log(1 + 0.5 * forca) * Y[t - 1]
        if t and tipo == "frio":
            lw -= math.log(1 + 0.5 * forca) * Y[t - 1]
        if t and tipo == "vizinho":
            viz = np.roll(Y[t - 1], 1) + np.roll(Y[t - 1], -1)
            lw += math.log(1 + 0.4 * forca) * (viz > 0)
        if tipo == "periodico" and t % 9 == 0:
            lw[alvo_per] += math.log(1 + 1.0 * forca)
        if tipo == "deriva":
            deriva += r.normal(0, 0.05, N)
            lw += 0.35 * forca * deriva
        g = lw - np.log(-np.log(r.random(N)))          # Gumbel top-K = sorteio ponderado sem reposição
        Y[t, np.argsort(-g)[:K]] = 1
    return {"jogo": f"sintetica:{tipo}:{forca}:{seed}", "Y": Y, "Y2": None, "TR": None, "escolhe": escolhe,
            "sorteados": K, "concursos": list(range(1, T + 1))}


# =============================== atributos (só o passado) ===============================
def atributos(Y, conjunto="completo", ini=INI):
    """A[t, u, :] usa apenas os concursos < t. Valores já em escala fixa (0 a 1 ou desvio da taxa base): nenhuma
    estatística do histórico inteiro é usada (corrige o vazamento leve da versão anterior)."""
    T, U = Y.shape
    base = Y[:ini].mean() if T > ini else Y.mean()
    C = np.vstack([np.zeros((1, U), np.float32), np.cumsum(Y, 0)])
    atr, a = np.zeros((T, U), np.float32), np.full(U, 100.0)
    for t in range(T):
        atr[t] = np.minimum(a, 100) / 100
        a = np.where(Y[t] > 0, 1, a + 1)
    blocos = []
    for k in (1, 2, 3):
        prev = np.zeros((T, U), np.float32)
        prev[k:] = Y[:-k]
        blocos.append(prev)
    if conjunto == "completo":
        t_idx = np.arange(T)[:, None]
        for w in JANELAS:
            n = np.minimum(t_idx, w).clip(1)
            blocos.append((C[np.arange(T)] - C[np.maximum(np.arange(T) - w, 0)]) / n - base)
        blocos.append(C[np.arange(T)] / np.arange(T).clip(1)[:, None] - base)
    blocos.append(atr)
    return np.stack(blocos, -1).astype(np.float32)


# =============================== modelos (todos online) ===============================
class Modelo:
    """prever(t) -> probabilidade de cada unidade sair em t; atualizar(t) depois do resultado revelado."""
    def __init__(self, s, seed=0):
        self.s, self.Y, self.rng = s, s["Y"], np.random.default_rng(seed)
        self.Y2 = s.get("Y2")    # Dupla Sena: todos os modelos recebem também o 2º sorteio
        self.p0 = 0.1 if s.get("grupos") else s["sorteados"] / self.Y.shape[1]

    def estado(self):
        return b""

    def atualizar(self, t):
        pass


class Aleatorio(Modelo):
    nome = "aleatório uniforme"

    def prever(self, t):
        return np.full(self.Y.shape[1], self.p0) + self.rng.random(self.Y.shape[1]) * 1e-9


class FreqHist(Modelo):
    nome = "frequência histórica"

    def __init__(self, s, seed=0):
        super().__init__(s, seed)
        self.c, self.n = np.zeros(self.Y.shape[1]), 0

    def prever(self, t):
        return (self.c + self.p0 * 2) / (self.n + 2) + self.rng.random(len(self.c)) * 1e-9

    def atualizar(self, t):
        self.c += self.Y[t]
        self.n += 1
        if self.s.get("Y2") is not None and self.s["Y2"][t].any():
            self.c += self.s["Y2"][t]
            self.n += 1

    def estado(self):
        return self.c.tobytes()


class FreqJanela(FreqHist):
    nome = "frequência janela 50"

    def prever(self, t):
        a = max(0, t - 50)
        c, n = self.Y[a:t].sum(0), t - a
        if self.Y2 is not None:
            c, n = c + self.Y2[a:t].sum(0), 2 * n
        return (c + self.p0 * 2) / (n + 2) + self.rng.random(len(c)) * 1e-9


class FreqExp(FreqHist):
    nome = "frequência ponderada (meia-vida 100)"

    def __init__(self, s, seed=0):
        super().__init__(s, seed)
        self.d = 0.5 ** (1 / 100)
        self.c, self.n = np.full(self.Y.shape[1], self.p0), 1.0

    def prever(self, t):
        return self.c / self.n + self.rng.random(len(self.c)) * 1e-9

    def atualizar(self, t):
        dois = self.Y2 is not None and self.Y2[t].any()
        self.c = self.c * self.d + self.Y[t] + (self.Y2[t] if dois else 0)
        self.n = self.n * self.d + (2 if dois else 1)


class Markov(Modelo):
    """Modelo estatístico simples: P(sair | saiu ou não no anterior), por unidade, com prior Beta(1,1)."""
    nome = "Markov por número"

    def __init__(self, s, seed=0):
        super().__init__(s, seed)
        U = self.Y.shape[1]
        self.k = np.ones((2, U))           # sucessos por estado anterior
        self.n = np.full((2, U), 2.0)

    def prever(self, t):
        st = (self.Y[t - 1] > 0).astype(int) if t else np.zeros(self.Y.shape[1], int)
        return self.k[st, np.arange(len(st))] / self.n[st, np.arange(len(st))] + self.rng.random(len(st)) * 1e-9

    def atualizar(self, t):
        if t:
            st = (self.Y[t - 1] > 0).astype(int)
            self.k[st, np.arange(len(st))] += self.Y[t]
            self.n[st, np.arange(len(st))] += 1

    def estado(self):
        return self.k.tobytes()


class Distribuicao(Modelo):
    """Modelo pela distribuição das dezenas: frequência de cada faixa (dezena) e paridade nos sorteios passados."""
    nome = "distribuição (faixa x paridade)"

    def __init__(self, s, seed=0):
        super().__init__(s, seed)
        U = self.Y.shape[1]
        self.cls = (np.arange(U) // 10) * 2 + (np.arange(U) % 2)
        self.c = np.ones(self.cls.max() + 1)

    def prever(self, t):
        f = self.c[self.cls] / np.bincount(self.cls)[self.cls]
        return self.p0 * f / f.mean() + self.rng.random(len(f)) * 1e-9

    def atualizar(self, t):
        np.add.at(self.c, self.cls, self.Y[t])
        if self.Y2 is not None:
            np.add.at(self.c, self.cls, self.Y2[t])

    def estado(self):
        return self.c.tobytes()


class Rede(Modelo):
    """Rede com pesos compartilhados entre os números (mesma MLP para cada unidade). Prevê, e só depois do
    resultado atualiza com o concurso novo + 'rev' lotes de revisão do passado (contra esquecimento)."""
    nome = "rede neural (MLP compartilhada)"

    def __init__(self, s, seed=0, lr=3e-3, oculta=16, conjunto="completo", rev=2, A=None):
        super().__init__(s, seed)
        self.A = A if A is not None else atributos(self.Y, conjunto, s.get("ini", INI))
        Fd = self.A.shape[2]
        r = self.rng
        self.lr, self.rev = lr, rev
        self.W1 = r.normal(0, math.sqrt(2 / Fd), (Fd, oculta)).astype(np.float32)
        self.b1 = np.zeros(oculta, np.float32)
        self.W2 = np.zeros((oculta, 1), np.float32)
        self.b2 = np.full(1, math.log(self.p0 / (1 - self.p0)), np.float32)
        self.P = [self.W1, self.b1, self.W2, self.b2]
        self.m = [np.zeros_like(p) for p in self.P]
        self.v = [np.zeros_like(p) for p in self.P]
        self.passo = 0
        self.config = {"lr": lr, "oculta": oculta, "conjunto": conjunto, "rev": rev}

    def _frente(self, x):
        h = np.maximum(x @ self.W1 + self.b1, 0)
        return h, 1 / (1 + np.exp(-(h @ self.W2 + self.b2)[..., 0]))

    def prever(self, t):
        return self._frente(self.A[t])[1]

    def _passo(self, xb, yb, l2=1e-4):
        h, p = self._frente(xb)
        g = ((p - yb) / len(xb))[:, None]
        gh = (g @ self.W2.T) * (h > 0)
        grads = [xb.T @ gh + l2 * self.W1, gh.sum(0), h.T @ g + l2 * self.W2, g.sum(0)]
        self.passo += 1
        for i, (prm, gr) in enumerate(zip(self.P, grads)):
            self.m[i] = 0.9 * self.m[i] + 0.1 * gr
            self.v[i] = 0.999 * self.v[i] + 0.001 * gr * gr
            prm -= self.lr * (self.m[i] / (1 - 0.9 ** self.passo)) / (np.sqrt(self.v[i] / (1 - 0.999 ** self.passo))
                                                                       + 1e-8)

    def atualizar(self, t):
        Y2 = self.s.get("Y2")
        for _ in range(self.rev):
            conc = np.concatenate([[t], self.rng.integers(0, t + 1, 7)])
            xs, ys = [self.A[conc].reshape(-1, self.A.shape[2])], [self.Y[conc].reshape(-1)]
            if Y2 is not None:   # Dupla Sena: o 2º sorteio é outro exemplo com os mesmos atributos
                xs.append(self.A[conc].reshape(-1, self.A.shape[2]))
                ys.append(Y2[conc].reshape(-1))
            self._passo(np.concatenate(xs), np.concatenate(ys))

    def estado(self):
        return b"".join(p.tobytes() for p in self.P)


REFERENCIAS = [Aleatorio, FreqHist, FreqJanela, FreqExp, Markov, Distribuicao]


# =============================== escolha, acertos e métricas ===============================
def escolher(s, p, rng):
    if s.get("grupos"):
        return [g[int(np.argmax(p[g] + rng.random(len(g)) * 1e-12))] for g in s["grupos"]]
    return list(np.argsort(-(p + rng.random(len(p)) * 1e-12))[:s["escolhe"]])


def acertos(s, esc, t):
    h = float(s["Y"][t, esc].sum())
    if s.get("Y2") is not None and s["Y2"][t].any():
        h = (h + float(s["Y2"][t, esc].sum())) / 2      # média dos dois sorteios
    return h


def referencia(s):
    """(acertos esperados, variância por concurso) ao acaso, pela regra da modalidade."""
    if s.get("grupos"):
        return 0.7, 7 * 0.1 * 0.9
    U, K, n = s["Y"].shape[1], s["sorteados"], s["escolhe"]
    esp = n * K / U
    var = n * (K / U) * (1 - K / U) * (U - n) / (U - 1)
    return esp, var / (2 if s.get("Y2") is not None else 1)


def perda_brier(s, p, t):
    """Log loss e Brier por unidade (Super Sete: probabilidades normalizadas por coluna)."""
    y = s["Y"][t]
    if s.get("grupos"):
        p = p.copy()
        for g in s["grupos"]:
            p[g] = p[g] / p[g].sum()
    pc = np.clip(p, 1e-6, 1 - 1e-6)
    return float(-(y * np.log(pc) + (1 - y) * np.log(1 - pc)).mean()), float(((p - y) ** 2).mean())


def _h(b):
    return hashlib.sha256(b).hexdigest()


# =============================== Experimento A: aprendizado sequencial auditável ===============================
def rodar(s, fabrica, seed=0, registro=None, ate=None):
    """Prevê -> grava (hash encadeado) -> revela -> métricas -> atualiza. Devolve arrays a partir do aquecimento."""
    mod = fabrica(s, seed)
    rng = np.random.default_rng(seed + 991)
    T = len(s["Y"]) if ate is None else ate
    hs, perdas, briers = [], [], []
    anterior = "0" * 64
    arq = open(registro, "w", encoding="utf8") if registro else None
    crit = _h(open(CRITERIOS, "rb").read()) if os.path.exists(CRITERIOS) else None
    try:
        ini, aq = s.get("ini", INI), s.get("aq", AQUECIMENTO)
        for t in range(ini, T):
            if t >= ini + aq:
                antes = _h(mod.estado())
                p = mod.prever(t)
                esc = escolher(s, p, rng)
                # previsão encadeada por hash (conteúdo, sem o horário) e gravada antes de ler o resultado
                reg = {"concurso": s["concursos"][t], "historico_ate": s["concursos"][t - 1],
                       "dados": _h(s["Y"][:t].tobytes())[:16], "previsao": [int(x) for x in esc], "modelo": mod.nome,
                       "versao": VERSAO, "semente": seed, "config": getattr(mod, "config", {}),
                       "pesos_antes": antes[:16], "criterios": crit[:16] if crit else None, "anterior": anterior}
                anterior = _h(json.dumps(reg, sort_keys=True).encode())
                if arq:
                    arq.write(json.dumps({**reg, "hash": anterior, "quando": time.strftime("%Y-%m-%dT%H:%M:%S")})
                              + "\n")
                h = acertos(s, esc, t)
                lp, bs = perda_brier(s, p, t)
                hs.append(h); perdas.append(lp); briers.append(bs)
                if arq:
                    arq.write(json.dumps({"concurso": s["concursos"][t], "resultado": [int(x) for x in np.flatnonzero(
                        s["Y"][t])], "acertos": h, "log_loss": lp, "brier": bs}) + "\n")
            mod.atualizar(t)
            if arq and t >= ini + aq:
                arq.write(json.dumps({"concurso": s["concursos"][t], "pesos_depois": _h(mod.estado())[:16]}) + "\n")
    finally:
        if arq:
            arq.close()
    return {"acertos": np.array(hs), "log_loss": np.array(perdas), "brier": np.array(briers), "cadeia": anterior,
            "modelo": mod}


def verificar_cadeia(caminho):
    """Recalcula o encadeamento: qualquer previsão alterada depois muda os hashes seguintes."""
    anterior = "0" * 64
    with open(caminho, encoding="utf8") as f:
        for linha in f:
            reg = json.loads(linha)
            if "previsao" not in reg:
                continue
            h = reg.pop("hash")
            reg.pop("quando", None)
            if reg["anterior"] != anterior or _h(json.dumps(reg, sort_keys=True).encode()) != h:
                return False
            anterior = h
    return True


# =============================== estatística ===============================
def resumo(s, hs_por_semente, inicio, fim):
    """Diferença para o acaso no período [inicio, fim) (índices relativos ao fim do aquecimento), com a média das
    sementes por concurso como unidade (as sementes usam os mesmos sorteios: não são testes independentes)."""
    esp, var = referencia(s)
    H = np.array([h[inicio:fim] for h in hs_por_semente])
    media_conc = H.mean(0)
    n = len(media_conc)
    exc = media_conc - esp
    se = max(exc.std(ddof=1), 1e-12) / math.sqrt(n)
    z = exc.mean() / se
    sementes = H.mean(1) - esp
    blocos = [float(exc[i:i + 250].mean()) for i in range(0, n, 250)]
    tt = np.arange(n) - (n - 1) / 2
    inclin = float((tt * exc).sum() / (tt * tt).sum())
    se_incl = float(exc.std(ddof=1) / math.sqrt((tt * tt).sum()))
    return {"n": n, "esperado": esp, "media": float(media_conc.mean()), "dif": float(exc.mean()),
            "ic95": [float(exc.mean() - 1.96 * se), float(exc.mean() + 1.96 * se)], "z": float(z),
            "p_unilateral": float(0.5 * math.erfc(z / math.sqrt(2))), "efeito_d": float(exc.mean() / math.sqrt(var)),
            "sementes_positivas": float((sementes > 0).mean()), "dif_sementes": [float(x) for x in sementes],
            "blocos": blocos, "blocos_positivos": float(np.mean([b > 0 for b in blocos])),
            "tendencia_por_1000": inclin * 1000, "tendencia_z": inclin / se_incl if se_incl else 0.0}


def comparar(a, b):
    """Diferença pareada por concurso (a - b), com desvio empírico."""
    d = a - b
    se = max(d.std(ddof=1), 1e-12) / math.sqrt(len(d))
    z = d.mean() / se
    return {"dif": float(d.mean()), "ic95": [float(d.mean() - 1.96 * se), float(d.mean() + 1.96 * se)], "z": float(z),
            "p_unilateral": float(0.5 * math.erfc(z / math.sqrt(2)))}


def holm(ps):
    ordem = sorted(range(len(ps)), key=lambda i: ps[i])
    out, maior = [0.0] * len(ps), 0.0
    for r, i in enumerate(ordem):
        maior = max(maior, min(1.0, (len(ps) - r) * ps[i]))
        out[i] = maior
    return out


def ljung_box(Y, lags=20):
    """Dependência serial de cada número (autocorrelação até 'lags'), somada: sob independência ~ qui² com U*lags gl.
    Justifica (ou não) redes recorrentes: sem dependência temporal, LSTM/GRU não têm o que capturar."""
    T, U = Y.shape
    Q = 0.0
    for u in range(U):
        x = Y[:, u] - Y[:, u].mean()
        d = (x * x).sum()
        if not d:
            continue
        Q += T * (T + 2) * sum(((x[k:] * x[:-k]).sum() / d) ** 2 / (T - k) for k in range(1, lags + 1))
    gl = U * lags
    z = (Q - gl) / math.sqrt(2 * gl)
    return {"Q": float(Q), "gl": gl, "z": float(z), "p": float(0.5 * math.erfc(z / math.sqrt(2)))}


# =============================== versão 1.1 EXPLORATÓRIA: representações (etapa 3, seções 5.3 e 6) ===============
# Não altera a versão 1.0 (congelada): só acrescenta grupos de atributos para experimentos controlados.
VERSAO_EXPLORATORIA = "aprendizado-1.1-exploratoria"
GRUPOS = ("binario", "frequencias", "decaimento", "atraso", "janela12", "faixa_paridade", "relacional")


def atributos_grupos(Y, grupos, ini=INI):
    """Atributos por número, cada grupo só com concursos anteriores a t:
    binario: saiu em t-1, t-2, t-3 | frequencias: 6 janelas + geral | decaimento: frequência com meia-vida 25 e 100
    atraso | janela12: os 12 últimos estados do próprio número (genérico: permite ver ciclos sem informá-los)
    faixa_paridade: paridade, faixa e frequência recente da classe | relacional: o sorteio anterior inteiro +
    identidade do número (genérico: permite aprender relações entre números sem dizer quais)."""
    T, U = Y.shape
    base = Y[:ini].mean() if T > ini else Y.mean()
    blocos = []
    if "binario" in grupos or "frequencias" in grupos or "atraso" in grupos:
        comp = atributos(Y, "completo", ini)     # [t-1, t-2, t-3, 6 janelas, geral, atraso]
        if "binario" in grupos:
            blocos.append(comp[..., 0:3])
        if "frequencias" in grupos:
            blocos.append(comp[..., 3:10])
        if "atraso" in grupos:
            blocos.append(comp[..., 10:11])
    if "decaimento" in grupos:
        for hl in (25, 100):
            d, c, n = 0.5 ** (1 / hl), np.zeros(U), 0.0
            arr = np.zeros((T, U), np.float32)
            for t in range(T):
                arr[t] = (c / n - base) if n else 0.0
                c, n = c * d + Y[t], n * d + 1
            blocos.append(arr[..., None])
    if "janela12" in grupos:
        jan = np.zeros((T, U, 12), np.float32)
        for k in range(1, 13):
            jan[k:, :, k - 1] = Y[:-k]
        blocos.append(jan - base)
    if "faixa_paridade" in grupos:
        u = np.arange(U)
        cls = (u // 10) * 2 + (u % 2)
        fr = np.zeros((T, U), np.float32)
        C = np.vstack([np.zeros((1, U)), np.cumsum(Y, 0)])
        for t in range(T):
            a = max(0, t - 50)
            cont = np.bincount(cls, weights=C[t] - C[a], minlength=cls.max() + 1) / np.bincount(cls)
            fr[t] = cont[cls] / max(1, t - a) - base
        fixo = np.stack([u % 2, u // 10 / max(1, (U - 1) // 10)], -1).astype(np.float32)
        blocos.append(np.concatenate([np.broadcast_to(fixo, (T, U, 2)), fr[..., None]], -1))
    if "relacional" in grupos:
        prev = np.zeros((T, U), np.float32)
        prev[1:] = Y[:-1]
        blocos.append(np.broadcast_to(prev[:, None, :] - base, (T, U, U)))
        blocos.append(np.broadcast_to(np.eye(U, dtype=np.float32), (T, U, U)))
    return np.concatenate(blocos, -1).astype(np.float32)


def experimento_representacao(s, grupos, seed=0, lr=3e-3, oculta=16, ate=None):
    """Mesma rede e mesmo protocolo online; só a representação muda (controle de uma variável por vez)."""
    A = atributos_grupos(s["Y"], grupos, s.get("ini", INI))
    r = rodar(s, lambda s_, sd: Rede(s_, sd, lr=lr, oculta=oculta, A=A), seed, ate=ate)
    esp = referencia(s)[0]
    base = -np.mean([perda_brier(s, np.full(s["Y"].shape[1], 0.1 if s.get("grupos") else
                                            s["sorteados"] / s["Y"].shape[1]), t)[0]
                     for t in range((ate or len(s["Y"])) - len(r["acertos"]), ate or len(s["Y"]))])
    return {"grupos": list(grupos), "dif": float(r["acertos"].mean() - esp),
            "perda_vs_base_por_mil": float((base + r["log_loss"].mean()) * 1000), "dims": int(A.shape[2])}


# =============================== bateria (Experimentos B a E) ===============================
GRADE = [(lr, h, c) for lr in (1e-3, 3e-3, 1e-2) for h in (8, 32) for c in ("basico", "completo")]


def _fab_rede(cfg, A):
    lr, h, c = cfg
    return lambda s, sd: Rede(s, sd, lr=lr, oculta=h, conjunto=c, A=A[c])


def _arquivo(registros, s, nome):
    if not registros:
        return None
    os.makedirs(registros, exist_ok=True)
    return os.path.join(registros, f"{s['jogo'].replace(':', '_')}__{nome.split(' (')[0].replace(' ', '_')}.jsonl")


def estudar(s, sementes=20, selecionar=True, registros=None, log=print):
    """Escolha só no desenvolvimento; avaliação final intocada; tudo com a mesma informação por concurso."""
    ini, aq = s.get("ini", INI), s.get("aq", AQUECIMENTO)
    n_aval = len(s["Y"]) - ini - aq
    dev = n_aval // 2
    ate_dev = ini + aq + dev
    A = {c: atributos(s["Y"], c, ini) for c in ("basico", "completo")}
    # B) configuração da rede escolhida por log loss médio no desenvolvimento (3 sementes)
    notas = {}
    for cfg in (GRADE if selecionar else [(3e-3, 16, "completo")]):
        notas[cfg] = float(np.mean([rodar(s, _fab_rede(cfg, A), sd, ate=ate_dev)["log_loss"].mean()
                                    for sd in range(3 if selecionar else 1)]))
    cfg = min(notas, key=notas.get)
    log(f"  {s['jogo']}: configuração escolhida no desenvolvimento {cfg}")
    # C) melhor referência escolhida no desenvolvimento (média de acertos)
    ref_dev = {c.nome: float(np.mean([rodar(s, c, sd, ate=ate_dev)["acertos"].mean() for sd in range(3)]))
               for c in REFERENCIAS if c is not Aleatorio}
    melhor_ref = max(ref_dev, key=ref_dev.get)
    # A/E) várias sementes; registro imutável da semente 0
    rede = [rodar(s, _fab_rede(cfg, A), sd, registro=_arquivo(registros, s, "rede") if sd == 0 else None)
            for sd in range(sementes)]
    reproduz = rodar(s, _fab_rede(cfg, A), 0)["cadeia"] == rede[0]["cadeia"]
    refs = {c.nome: [rodar(s, c, sd, registro=_arquivo(registros, s, c.nome) if sd == 0 else None)
                     for sd in range(sementes)] for c in REFERENCIAS}
    fim = n_aval

    def media(rs, k):
        return np.mean([r[k][dev:fim] for r in rs], 0)
    base = refs["aleatório uniforme"]
    mod = rede[0]["modelo"]   # D) esquecimento: modelo final congelado nos concursos do desenvolvimento
    congelado = [perda_brier(s, mod.prever(t), t)[0] for t in range(ini + aq, ate_dev)]
    esp, var = referencia(s)
    return {
        "jogo": s["jogo"], "concursos": len(s["Y"]), "ini": ini, "aquecimento": aq, "desenvolvimento": dev,
        "final": n_aval - dev, "config": list(cfg), "notas_dev": {str(k): v for k, v in notas.items()},
        "melhor_referencia": melhor_ref, "referencias_dev": ref_dev, "reproduz": reproduz,
        "rede": resumo(s, [r["acertos"] for r in rede], dev, fim),
        "refs": {n: resumo(s, [r["acertos"] for r in rs], dev, fim) for n, rs in refs.items()},
        "rede_vs_melhor": comparar(media(rede, "acertos"), media(refs[melhor_ref], "acertos")),
        "log_loss_vs_base": comparar(-media(rede, "log_loss"), -media(base, "log_loss")),      # positivo = melhor
        "brier_vs_base": comparar(-media(rede, "brier"), -media(base, "brier")),
        "log_loss_melhor_ref_vs_base": comparar(-media(refs[melhor_ref], "log_loss"), -media(base, "log_loss")),
        "esquecimento": {"perda_online_dev": float(rede[0]["log_loss"][:dev].mean()),
                         "perda_modelo_final_no_dev": float(np.mean(congelado))},
        "ljung_box": ljung_box(s["Y"]),
        "curva_rede": [float(x) for x in np.cumsum(np.mean([r["acertos"] for r in rede], 0) - esp)],
        "curva_ref": [float(x) for x in np.cumsum(np.mean([r["acertos"] for r in refs[melhor_ref]], 0) - esp)],
        "banda": float(1.96 * math.sqrt(var / sementes)), "banda_1": float(1.96 * math.sqrt(var)),
        "cadeia_rede": rede[0]["cadeia"]}


TIPOS_SINTETICOS = ["nulo", "repetir", "vizinho", "periodico", "deriva", "frio"]


def sinteticos(sementes=5, log=print):
    """Loterias artificiais de vários níveis. A última tem tipo e força sorteados às cegas: ficam selados por hash
    (com sal secreto) antes da avaliação e só são revelados no fim."""
    import secrets
    sal = secrets.token_hex(8)
    tipo_cego = TIPOS_SINTETICOS[secrets.randbelow(len(TIPOS_SINTETICOS))]
    forca_cega = round(0.5 + secrets.randbelow(100) / 100, 2)
    selo = _h(f"{tipo_cego}|{forca_cega}|{sal}".encode())
    log(f"  tipo cego selado: {selo[:16]}…")
    casos = [(t, 1.0) for t in TIPOS_SINTETICOS] + [(t, 0.5) for t in ("repetir", "deriva", "periodico")]
    out = {}
    for k, (tipo, forca) in enumerate(casos + [(tipo_cego, forca_cega)]):
        cego = k == len(casos)
        s = sintetica(tipo, forca, seed=99 if cego else 7)
        r = estudar(s, sementes=sementes, selecionar=False, log=lambda *_: None)
        for chave in ("curva_rede", "curva_ref"):
            r[chave] = r[chave][::10]
        nome = "cego" if cego else f"{tipo}:{forca}"
        out[nome] = r
        log(f"  {nome}: rede {r['rede']['dif']:+.3f} (z {r['rede']['z']:+.1f}) | melhor ref "
            f"{r['refs'][r['melhor_referencia']]['dif']:+.3f}")
    out["_cego"] = {"selo": selo, "tipo": tipo_cego, "forca": forca_cega, "sal": sal,
                    "confere": _h(f"{tipo_cego}|{forca_cega}|{sal}".encode()) == selo}
    return out


# =============================== gráficos (Pillow) ===============================
def grafico(caminho, titulo, series, banda=None):
    """Linhas de acertos acima do acaso (acumulado) e a faixa de 95% do acaso."""
    from PIL import Image, ImageDraw, ImageFont
    W, Hh, M = 1100, 520, 70
    img = Image.new("RGB", (W, Hh), "white")
    d = ImageDraw.Draw(img)
    try:
        f = ImageFont.truetype("C:/Windows/Fonts/segoeui.ttf", 16)
        fb = ImageFont.truetype("C:/Windows/Fonts/segoeuib.ttf", 18)
    except OSError:
        f = fb = ImageFont.load_default()
    n = max(len(ys) for _, ys, _ in series)
    vals = [v for _, ys, _ in series for v in ys] + [0]
    if banda:
        vals += [banda * math.sqrt(n), -banda * math.sqrt(n)]
    lo, hi = min(vals), max(vals)
    hi, lo = hi + (hi - lo) * 0.08 + 1e-9, lo - (hi - lo) * 0.08 - 1e-9

    def X(i):
        return M + (W - 2 * M) * i / max(1, n - 1)

    def Yp(v):
        return Hh - M - (Hh - 2 * M) * (v - lo) / (hi - lo)
    d.text((M, 18), titulo, fill="black", font=fb)
    d.line([(M, Yp(0)), (W - M, Yp(0))], fill="#888888", width=1)
    if banda:
        passo = max(1, n // 200)
        d.line([(X(i), Yp(banda * math.sqrt(i + 1))) for i in range(0, n, passo)], fill="#bbbbbb", width=2)
        d.line([(X(i), Yp(-banda * math.sqrt(i + 1))) for i in range(0, n, passo)], fill="#bbbbbb", width=2)
        d.text((W - M - 200, Yp(banda * math.sqrt(n)) - 22), "faixa de 95% do acaso", fill="#777777", font=f)
    for _nome, ys, cor in series:
        pts = [(X(i), Yp(v)) for i, v in enumerate(ys)][::max(1, len(ys) // 600)]
        if len(pts) > 1:
            d.line(pts, fill=cor, width=3)
    for k, (nome, _, cor) in enumerate(series):
        d.text((M + 10 + k * 360, Hh - M + 20), "■ " + nome, fill=cor, font=f)
    d.text((8, M - 30), "acertos acima do acaso (acumulado)", fill="#444444", font=f)
    d.text((W - M - 170, Hh - M + 45), "concursos avaliados →", fill="#444444", font=f)
    img.save(caminho)


def _main():
    import sys as _s
    _s.stdout.reconfigure(encoding="utf-8")
    os.makedirs(SAIDA, exist_ok=True)
    acao = _s.argv[1] if len(_s.argv) > 1 else "ajuda"
    if acao == "jogo":
        import core
        j = _s.argv[2]
        s = serie(j)
        regs = os.path.join(core.DATA, "aprendizado")
        r = estudar(s, registros=regs)
        if j == "maismilionaria":
            r["trevos"] = estudar(serie_trevos(s), registros=regs)
        json.dump(r, open(os.path.join(SAIDA, f"{j}.json"), "w", encoding="utf8"), default=float)
        print(f"{j}: rede {r['rede']['dif']:+.3f} (z {r['rede']['z']:+.2f}) | melhor ref {r['melhor_referencia']}")
    elif acao == "sinteticos":
        json.dump(sinteticos(), open(os.path.join(SAIDA, "sinteticos.json"), "w", encoding="utf8"), default=float)
    else:
        print(__doc__)


if __name__ == "__main__":
    _main()
