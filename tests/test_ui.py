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
    assert {"Análise", "Minhas apostas", "Otimizador", "Retorno", "Laboratório"} <= set(nomes)
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
