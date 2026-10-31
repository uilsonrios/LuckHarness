"""Regras específicas de cada modalidade (auditoria multiloterias de 31/10/2026)."""
import json

import pytest

import core
import laboratorio as lab
import otimizador as o


def _grava(jogo, conc, dezenas, premios=(), **meta):
    d = {"concurso": conc, "dezenas": [str(x) for x in dezenas], "data": meta.pop("data", ""), "trevos": [],
         "premios": [list(p) for p in premios], **meta}
    with core.db() as c:
        c.execute("INSERT OR REPLACE INTO draws(jogo, concurso, dezenas, meta) VALUES(?,?,?,?)", core._row(jogo, d))


def test_lotomania_00_tem_uma_representacao_so():
    with core.db() as c:
        c.execute("DELETE FROM draws WHERE jogo='lotomania'")
    _grava("lotomania", 1, ["00"] + list(range(1, 20)), [("20 acertos", 0), ("16 acertos", 30.0)])
    assert 100 in core.load_draws("lotomania", 1)[0][1] and 0 not in core.load_draws("lotomania", 1)[0][1]
    nums, _ = core.parse_bet("lotomania", "00 " + " ".join(f"{x:02d}" for x in range(1, 16)) + " "
                             + " ".join(str(x) for x in range(60, 94)))
    assert 100 in nums and core.fmt_bet("lotomania", nums, []).endswith("00")
    core.save_bet("lm", "lotomania", nums, [], 1)
    b = [x for x in core.check_bets("lm") if x["jogo"] == "lotomania"][0]
    assert b["melhor"] == 16 and b["ganhos"] == 30.0           # o 00 sorteado conta como acerto
    assert sorted(core.espelho(nums) + nums) == list(range(1, 101))


def test_migracao_converte_lotomania_e_times_antigos():
    with core.db() as c:
        c.execute("INSERT OR REPLACE INTO draws(jogo, concurso, dezenas, meta) VALUES('lotomania', 900, ?, '{}')",
                  (json.dumps([0] + list(range(1, 20))),))
        c.execute("INSERT OR REPLACE INTO draws(jogo, concurso, dezenas, meta) VALUES('timemania', 900, ?, ?)",
                  (json.dumps(list(range(1, 8))), json.dumps({"time": "AMERICA\x00\x00\x00/RJ"})))
    c = core.db()
    core._migra(c)
    assert 100 in json.loads(c.execute("SELECT dezenas FROM draws WHERE jogo='lotomania' AND concurso=900"
                                       ).fetchone()[0])
    assert json.loads(c.execute("SELECT meta FROM draws WHERE jogo='timemania' AND concurso=900").fetchone()[0]
                      )["time"] == "AMERICA /RJ"


def test_times_padronizados_e_lista_oficial(tmp_path, monkeypatch):
    assert core.norm_time("ABC/RN") == core.norm_time("A B C/RN") == core.norm_time("ABC /RN") == "ABC /RN"
    arq = tmp_path / "times.json"
    arq.write_text(json.dumps({"fonte": "teste", "times": ["ABC/RN", "SANTOS /SP"]}), encoding="utf8")
    monkeypatch.setattr(core, "TIMES_OFICIAIS", str(arq))
    assert core.times_validos() == (["ABC /RN", "SANTOS /SP"], True)
    assert {t[0] for t in o.extras_espalhados("timemania", 6)} <= {"ABC /RN", "SANTOS /SP"}


def test_super_sete_com_mais_numeros_por_coluna():
    precos = {"1,2 3,4 5 6 7 8 9": 12.0, "1,2 3,4 5,6 7,8 9,0 1,2 3,4": 384.0,
              "1,2,3 4,5 6,7 8,9 0,1 2,3 4,5": 576.0, "1,2,3 4,5,6 7,8,9 0,1,2 3,4,5 6,7,8 9,0,1": 6561.0}
    for txt, preco in precos.items():   # tabela oficial (loterias.caixa.gov.br, 31/10/2026)
        assert core.custo("supersete", core.parse_bet("supersete", txt)[0], []) == preco
    with pytest.raises(ValueError):
        core.parse_bet("supersete", "1,2,3 4 5 6 7 8 9")              # 3 numa coluna só a partir de 15
    with core.db() as c:
        c.execute("DELETE FROM draws WHERE jogo='supersete'")
    _grava("supersete", 1, [5, 2, 3, 4, 5, 6, 0], [("7 acertos", 0), ("6 acertos", 1000.0), ("5 acertos", 50.0)])
    nums, _ = core.parse_bet("supersete", "1,5 2 3 4 5 6 7")
    core.save_bet("ss", "supersete", nums, [], 1)
    b = [x for x in core.check_bets("ss") if x["jogo"] == "supersete"][0]
    assert b["ganhos"] == 1000.0 + 50.0                          # 2 apostas simples: uma faz 6, outra faz 5
    assert o._ganho_cartao("supersete", nums, [], [5, 2, 3, 4, 5, 6, 0],
                           {"premios": [["6 acertos", 1000.0], ["5 acertos", 50.0]]})[0] == 1050.0


def test_preco_com_vigencia_e_datas_suspeitas():
    assert core.preco_vigente("megasena", 2887) == (6.0, True)     # 1º concurso com o preço novo (cronograma oficial)
    assert core.preco_vigente("megasena", 2886) == (6.0, False)    # preço da época não registrado: usa o atual
    assert core.preco_vigente("lotofacil", 3439)[1] and not core.preco_vigente("lotomania", 2900)[1]
    assert core.universo("lotomania") == (1, 100) and core.universo("supersete") == (0, 9)
    with core.db() as c:
        c.execute("DELETE FROM draws WHERE jogo='quina'")
    for conc, data in ((1, "08/03/1998"), (2, "12/03/1997"), (3, "15/03/1998")):
        _grava("quina", conc, [1, 2, 3, 4, 5], data=data)
    assert core.datas_fora_de_ordem("quina") == [(2, "12/03/1997")]


@pytest.fixture
def com_historico():
    import random
    rng = random.Random(9)
    with core.db() as c:
        for j in ("supersete", "maismilionaria", "duplasena"):
            c.execute("DELETE FROM draws WHERE jogo=?", (j,))
    for i in range(1, 361):
        _grava("supersete", i, [rng.randrange(10) for _ in range(7)])
        _grava("maismilionaria", i, sorted(rng.sample(range(1, 51), 6)))
        _grava("duplasena", i, sorted(rng.sample(range(1, 51), 6)), dezenas2=sorted(rng.sample(range(1, 51), 6)))


def test_laboratorio_tem_metrica_de_cada_modalidade(com_historico):
    r = lab.teste_cego("supersete", 100, ["ao acaso", "quentes", "frios", "repetir último"])
    assert r["esperado"] == pytest.approx(0.7) and r["faixa"] == "3+ colunas"
    r = lab.teste_cego("maismilionaria", 100, ["ao acaso", "quentes"])
    assert "trevos à parte" in r["faixa"] and r["esperado"] == pytest.approx(0.72)
    r = lab.teste_continuo("duplasena", 50, ["ao acaso"])
    li = [x for x in r["linhas"] if x["nome"] == "ao acaso"][0]
    assert li["apostas"] == 100                                    # 50 concursos x 2 sorteios


def test_experimentos_recusa_o_que_nao_representa():
    import experimentos as ex
    for j in ("supersete", "maismilionaria", "duplasena"):
        with pytest.raises(ValueError):
            ex.carrega(j)


def test_contidas_e_fonte_unica_da_conferencia():
    # 7 dezenas com 5 acertos na Mega = 2 quinas + 5 quadras; +Milionária com 3 trevos (2 certos) e 6 acertos
    assert core.contidas("megasena", 7, 5) == [(4, 0, 5), (5, 0, 2)]
    assert dict(((j, t), q) for j, t, q in core.contidas("maismilionaria", 6, 6, 3, 2))[(6, 2)] == 1
    assert dict(((j, t), q) for j, t, q in core.contidas("maismilionaria", 6, 6, 3, 2))[(6, 1)] == 2


def test_times_do_bienio_quando_nao_ha_lista_oficial(monkeypatch, tmp_path):
    monkeypatch.setattr(core, "TIMES_OFICIAIS", str(tmp_path / "nao_existe.json"))
    with core.db() as c:
        c.execute("DELETE FROM draws WHERE jogo='timemania'")
    for i in range(40):   # 40 times no biênio atual e 1 time antigo que não está mais no volante
        _grava("timemania", 100 + i, range(1, 8), data="10/06/2026", time=f"CLUBE{i:02d}/SP")
    _grava("timemania", 99, range(1, 8), data="10/01/2026", time="ANTIGO/RJ")
    ts, oficial = core.times_validos()
    assert not oficial and len(ts) == 40 and "ANTIGO /RJ" not in ts


def test_painel_mostra_regra_e_estabilidade_entre_sementes(com_historico):
    r = lab.teste_cego("duplasena", 100, ["ao acaso", "quentes"], sementes=3)
    assert len(r["sementes"]["ao acaso"]) == 3 and len(set(r["sementes"]["quentes"])) == 1   # sem sorteio: igual
    txt = lab.texto_resultado(r)
    assert "Estabilidade entre sementes (3 sementes" in txt and "Regras (Dupla Sena)" in txt and "2 sorteios" in txt


def test_super_sete_maior_no_otimizador_e_conta_exata():
    import random
    tam = o.formatos_supersete()[9]
    assert sum(tam) == 9 and max(tam) == 2 and o.formatos_supersete()[21] == [3] * 7
    p_premio = sum(p for p, d in o.padroes_supersete(tam) if any(d[h] for h in (3, 4, 5, 6, 7)))
    card = o.cartoes_supersete(1, tam, random.Random(1))[0]
    rng = random.Random(5)
    sim = sum(any(q for h, q in enumerate(core.dist_colunas(card, [rng.randrange(10) for _ in range(7)])) if h >= 3)
              for _ in range(100000)) / 100000
    assert abs(sim - p_premio) < 4 * (p_premio * (1 - p_premio) / 100000) ** 0.5
    assert core.custo("supersete", card, []) == 3.0 * 4          # 9 números = 2 colunas com 2 = 4 apostas


def test_dupla_sena_dois_sorteios_na_analise_e_popularidade(com_historico):
    s = core.load_sorteios("duplasena", 5)
    assert len(s) == 10 and s[0][0] == s[1][0]
    assert "sorteios (2 por concurso)" in core.analyze("duplasena", s, 6)[0]


def test_proximo_sorteio_usa_a_data_oficial():
    from datetime import date
    with core.db() as c:
        c.execute("DELETE FROM draws WHERE jogo='timemania'")
    _grava("timemania", 1, range(1, 8), data="29/10/2026", proximo="01/11/2026")
    assert core.proximo_sorteio("timemania", date(2026, 10, 30)) == date(2026, 11, 1)   # oficial (domingo)
    assert "data oficial da Caixa" in core.texto_calendario(date(2026, 10, 30))
    # data oficial já passou (resultado ainda não baixado): volta ao calendário de dias habituais
    assert core.proximo_sorteio("timemania", date(2026, 11, 3)).weekday() in core.DIAS_SORTEIO["timemania"]
