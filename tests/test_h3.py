import hashlib
import json
import os
import random

import pytest

import core
import popularidade_h3 as h3

CRITERIOS_SHA = "7eeca0b3ca43faa4f39ae146e697a4a9fb30b879011060a0104cb2a8cd71ad60"


def _meta(rng, ganh15=None, arrec=2.0e7):
    g15 = rng.randrange(0, 6) if ganh15 is None else ganh15
    return {"arrecadado": arrec, "premios": [["15 acertos", 1e6], ["14 acertos", 2000.0], ["13 acertos", 35.0],
                                             ["12 acertos", 14.0], ["11 acertos", 7.0]],
            "ganhadores": [g15, rng.randrange(150, 350), rng.randrange(6000, 9000), rng.randrange(70000, 100000),
                           rng.randrange(400000, 560000)]}


@pytest.fixture
def lotofacil():
    rng = random.Random(9)
    with core.db() as c:
        c.execute("DELETE FROM draws WHERE jogo='lotofacil'")
        for i in range(3500, 3819):
            c.execute("INSERT INTO draws(jogo, concurso, dezenas, meta) VALUES(?,?,?,?)",
                      ("lotofacil", i, json.dumps(sorted(rng.sample(range(1, 26), 15))), json.dumps(_meta(rng))))
    if os.path.exists(h3.REGISTRO):
        os.remove(h3.REGISTRO)
    return rng


def _sorteia(rng, conc, **kw):
    with core.db() as c:
        c.execute("INSERT INTO draws(jogo, concurso, dezenas, meta) VALUES(?,?,?,?)",
                  ("lotofacil", conc, json.dumps(sorted(rng.sample(range(1, 26), 15))), json.dumps(_meta(rng, **kw))))


def test_pre_registro_e_codigo_congelados():
    assert hashlib.sha256(h3._criterios()).hexdigest() == CRITERIOS_SHA
    assert h3.verificar_codigo() == h3.codigo_sha()      # quem mexer no modelo H3 quebra este teste


def test_codigo_alterado_nao_sela(lotofacil, monkeypatch):
    monkeypatch.setattr(h3, "codigo_sha", lambda: "0" * 64)
    with pytest.raises(RuntimeError, match="código alterado"):
        h3.selar(log=lambda m: None)
    assert not h3._registros()


def test_sela_so_com_o_passado_e_reproduz(lotofacil):
    assert h3.selar(log=lambda m: None) == 3819
    assert h3.selar(log=lambda m: None) is None                      # não sela duas vezes
    r = h3._registros()[0]
    assert r["concurso"] == 3819 and r["treinado_ate"] == 3818       # nunca vê o concurso que vai avaliar
    import otimizador as o
    treino = [a for a in o._amostras("lotofacil") if a["concurso"] < 3819]
    assert h3._sha(json.dumps(o._ajusta("lotofacil", treino, h3.CFG), sort_keys=True)) == r["modelo"]
    assert h3.verificar_cadeia()


def test_cadeia_detecta_adulteracao(lotofacil):
    h3.selar(log=lambda m: None)
    linhas = open(h3.REGISTRO, encoding="utf8").read().splitlines()
    reg = json.loads(linhas[0])
    reg["treinado_ate"] = 3819
    open(h3.REGISTRO, "w", encoding="utf8").write(json.dumps(reg) + "\n")
    assert not h3.verificar_cadeia()


def test_acumulado_fica_com_r15_zero_e_falta_de_dado_fica_fora(lotofacil):
    h3.selar(log=lambda m: None)
    _sorteia(lotofacil, 3819, ganh15=0)
    h3.selar(log=lambda m: None)
    _sorteia(lotofacil, 3820, arrec=None)                            # sem dado oficial de vendas
    r = h3.avaliar()
    assert [x["concurso"] for x in r["linhas"]] == [3819] and r["faltas"] == [3820]
    assert r["linhas"][0]["r15"] == 0 and r["linhas"][0]["g15"] == 0 and r["linhas"][0]["e15"] > 0
    assert "Avaliados: 1/150" in h3.texto() and "TESTE CONFIRMATÓRIO" not in h3.texto()


def test_modelo_adulterado_e_recusado(lotofacil):
    h3.selar(log=lambda m: None)
    _sorteia(lotofacil, 3819)
    with open(h3._modelo_arquivo(3819), "a", encoding="utf8") as f:
        f.write(" ")
    with pytest.raises(RuntimeError, match="não confere"):
        h3.avaliar()


def test_permutacao_e_postos():
    pytest.importorskip("numpy")
    assert h3._postos([0, 0, 1, 0.5, 0]) == [2.0, 2.0, 5.0, 4.0, 2.0]   # empates com posto médio
    rng = random.Random(3)
    f = [rng.gauss(0, 1) for _ in range(150)]
    rho, p = h3.p_spearman(f, [x + rng.gauss(0, 1) for x in f], B=2000)
    assert rho > 0.5 and p < 0.001
    _, p0 = h3.p_spearman(f, [rng.gauss(0, 1) for _ in f], B=2000)
    assert p0 > 0.01
    q, pq = h3.p_extremos(f, [max(0, round(3 + 2 * x)) for x in f], [3.0] * 150, B=2000)
    assert q > 1 and pq < 0.01
