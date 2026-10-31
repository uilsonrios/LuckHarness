"""Experimentos de previsão de dezenas, reproduzíveis e registrados (ferramenta de pesquisa; não faz parte do app).

Protocolo (LuckHarness_Diretrizes): walk-forward (cada bloco de teste é previsto por modelos treinados só com o
passado), parada antecipada numa validação temporal dentro do treino, comparação com a conta exata do acaso
(hipergeométrica) e com uma distribuição nula simulada, estabilidade por bloco e por semente, e um registro em
experimentos/registro.jsonl com semente, commit, hash dos dados, janelas, atributos e resultados. Se o período de
teste já aparece no registro, o resultado é marcado como exploratório: teste consultado não é teste independente.

Uso: python experimentos.py <jogo|sintetico_forte|sintetico_fraco> [--teste 1000] [--sementes 1] [--rapido]
Requer numpy (requirements-dev.txt).
"""
import argparse, hashlib, json, math, os, platform, subprocess, sys, time
from math import comb

import numpy as np

AQUI = os.path.dirname(os.path.abspath(__file__))
REGISTRO = os.path.join(AQUI, "experimentos", "registro.jsonl")
JAN = (5, 10, 20, 50, 100, 200)
INI, BLOCO = 200, 100
NAO_SUPORTADOS = {"supersete": "colunas com dígitos que se repetem, não dezenas distintas.",
                  "maismilionaria": "os trevos fazem parte do resultado.",
                  "duplasena": "são dois sorteios por concurso."}
ATRIBUTOS = ["saiu em t-1", "saiu em t-2", "saiu em t-3"] + [f"frequência últimos {w}" for w in JAN] + [
    "frequência geral", "atraso (até 100)", "saiu em t-1 e t-2"]


def carrega(alvo, seed=2026):
    """Matriz concursos x números (1 = sorteado), N, K e a impressão digital dos dados."""
    if alvo in NAO_SUPORTADOS:
        raise ValueError(f"{alvo}: {NAO_SUPORTADOS[alvo]} Este experimento só representa 'K números distintos "
                         "de 1 a N'; use o Laboratório, que tem métrica própria para esta modalidade.")
    if alvo.startswith("sintetico"):
        # Lotofácil falsa com padrão plantado (quem saiu no anterior tem peso maior): prova que o método acha sinal
        forca = {"sintetico_forte": 1.5, "sintetico_fraco": 1.15}[alvo]
        r, N, K, T = np.random.default_rng(seed), 25, 15, 3800
        X = np.zeros((T, N), np.float32)
        X[0, r.choice(N, K, replace=False)] = 1
        for t in range(1, T):
            g = np.log(np.where(X[t - 1] > 0, forca, 1.0)) - np.log(-np.log(r.random(N)))
            X[t, np.argsort(-g)[:K]] = 1
        return X, N, K, f"sintético-{forca}-{seed}"
    import core
    N, K = core.JOGOS[alvo][0], core.JOGOS[alvo][1]
    D = sorted(core.load_draws(alvo, 100000))
    X = np.zeros((len(D), N), np.float32)
    for i, (_, d) in enumerate(D):
        X[i, np.array(d) - 1] = 1
    return X, N, K, core.hash_dados(alvo)


def atributos(X):
    """Linha t usa só os concursos anteriores a t (sem vazamento)."""
    T, N = X.shape
    C = np.vstack([np.zeros((1, N), np.float32), np.cumsum(X, 0)])
    atr, a = np.zeros((T, N), np.float32), np.full(N, 100.0)
    for t in range(T):
        atr[t] = np.minimum(a, 100) / 100
        a = np.where(X[t] > 0, 1, a + 1)
    F = [np.concatenate([X[t - 1], X[t - 2], X[t - 3]] + [(C[t] - C[t - w]) / w for w in JAN]
                        + [C[t] / t, atr[t], X[t - 1] * X[t - 2]]) for t in range(INI, T)]
    return np.array(F, np.float32), X[INI:]


def bce(p, y):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return float(-(y * np.log(p) + (1 - y) * np.log(1 - p)).mean())


def treina(Xtr, Ytr, camadas, epocas, seed, p0, drop=0.0, l2=1e-4, lr=1e-3, lote=64):
    """MLP (ReLU, Adam) com parada antecipada pelos 15% mais recentes do treino. Devolve a função de previsão e
    as perdas de treino/validação por época (curva para monitorar overfitting)."""
    r = np.random.default_rng(seed)
    nv = max(50, int(len(Xtr) * 0.15))
    Xa, Ya, Xv, Yv = Xtr[:-nv], Ytr[:-nv], Xtr[-nv:], Ytr[-nv:]
    dims = [Xtr.shape[1], *camadas, Ytr.shape[1]]
    W = [r.normal(0, math.sqrt(2 / dims[i]), (dims[i], dims[i + 1])).astype(np.float32) for i in range(len(dims) - 1)]
    B = [np.zeros(d, np.float32) for d in dims[1:]]
    B[-1][:] = math.log(p0 / (1 - p0))
    m = [np.zeros_like(w) for w in W + B]
    v = [np.zeros_like(w) for w in W + B]
    passo, curva, melhor, best = 0, [], 1e9, None

    def frente(x, treino=False):
        hs, masks = [x], []
        for i in range(len(W) - 1):
            h = np.maximum(hs[-1] @ W[i] + B[i], 0)
            mk = (r.random(h.shape) > drop) / (1 - drop) if treino and drop else None
            hs.append(h * mk if mk is not None else h)
            masks.append(mk)
        return hs, masks, 1 / (1 + np.exp(-(hs[-1] @ W[-1] + B[-1])))

    for _ in range(epocas):
        idx = r.permutation(len(Xa))
        for s in range(0, len(Xa), lote):
            b = idx[s:s + lote]
            hs, masks, p = frente(Xa[b], True)
            g = (p - Ya[b]) / len(b)
            gW, gB = [None] * len(W), [None] * len(B)
            for i in range(len(W) - 1, -1, -1):
                gW[i], gB[i] = hs[i].T @ g + l2 * W[i], g.sum(0)
                if i:
                    g = (g @ W[i].T) * (hs[i] > 0)
                    if masks[i - 1] is not None:
                        g = g * masks[i - 1]
            passo += 1
            for j, (prm, gr) in enumerate(zip(W + B, gW + gB)):
                m[j] = 0.9 * m[j] + 0.1 * gr
                v[j] = 0.999 * v[j] + 0.001 * gr * gr
                prm -= lr * (m[j] / (1 - 0.9 ** passo)) / (np.sqrt(v[j] / (1 - 0.999 ** passo)) + 1e-8)
        pv = bce(frente(Xv)[2], Yv)
        curva.append((bce(frente(Xa)[2], Ya), pv))
        if pv < melhor:
            melhor, best = pv, ([w.copy() for w in W], [b.copy() for b in B])
    W[:], B[:] = best
    return (lambda x: frente(x)[2]), curva


def modelos(rapido):
    """Hiperparâmetros fixos, definidos antes do teste (nada é escolhido olhando o teste)."""
    out = {"frequência histórica": None, "regressão logística": dict(camadas=(), epocas=10 if rapido else 40)}
    if not rapido:
        out["rede 128-64"] = dict(camadas=(128, 64), epocas=40)
        out["rede 256-128-64 dropout"] = dict(camadas=(256, 128, 64), epocas=40, drop=0.2)
    return out


def _commit():
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=AQUI, capture_output=True, text=True,
                              timeout=10).stdout.strip() or None
    except OSError:
        return None


def ja_consultados(alvo, ini, fim, registro=REGISTRO):
    """Execuções anteriores cujo período de teste se sobrepõe a [ini, fim]."""
    if not registro or not os.path.exists(registro):
        return []
    out = []
    with open(registro, encoding="utf8") as f:
        for linha in f:
            e = json.loads(linha)
            if e["alvo"] == alvo and e["teste"][0] <= fim and ini <= e["teste"][1]:
                out.append(e["id"])
    return out


def rodar(alvo, teste=1000, sementes=1, rapido=False, registro=REGISTRO, nulos=2000, log=print):
    t0 = time.time()
    X, N, K, hdados = carrega(alvo)
    F, Y = atributos(X)
    teste = min(teste, len(F) - 200)
    ini = len(F) - teste
    p0 = K / N
    cfgs = modelos(rapido)
    prev = {(nome, s): np.zeros((teste, N), np.float32) for nome in cfgs for s in range(sementes)}
    curvas = {}
    for s0 in range(ini, len(F), BLOCO):
        e = min(s0 + BLOCO, len(F))
        mu, sd = F[:s0].mean(0), F[:s0].std(0) + 1e-6      # normalização só com o treino
        Ztr, Zte = (F[:s0] - mu) / sd, (F[s0:e] - mu) / sd
        for nome, cfg in cfgs.items():
            for sem in range(sementes):
                if cfg is None:
                    prev[(nome, sem)][s0 - ini:e - ini] = F[s0:e, 9 * N:10 * N]
                    continue
                f, curva = treina(Ztr, Y[:s0], cfg["camadas"], cfg["epocas"], 1000 * sem + s0, p0, cfg.get("drop", 0))
                prev[(nome, sem)][s0 - ini:e - ini] = f(Zte)
                curvas.setdefault(nome, []).append(curva[-1])
        log(f"  bloco {s0 - ini + 1}-{e - ini} ({time.time() - t0:.0f}s)")
    Yte = Y[ini:]
    esp = K * K / N
    sd1 = math.sqrt(K * (K / N) * ((N - K) / N) * ((N - K) / (N - 1)))
    P = [comb(K, h) * comb(N - K, K - h) / comb(N, K) for h in range(K + 1)]
    # distribuição nula simulada: cartões ao acaso nos mesmos concursos
    rn = np.random.default_rng(12345)
    medias_nulas = np.array([np.take_along_axis(Yte, np.argsort(rn.random(Yte.shape), 1)[:, :K], 1).sum(1).mean()
                             for _ in range(nulos)])
    res = {}
    for (nome, _sem), p in prev.items():
        hits = np.take_along_axis(Yte, np.argsort(-p, 1)[:, :K], 1).sum(1)
        se = sd1 / math.sqrt(teste)
        z = (hits.mean() - esp) / se
        r = res.setdefault(nome, {"sementes": []})
        r["sementes"].append({
            "media": float(hits.mean()), "dif": float(hits.mean() - esp), "ic95": [float(hits.mean() - esp - 1.96 * se),
                                                                                  float(hits.mean() - esp + 1.96 * se)],
            "z": float(z), "p": float(math.erfc(abs(z) / math.sqrt(2))),
            "percentil_nulo": float((medias_nulas < hits.mean()).mean()),
            "dist": np.bincount(hits.astype(int), minlength=K + 1).tolist(),
            "blocos": [float(hits[i:i + BLOCO].mean() - esp) for i in range(0, teste, BLOCO)],
            "perda_vs_acaso_x1000": 1000 * (bce(p, Yte) - bce(np.full_like(Yte, p0), Yte))})
        if nome in curvas:
            tr, va = np.mean([c[0] for c in curvas[nome]]), np.mean([c[1] for c in curvas[nome]])
            r["perda_final_treino_validacao"] = [float(tr), float(va)]
    # Holm sobre todas as (modelo, semente) desta execução
    todos = [(nome, i, x["p"]) for nome, r in res.items() for i, x in enumerate(r["sementes"])]
    ordem, maior = sorted(range(len(todos)), key=lambda i: todos[i][2]), 0.0
    for k, i in enumerate(ordem):
        maior = max(maior, min(1.0, (len(todos) - k) * todos[i][2]))
        res[todos[i][0]]["sementes"][todos[i][1]]["p_holm"] = maior
    concursos_teste = [int(INI + ini) + 1, int(INI + len(F))]   # posição 1-based na série (= concurso nos jogos reais)
    anteriores = ja_consultados(alvo, *concursos_teste, registro)
    entrada = {
        "id": hashlib.sha256(f"{alvo}{time.time()}".encode()).hexdigest()[:10],
        "quando": time.strftime("%Y-%m-%dT%H:%M:%S"), "alvo": alvo, "commit": _commit(), "hash_dados": hdados,
        "teste": concursos_teste, "treino_inicio": INI + 1, "bloco": BLOCO, "sementes": sementes, "rapido": rapido,
        "atributos": ATRIBUTOS, "modelos": {k: v for k, v in cfgs.items()}, "acaso": {"media": esp, "P": P},
        "nulos_simulados": nulos, "resultados": res, "teste_ja_consultado_por": anteriores,
        "confirmatorio": not anteriores,
        "ambiente": {"python": platform.python_version(), "numpy": np.__version__, "so": platform.platform()},
        "segundos": round(time.time() - t0)}
    if registro:
        os.makedirs(os.path.dirname(registro), exist_ok=True)
        with open(registro, "a", encoding="utf8") as f:
            f.write(json.dumps(entrada, ensure_ascii=False) + "\n")
    return entrada


def texto(e):
    out = [f"=== EXPERIMENTO {e['id']}: {e['alvo']} | teste {e['teste'][0]}-{e['teste'][1]} | "
           f"commit {e['commit']} | dados {e['hash_dados']} ===",
           ("Teste INÉDITO (confirmatório)." if e["confirmatorio"] else
            f"EXPLORATÓRIO: este período já foi consultado por {', '.join(e['teste_ja_consultado_por'])}."),
           f"Acaso: {e['acaso']['media']:.3f} acertos por cartão."]
    for nome, r in e["resultados"].items():
        for i, x in enumerate(r["sementes"]):
            out.append(f"  {nome:<26} semente {i}: média {x['media']:.3f}  dif {x['dif']:+.3f} "
                       f"(IC95 {x['ic95'][0]:+.3f} a {x['ic95'][1]:+.3f})  p {x['p']:.3f}  Holm {x['p_holm']:.3f}  "
                       f"percentil no nulo {x['percentil_nulo']:.0%}")
    return "\n".join(out)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("alvo")
    ap.add_argument("--teste", type=int, default=1000)
    ap.add_argument("--sementes", type=int, default=1)
    ap.add_argument("--rapido", action="store_true")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    print(texto(rodar(a.alvo, a.teste, a.sementes, a.rapido)))
