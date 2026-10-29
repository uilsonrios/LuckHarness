from itertools import combinations

import pytest

import core


def test_hypergeom_megasena_sena():
    assert core.hypergeom(60, 6, 6, 6) == pytest.approx(1 / 50_063_860)


def test_hypergeom_soma_um():
    assert sum(core.hypergeom(25, 15, 15, k) for k in range(16)) == pytest.approx(1)


def test_prob_faixa_supersete():
    assert sum(core.prob_faixa("supersete", h) for h in range(8)) == pytest.approx(1)


def test_parse_bet_ok():
    assert core.parse_bet("megasena", "01 02 03 04 05 60") == ([1, 2, 3, 4, 5, 60], [])
    assert core.parse_bet("maismilionaria", "1 2 3 4 5 6 + 4 1") == ([1, 2, 3, 4, 5, 6], [1, 4])
    assert core.parse_bet("supersete", "0 1 2 3 4 5 9") == ([0, 1, 2, 3, 4, 5, 9], [])


@pytest.mark.parametrize("jogo,txt", [("megasena", "1 2 3 4 5 61"), ("megasena", "1 2 3"),
                                      ("supersete", "1 2 3"), ("maismilionaria", "1 2 3 4 5 6 + 7 1")])
def test_parse_bet_invalida(jogo, txt):
    with pytest.raises(ValueError):
        core.parse_bet(jogo, txt)


def test_custo():
    assert core.custo("megasena", list(range(1, 7)), []) == 5.0
    assert core.custo("megasena", list(range(1, 8)), []) == 5.0 * 7
    assert core.custo("maismilionaria", list(range(1, 7)), [1, 2, 3]) == 6.0 * 3


def test_brl():
    assert core.brl(1234567.891) == "R$ 1.234.567,89"


def test_fechamento_garante_cobertura():
    nums, k, t, m = list(range(1, 11)), 6, 4, 5
    cards = core.fechamento(nums, k, t, m)
    for alvo in combinations(nums, m):
        assert any(len(set(alvo) & set(c)) >= t for c in cards)


def test_fechamento_parametros_invalidos():
    with pytest.raises(ValueError):
        core.fechamento([1, 2, 3], 6, 4, 5)


def test_auth_e_sessao():
    core.register("ana", "segredo1")
    assert core.check_login("ana", "segredo1")
    assert not core.check_login("ana", "errada")
    with pytest.raises(ValueError):
        core.register("bia", "123")
    core.register("bia", "segredo2")
    core.remember("bia")
    assert core.remembered_user() == "bia"
    core.forget("ana")  # não pode derrubar a sessão de outro usuário
    assert core.remembered_user() == "bia"
    core.forget("bia")
    assert core.remembered_user() is None


def test_db_fecha_conexao():
    with core.db() as c:
        c.execute("SELECT 1")
    with pytest.raises(core.sqlite3.ProgrammingError):
        c.execute("SELECT 1")


def test_learn_poucos_dados():
    txt, top = core.learn("quina", [(i, [1, 2, 3, 4, 5]) for i in range(10)], 5)
    assert top is None and "60" in txt


def test_duplasena_confere_segundo_sorteio():
    d = {"concurso": 9, "dezenas": [str(x) for x in (1, 2, 3, 4, 5, 6, 40, 41, 42, 43, 44, 45)], "data": "",
         "trevos": [], "premios": [["6 acertos", 0], ["5 acertos", 10.0], ["6 acertos", 0], ["5 acertos", 7.0]]}
    with core.db() as c:
        c.execute("INSERT INTO draws(jogo, concurso, dezenas, meta) VALUES(?,?,?,?)", core._row("duplasena", d))
    core.save_bet("carla", "duplasena", [40, 41, 42, 43, 44, 50], [], 9)
    b = core.check_bets("carla")[0]
    assert b["premiadas"] == [("9 (2º sorteio)", "5 acertos", 7.0)]
    assert b["ganhos"] == 7.0


def _salva(jogo, conc, dezenas, premios, **meta):
    d = {"concurso": conc, "dezenas": [str(x) for x in dezenas], "data": "", "trevos": [], "premios": premios, **meta}
    with core.db() as c:
        c.execute("INSERT INTO draws(jogo, concurso, dezenas, meta) VALUES(?,?,?,?)", core._row(jogo, d))


def test_timemania_time_do_coracao():
    _salva("timemania", 5, range(1, 8), [["3 acertos", 3.5], ["Time do Coração", 8.5]], time="VITORIA /BA")
    _salva("timemania", 6, range(1, 8), [["Time do Coração", 8.5]], time="ATLETICO /MG")
    _salva("timemania", 7, range(1, 8), [["Time do Coração", 8.5]], time="ATLETICO /PR")
    nums, tv = core.parse_bet("timemania", "1 2 3 40 41 42 43 44 45 46 + Vitória")
    assert tv == ["VITORIA /BA"]
    with pytest.raises(ValueError, match="mais de um"):
        core.parse_bet("timemania", "1 2 3 40 41 42 43 44 45 46 + atletico")
    assert core.custo("timemania", nums, tv) == 3.5
    core.save_bet("dani", "timemania", nums, tv, 5)
    b = core.check_bets("dani")[0]
    assert b["premiadas"][:2] == [(5, "Time do Coração", 8.5), (5, "3 acertos", 3.5)]
    assert sum(1 for r in b["premiadas"] if r[1] == "Time do Coração") == 1


def test_diadesorte_mes_da_sorte():
    _salva("diadesorte", 3, range(1, 8), [["Mês da Sorte", 2.5]], mes="OUTUBRO")
    assert core.parse_bet("diadesorte", "1 2 3 4 5 6 30 + 10")[1] == ["Outubro"]
    nums, tv = core.parse_bet("diadesorte", "10 11 12 13 14 15 30 + out")
    core.save_bet("eva", "diadesorte", nums, tv, 3)
    assert core.check_bets("eva")[0]["ganhos"] == 2.5
    with pytest.raises(ValueError):
        core.parse_bet("diadesorte", "1 2 3 4 5 6 7 + xyz")


def test_extra_aleatorio_e_editar():
    _salva("timemania", 50, range(1, 8), [["Time do Coração", 8.5]], time="SANTOS /SP")
    assert core.extra_aleatorio("diadesorte")[0] in core.MESES
    assert core.extra_aleatorio("timemania")[0] in core.times_conhecidos()
    assert len(core.extra_aleatorio("maismilionaria")) == 2
    assert core.extra_aleatorio("megasena") == []
    bid = core.save_bet("fabi", "timemania", list(range(1, 11)), [], 50)
    assert core.editar_extra("fabi", bid, "santos") == ("timemania", ["SANTOS /SP"])
    assert core.check_bets("fabi")[0]["ganhos"] >= 8.5
    with pytest.raises(ValueError):
        core.editar_extra("outro", bid, "santos")


def test_backup_restore(tmp_path):
    core.register("gabi", "segredo3")
    arq = str(tmp_path / "bk.db")
    core.backup_db(arq)
    with core.db() as c:
        c.execute("DELETE FROM users WHERE name='gabi'")
    core.restore_db(arq)
    assert core.check_login("gabi", "segredo3")
    ruim = tmp_path / "x.db"
    core.sqlite3.connect(ruim).close()
    with pytest.raises(ValueError):
        core.restore_db(str(ruim))
