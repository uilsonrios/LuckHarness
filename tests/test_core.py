import json
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
    assert core.custo("megasena", list(range(1, 7)), []) == 6.0
    assert core.custo("megasena", list(range(1, 8)), []) == 6.0 * 7
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


def test_fechamento_otimizado_mantem_garantia_e_reduz():
    nums, k, t, m = list(range(1, 16)), 6, 3, 4
    info = {}
    cards = core.fechamento(nums, k, t, m, tempo=3, info=info)
    assert info["final"] == len(cards) <= info["guloso"]
    assert len(cards) < 20   # guloso dá 20; a busca local chega a 16
    for alvo in combinations(nums, m):
        assert any(len(set(alvo) & set(c)) >= t for c in cards)


def test_editar_trevos_ajusta_banca():
    bid = core.save_bet("hugo", "maismilionaria", [1, 2, 3, 4, 5, 6], [1, 2], 1)
    core.add_banca("hugo", "Gasto", 6.0, f"Aposta #{bid} maismilionaria")
    core.editar_extra("hugo", bid, "1 2 3")
    assert core.banca_mes("hugo")[0] == 18.0


def test_checar_update(monkeypatch):
    monkeypatch.setattr(core, "_get", lambda url, t=15: {"tag_name": "v99.0.0", "html_url": "u", "body": "x"})
    assert core.checar_update()["versao"] == "99.0.0"
    monkeypatch.setattr(core, "_get", lambda url, t=15: {"tag_name": "v0.1.0"})
    assert core.checar_update() is None
    def falha(*a):
        raise OSError
    monkeypatch.setattr(core, "_get", falha)
    assert core.checar_update() is None


def test_aposta_com_mais_dezenas_soma_todas_as_simples():
    # Mega com 7 dezenas e 5 acertos = 2 quinas + 5 quadras; +Milionária com 3 trevos e 2 acertados
    _salva("megasena", 70, range(1, 7), [["6 acertos", 0], ["5 acertos", 100.0], ["4 acertos", 10.0]])
    core.save_bet("gabi", "megasena", [1, 2, 3, 4, 5, 50, 51], [], 70)
    b = core.check_bets("gabi")[0]
    assert b["ganhos"] == 2 * 100 + 5 * 10
    _salva("maismilionaria", 71, range(1, 7), [["4 acertos + 2 trevos", 50.0],
                                               ["4 acertos + 1 ou nenhum trevo", 20.0]], trevos=[1, 2])
    core.save_bet("gabi", "maismilionaria", [1, 2, 3, 4, 40, 41], [1, 2, 3], 71)
    # pares de trevos: (1,2) acerta 2; (1,3) e (2,3) acertam 1
    assert core.check_bets("gabi")[0]["ganhos"] == 50 + 2 * 20


def test_sync_sem_espelho_com_concurso_faltando(monkeypatch):
    # espelho fora do ar e um concurso da Caixa falhando: não pode quebrar (log=print não tem .warning)
    def get(url, timeout=15):
        raise OSError("fora do ar")

    def fetch(jogo, k=""):
        if k == 498:
            raise OSError("timeout")
        return {"concurso": k or 500, "dezenas": ["1", "2", "3", "4", "5"], "data": "", "trevos": [], "premios": []}
    monkeypatch.setattr(core, "_get", get)
    monkeypatch.setattr(core, "fetch", fetch)
    msgs = []
    assert core.sync_draws("quina", 5, log=msgs.append) == 4
    assert any("não baixaram" in m for m in msgs)


def test_sync_completa_buracos_do_espelho_pela_caixa(monkeypatch):
    # espelho sem os concursos 2 e 4 (como na Timemania): a Caixa preenche
    def get(url, timeout=15):
        return [{"concurso": k, "dezenas": ["1", "2", "3", "4", "5"]} for k in (1, 3, 5)]

    pedidos = []

    def fetch(jogo, k=""):
        pedidos.append(k)
        return {"concurso": k, "dezenas": ["6", "7", "8", "9", "10"], "data": "", "trevos": [], "premios": []}
    monkeypatch.setattr(core, "_get", get)
    monkeypatch.setattr(core, "fetch", fetch)
    with core.db() as c:
        c.execute("DELETE FROM draws WHERE jogo='quina'")
    assert core.sync_draws("quina", log=lambda m: None) == 5
    assert sorted(pedidos) == [2, 4]
    assert [c for c, _ in core.load_draws("quina", 10)] == [5, 4, 3, 2, 1]


def test_fetch_tenta_os_dois_servidores_da_caixa(monkeypatch):
    urls = []

    def get(url, timeout=15):
        urls.append(url)
        if "servicebus2" in url:
            raise OSError("fora do ar")
        return {"numero": 7, "listaDezenas": ["01", "02", "03", "04", "05"]}
    monkeypatch.setattr(core, "_get", get)
    assert core.fetch("quina", 7)["concurso"] == 7
    assert "servicebus2" in urls[0] and "servicebus3" in urls[1]


def test_mes_ambiguo_e_texto_sobrando():
    import pytest
    with pytest.raises(ValueError, match="Março ou Maio"):
        core.parse_bet("diadesorte", "1 2 3 4 5 6 7 + ma")
    assert core.parse_bet("diadesorte", "1 2 3 4 5 6 7 + jul")[1] == ["Julho"]
    with pytest.raises(ValueError, match="nada depois"):
        core.parse_bet("megasena", "1 2 3 4 5 6 + 7 8")


def test_calendario_de_sorteios():
    from datetime import date
    qua = date(2026, 10, 28)
    assert core.proximo_sorteio("megasena", qua) == date(2026, 10, 29)
    assert core.proximo_sorteio("maismilionaria", qua) == qua
    assert "Mega-Sena" in core.texto_calendario(qua)


def test_sync_corrige_dezenas_registra_e_recusa_invalidos(monkeypatch):
    with core.db() as c:
        c.execute("DELETE FROM draws WHERE jogo='quina'")
    _salva("quina", 1, [1, 2, 3, 4, 5], [])
    _salva("quina", 2, [6, 7, 8, 9, 10], [])
    fonte = [{"concurso": 1, "dezenas": ["1", "2", "3", "4", "50"]},      # corrigido pela fonte
             {"concurso": 2, "dezenas": ["10", "9", "8", "7", "6"]},      # mesma coisa, outra ordem
             {"concurso": 3, "dezenas": ["1", "1", "2", "3", "4"]},       # repetida: recusa
             {"concurso": 4, "dezenas": ["1", "2", "3", "4", "81"]}]      # fora de 1-80: recusa
    monkeypatch.setattr(core, "_get", lambda url, timeout=15: fonte)
    monkeypatch.setattr(core, "fetch", lambda j, k="": (_ for _ in ()).throw(OSError("sem rede")))
    msgs = []
    core.sync_draws("quina", log=msgs.append)
    assert dict(core.load_draws("quina", 10))[1] == [1, 2, 3, 4, 50]
    corr = core.db().execute("SELECT concurso, antes, depois FROM correcoes WHERE jogo='quina'").fetchall()
    assert [c[0] for c in corr] == [1] and json.loads(corr[0][1]) == [1, 2, 3, 4, 5]
    assert {c for c, _ in core.load_draws("quina", 10)} == {1, 2}
    assert any("2 resultado(s) inválido(s)" in m for m in msgs) and any("corrigido" in m for m in msgs)


def test_simular_soma_mes_da_sorte_com_as_faixas(monkeypatch):
    # aposta que sempre faz 4 acertos e sempre acerta o mês: ganha os dois prêmios
    r = {"preco": 2.5, "ev": 7.5, "linhas": [("4 acertos", 1.0, 5.0, 5.0), ("Mês da Sorte", 1.0, 2.5, 2.5)]}
    monkeypatch.setattr(core, "valor_esperado", lambda j: r)
    m = core.simular("diadesorte", 2.5, 1, n_sim=5)
    assert m["mediana"] == 52 * (5.0 + 2.5) - 52 * 2.5
