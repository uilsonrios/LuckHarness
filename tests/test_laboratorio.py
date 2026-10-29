import json
import random

import pytest

import core
import laboratorio as lab


@pytest.fixture
def quina():
    rng = random.Random(4)
    with core.db() as c:
        c.execute("DELETE FROM draws WHERE jogo='quina'")
        for i in range(1, 501):
            c.execute("INSERT INTO draws(jogo, concurso, dezenas, meta) VALUES(?,?,?,?)",
                      ("quina", i, json.dumps(sorted(rng.sample(range(1, 81), 5))), None))
    return "quina"


def test_estrategias_nunca_enxergam_o_futuro(quina, monkeypatch):
    original = lab._apostas
    vistos = []

    def espia(jogo, passado, ctx):
        vistos.append(passado[-1][0])
        return original(jogo, passado, ctx)
    monkeypatch.setattr(lab, "_apostas", espia)
    lab.teste_cego(quina, 50, ["ao acaso", "quentes", "frios"])
    assert vistos == [450]                    # apostou uma vez só, com dados até o 450
    vistos.clear()
    lab.teste_continuo(quina, 30, ["quentes", "frios"])
    assert vistos == list(range(470, 500))    # cada concurso t só viu até t-1


def test_teste_cego_tem_selo_e_avaliacao(quina):
    r = lab.teste_cego(quina, 100, ["ao acaso", "quentes", "minha aposta"], minha=[1, 2, 3, 4, 5])
    assert len(r["hash"]) == 64
    nomes = {li["nome"] for li in r["linhas"]}
    assert nomes == {"ao acaso", "quentes", "minha aposta"}
    assert all(0 <= li["p_ajust"] <= 1 and li["apostas"] == 100 for li in r["linhas"])
    assert "LABORATÓRIO" in lab.texto_resultado(r)


def test_hedge_no_teste_continuo(quina):
    r = lab.teste_continuo(quina, 50, ["ao acaso", "quentes", "frios"])
    assert "aprende (Hedge)" in {li["nome"] for li in r["linhas"]}
    assert sum(r["pesos_hedge"].values()) == pytest.approx(1)


def test_registrar_e_conferir(quina):
    txt = lab.registrar_proximo("lia", quina, ["ao acaso", "minha aposta"], minha=[10, 20, 30, 40, 50])
    assert "concurso 501" in txt
    assert "aguardando sorteio" in lab.conferir_registros("lia")
    with core.db() as c:
        c.execute("INSERT INTO draws(jogo, concurso, dezenas, meta) VALUES(?,?,?,?)",
                  (quina, 501, json.dumps([10, 20, 30, 41, 51]), None))
    conf = lab.conferir_registros("lia")
    assert "minha aposta" in conf and "acertos 3" in conf


def test_minha_aposta_valida_quantidade():
    with pytest.raises(ValueError):
        lab.minha_aposta("megasena", "1 2 3 4 5 6 7")
    assert lab.minha_aposta("megasena", "6 5 4 3 2 1") == [1, 2, 3, 4, 5, 6]
