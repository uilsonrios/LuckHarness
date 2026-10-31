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
    assert vistos == [450] * 3                # uma aposta por semente (3), sempre com dados até o 450
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


def test_zero_acerto_da_lotomania_conta_como_premio():
    r = lab._avaliar("lotomania", {"zero": [[0]] * 5, "nada": [[10]] * 5})
    linha = {li["nome"]: li for li in r["linhas"]}
    assert linha["zero"]["premios"] == 5 and linha["nada"]["premios"] == 0
    assert r["faixa"] == "15+ ou 0"
    assert r["p_premio"] == pytest.approx(sum(core.hypergeom(100, 20, 50, h) for h in (0, 15, 16, 17, 18, 19, 20)))


def test_cartoes_iguais_no_mesmo_concurso_nao_viram_vantagem_falsa():
    # 10 cópias do mesmo cartão ao acaso em cada concurso: as 10 não são observações independentes
    rng = random.Random(3)
    reg = {"copias": []}
    for _ in range(200):
        card, sorteio = set(rng.sample(range(1, 26), 15)), set(rng.sample(range(1, 26), 15))
        reg["copias"].append([len(card & sorteio)] * 10)
    li = lab._avaliar("lotofacil", reg)["linhas"][0]
    media = sum(c[0] for c in reg["copias"]) / 200
    # desvio entre concursos ~1,22 (o de um cartão), não 1,22/sqrt(10)
    assert abs(li["z"]) == pytest.approx(abs(media - 9) / (1.22 / 200 ** 0.5), rel=0.15)


def test_experimentos_ficam_registrados_e_corrigidos_pela_familia(quina):
    with core.db() as c:
        c.execute("DELETE FROM experimentos WHERE jogo='quina'")
    r1 = lab.teste_cego(quina, 50, ["ao acaso", "quentes", "frios"])
    r2 = lab.teste_cego(quina, 50, ["ao acaso", "quentes", "frios"], seed=7)
    assert (r1["hipoteses"], r2["hipoteses"]) == (3, 6) and r2["execucoes"] == 2
    assert all(li["p_familia"] >= li["p_ajust"] - 1e-12 for li in r2["linhas"])   # conta as hipóteses de antes
    reg = core.db().execute("SELECT params, versao, hash_dados FROM experimentos WHERE jogo='quina'").fetchall()
    assert len(reg) == 2 and json.loads(reg[1][0])["seed"] == 7 and reg[1][1] == core.__version__ and reg[1][2]
    txt = lab.texto_resultado(r2)
    assert "IC 95%" in txt and "sem evidência de diferença" in txt and "6 hipóteses" in txt


def test_neuronio_relata_ic_e_poder(quina):
    txt, _ = core.learn(quina, core.load_draws(quina, 1000), 5, teste=0)
    assert "IC 95%" in txt and "só detecta diferenças" in txt and "NÃO supera" not in txt


def test_ciclo_diario_registra_rede_e_estrategias_uma_vez(quina):
    pytest.importorskip("numpy")
    lab._sql_lab()
    with core.db() as c:
        c.execute("DELETE FROM lab WHERE user='diario'")
    r1 = lab.ciclo_diario("diario", log=lambda m: None)
    assert "quina 501" in r1["registrados"]
    est = {e for (e,) in core.db().execute("SELECT estrategia FROM lab WHERE user='diario' AND jogo='quina'")}
    assert "rede online" in est and "quentes" in est and "minha aposta" not in est
    nums = json.loads(core.db().execute("SELECT numeros FROM lab WHERE user='diario' AND jogo='quina' AND estrategia='rede online'"
                                        ).fetchone()[0])[0]
    assert len(nums) == 5 and all(1 <= x <= 80 for x in nums)
    assert "quina 501" not in lab.ciclo_diario("diario", log=lambda m: None)["registrados"]   # não duplica
    # registro antigo sem a rede (sorteio ainda não aconteceu): a rede entra depois, com selo próprio
    with core.db() as c:
        c.execute("DELETE FROM lab WHERE user='diario' AND estrategia='rede online'")
    assert "quina 501 (rede online)" in lab.ciclo_diario("diario", log=lambda m: None)["registrados"]


def test_resumo_do_dia_mostra_acumulado_e_proximo(quina):
    with core.db() as c:
        c.execute("UPDATE draws SET meta=? WHERE jogo='quina' AND concurso=500",
                  (json.dumps({"data": "30/10/2026", "premios": [["5 acertos", 0]], "ganhadores": [0],
                               "estimado": 9.5e6, "proximo": "01/11/2026"}),))
    txt = core.resumo_do_dia()
    assert "Quina — concurso 500 (30/10/2026)" in txt and "ACUMULOU" in txt and "concurso 501 em 01/11/2026" in txt
