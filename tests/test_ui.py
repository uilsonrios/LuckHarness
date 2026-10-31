import time
import tkinter as tk

import pytest

import core
import luckharness as ui


@pytest.fixture
def app(monkeypatch):
    # sem rede nos testes: nada de baixar resultados nem procurar versão nova
    monkeypatch.setattr(ui.App, "auto_update", lambda self: None)
    monkeypatch.setattr(ui.App, "ver_update", lambda self: None)
    monkeypatch.setattr(ui.App, "assistente", lambda self: None)
    monkeypatch.setattr(ui.messagebox, "showinfo", lambda *a, **k: None)
    monkeypatch.setattr(ui.messagebox, "showerror", lambda *a, **k: None)
    try:
        core.register("ui_teste", "segredo9")
    except core.sqlite3.IntegrityError:
        pass
    try:
        a = ui.App("ui_teste")
    except tk.TclError as e:
        pytest.skip(f"sem interface gráfica: {e}")
    a.withdraw()
    yield a
    a.destroy()


def _espera(app, widget, timeout=20):
    fim = time.monotonic() + timeout
    while time.monotonic() < fim:
        app.update()
        txt = widget.get("1.0", "end")
        if "Calculando" not in txt:
            return txt
        time.sleep(0.05)
    raise AssertionError("tarefa em background não terminou")


def test_todas_as_abas_abrem(app):
    nomes = [app.nb.tab(i, "text") for i in range(app.nb.index("end"))]
    assert {"Hoje", "Análise", "Minhas apostas", "Otimizador", "Retorno", "Laboratório"} <= set(nomes)
    for i in range(len(nomes)):
        app.nb.select(i)
        app.update()


def test_otimizador_tabela_de_chances(app):
    app.bg_otim(ui.tabela_chances)
    txt = _espera(app, app.o_out)
    assert "lotofacil" in txt and "1 em 9,4" in txt


def test_salvar_aposta_e_editar(app, monkeypatch):
    app.jogo.set("diadesorte")
    app.bet_in.insert(0, "1 2 3 4 5 6 7 + outubro")
    app.lanca.set(False)
    app.add_bet()
    fim = time.monotonic() + 10
    while not app.tree.get_children() and time.monotonic() < fim:
        app.update()
        time.sleep(0.05)
    item = app.tree.get_children()[0]
    assert "Outubro" in app.tree.set(item, "dezenas")
    app.tree.selection_set(item)
    monkeypatch.setattr(ui.simpledialog, "askstring", lambda *a, **k: "3")
    app.edit_bet()
    assert core.check_bets("ui_teste")[0]["trevos"] == ["Março"]


def test_contexto_da_ia_nao_quebra(app):
    assert "FATOS DO APP" in app.context()


def test_contexto_da_ia_cabe_no_limite(app):
    app.last_report = "x" * 50_000
    app.otim_txt = "y" * 50_000
    app.ev_txt = "z" * 50_000
    app.ranking = "w" * 50_000
    app.last_sugs = [("lotofacil", list(range(1, 16)))] * 6
    ctx = app.context()
    assert len(ctx) < sum(app.CTX_LIMITES.values()) + 3000   # ~4 caracteres por token: cabe em 8192 tokens


def test_aposta_da_semana_monta_e_registra(app, monkeypatch):
    assert app.nb.tab(0, "text") == "Minha aposta da semana"
    app.jogo.set("lotofacil")
    assert app.jogo.get() == "lotofacil" and ui.ttk.Combobox.get(app.jogo) == "Lotofácil"
    app.s_val.delete(0, "end"); app.s_val.insert(0, "10,50")
    monkeypatch.setattr(ui, "sugestoes", lambda j, q, n=None: [list(range(1 + i, 16 + i)) for i in range(q)])
    app.montar_semana()
    fim = time.monotonic() + 20
    while not app.s_cards and time.monotonic() < fim:
        app.update()
        time.sleep(0.05)
    assert len(app.s_cards) == 3
    antes = core.banca_mes("ui_teste")[0]
    app.apostei()
    assert core.banca_mes("ui_teste")[0] == antes + 10.5


def test_volante_marca_dezenas(app):
    app.jogo.set("megasena")
    app.bet_in.delete(0, "end"); app.bet_in.insert(0, "01 02")
    app.volante()
    w = app.janela_volante
    grade = [c for c in w.winfo_children() if isinstance(c, tk.Frame)][0]
    bts = {int(b.cget("text")): b for b in grade.winfo_children()}
    for x in (10, 20, 30, 40):
        bts[x].invoke()
    bts[1].invoke()   # desmarca
    [b for b in w.winfo_children()[-1].winfo_children() if b.cget("text") == "Usar estas dezenas"][0].invoke()
    assert app.bet_in.get() == "02 10 20 30 40"



_ASSISTENTE = ui.App.assistente   # o fixture desliga o assistente; este teste chama o original


def test_assistente_salva_preferencias(app):
    _ASSISTENTE(app)
    w = app.janela_assistente
    fr = w.winfo_children()[0]
    g = [c for c in fr.winfo_children() if c.winfo_children() and isinstance(c.winfo_children()[0], ui.ttk.Label)][0]
    jb, orc, lim = [c for c in g.winfo_children() if isinstance(c, ui.ttk.Entry)]
    jb.set("quina")
    orc.delete(0, "end"); orc.insert(0, "25")
    lim.insert(0, "200")
    [b for b in fr.winfo_children()[-1].winfo_children() if b.cget("text") == "Começar"][0].invoke()
    pr = core.load_prefs()
    assert pr["jogo"] == "quina" and pr["orcamento"] == 25 and pr["limite_ui_teste"] == 200
    assert pr["assistente_ui_teste"] and app.jogo.get() == "quina"


def test_aposta_minima_gera_cartoes(app, monkeypatch):
    import otimizador as o
    monkeypatch.setattr(o, "premios_medios", lambda j, n=100: (
        {"14 acertos": 1800, "15 acertos": 1_500_000, "13 acertos": 35} if j == "lotofacil" else {}, {}))
    app.o_min.delete(0, "end"); app.o_min.insert(0, "3,50")
    app.o_alvo.set("R$ 1 mil")
    app.run_minima()
    txt = _espera(app, app.o_out)
    assert "APOSTA MÍNIMA" in txt and "lotofacil" in txt
    assert len(app.otim_cards) == 1 and app.otim_plan["jogo"] == "lotofacil"


def test_aposta_da_semana_respeita_o_limite_do_mes(app, monkeypatch):
    # pediu R$ 50 com limite de R$ 7 sobrando no mês: monta 2 cartões, não 14
    app.jogo.set("lotofacil")
    g = core.banca_mes("ui_teste")[0]
    core.save_prefs(**{"limite_ui_teste": g + 7})
    app.s_val.delete(0, "end"); app.s_val.insert(0, "50")
    monkeypatch.setattr(ui, "sugestoes", lambda j, q, n=None: [list(range(1 + i, 16 + i)) for i in range(q)])
    try:
        app.montar_semana()
        fim = time.monotonic() + 20
        while not app.s_cards and time.monotonic() < fim:
            app.update()
            time.sleep(0.05)
        assert len(app.s_cards) == 2
        assert "limite do mês" in app.s_out.get("1.0", "end")
    finally:
        core.save_prefs(**{"limite_ui_teste": 0})
