"""App, Otimizador e IA precisam dizer a mesma coisa."""
import io
import json
import os
import random

import core
import otimizador as o


def test_sugestoes_sao_as_mesmas_do_otimizador():
    a = o.sugestoes("lotofacil", 5, 15, random.Random(7))
    b = o.comparar_cobertura("lotofacil", 5, 15, n_sim=10, seed=7)[0]   # o Otimizador usa a mesma função
    assert a == b
    assert not hasattr(core, "suggest")   # a sugestão antiga (quentes/atrasadas) não existe mais


def test_checagem_pega_contradicoes():
    ruins = ["Jogue os números quentes, eles vêm saindo muito.",
             "O 13 está para sair, aposte nele.",
             "Esta estratégia garante o lucro no longo prazo.",
             "Dá para prever os números do próximo sorteio com padrões."]
    for t in ruins:
        assert o.checar_resposta(t, "megasena"), t


def test_checagem_nao_acusa_resposta_correta():
    boa = ("A Lotofácil premia 1 em 9,4 apostas e devolve em média 49%. Use os cartões do Otimizador, "
           "que espalham os números. O fechamento dá garantia condicional: se seus números saírem.")
    assert o.checar_resposta(boa, "lotofacil") == []


def test_checagem_de_numeros_inventados():
    app = [[1, 2, 3, 4, 5, 6]]
    assert o.checar_resposta("Aposte em 01 02 03 04 05 06.", "megasena", app) == []
    assert o.checar_resposta("Aposte em 07 13 22 31 44 58.", "megasena", app)


def test_fatos_vao_para_a_ia():
    f = o.fatos_app("megasena")
    assert "FATOS DO APP" in f and "quentes" in f and "1 em 2.298" in f


def test_ia_com_temperatura_baixa(monkeypatch):
    enviado = {}

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def falso(req, timeout=None):
        enviado.update(json.loads(req.data))
        return Resp(b'{"message": {"content": "ok"}}\n')
    monkeypatch.setattr(core.urllib.request, "urlopen", falso)
    assert core.ask_ollama("m", [{"role": "user", "content": "oi"}], lambda t: None) == "ok"
    assert enviado["options"]["temperature"] <= 0.3
    assert "FATOS DO APP" in enviado["messages"][0]["content"]


def test_checagem_aceita_explicacao_que_nega():
    t = ("O app testou números quentes, frios e redes neurais e não encontrou efeito. "
         "Mesmo usando números quentes, o retorno esperado é negativo. Não há números que estão para sair.")
    assert o.checar_resposta(t, "lotofacil") == []


def test_ollama_mostra_motivo_do_erro(monkeypatch):
    import urllib.error

    def recusa(req, timeout=None):
        raise urllib.error.HTTPError("u", 400, "Bad Request", {},
                                     io.BytesIO(b'{"error":"request exceeds the available context size"}'))
    monkeypatch.setattr(core.urllib.request, "urlopen", recusa)
    import pytest
    with pytest.raises(RuntimeError, match="memória de contexto"):
        core.ask_ollama("m", [{"role": "user", "content": "x"}], lambda t: None)


def test_treinar_todos_pula_jogo_sem_novidade(monkeypatch):
    chamadas = []
    monkeypatch.setattr(o, "treinar_popularidade",
                        lambda j: chamadas.append(j) or {"confiavel": True, "r2_teste": 0.5})
    monkeypatch.setattr(o, "_modelo", lambda j: {"ok": 1})
    monkeypatch.setattr(o, "_treinados", {})
    monkeypatch.setattr(o, "_pop_path", lambda j: os.devnull)   # não grava modelo falso de verdade
    o.treinar_todos(lambda t: None)
    n = len(chamadas)
    o.treinar_todos(lambda t: None)          # nada mudou: não treina de novo
    assert n > 0 and len(chamadas) == n
    o.treinar_todos(lambda t: None, forcar=True)
    assert len(chamadas) == 2 * n


def test_avaliar_aposta_usa_modelo_quando_ha():
    com = core.avaliar_aposta("megasena", [1, 2, 3, 4, 5, 6], [], r={"ev": 1, "retorno": .2}, fator=0.8)[0]
    sem = core.avaliar_aposta("megasena", [1, 2, 3, 4, 5, 6], [], r={"ev": 1, "retorno": .2})[0]
    assert "modelo treinado" in com and "/100" not in com
    assert "regras simples" in sem


def test_sugestoes_preferem_conjunto_que_divide_menos(monkeypatch):
    """Com modelo de popularidade, entre conjuntos igualmente espalhados fica o que divide menos o prêmio."""
    N = 25
    pesos = {i: (0.05 if i <= 12 else -0.05) for i in range(1, N + 1)}
    mod = {"confiavel": True, "pesos": {str(k): v for k, v in pesos.items()}, "coef": [pesos[i] for i in range(1, N + 1)],
           "cfg": {"padroes": False, "modelo": "ridge"}, "norm": ([], []), "D": N, "anterior": []}
    monkeypatch.setattr(o, "_modelo", lambda j: mod)
    sug = o.sugestoes("lotofacil", 5, 15, random.Random(1))
    media = lambda cs: sum(o._prever("lotofacil", mod, {"dz": c, "ant": [], "trevos": []}) for c in cs) / len(cs)
    um_so = o.cartoes_espalhados("lotofacil", 5, 15, pesos, random.Random(1))
    assert media(sug) <= media(um_so)
    uso = [sum(x in c for c in sug) for x in range(1, N + 1)]
    assert max(uso) - min(uso) <= 1          # continua espalhado


def test_treino_simultaneo_nao_repete(monkeypatch):
    import threading
    import time
    chamadas = []

    def lento(j):
        chamadas.append(j)
        time.sleep(0.01)
        return {"confiavel": True, "r2_teste": 0.5}
    monkeypatch.setattr(o, "treinar_popularidade", lento)
    monkeypatch.setattr(o, "_treinados", {})
    monkeypatch.setattr(o, "_modelo", lambda j: {"ok": 1})
    monkeypatch.setattr(o, "_pop_path", lambda j: os.devnull)
    ts = [threading.Thread(target=o.treinar_todos, args=(lambda t: None,)) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(chamadas) == len(set(chamadas))   # cada jogo treinado uma vez só


SUGS = [([1, 2, 3, 8, 10, 12, 16, 17, 18, 19, 20, 21, 23, 24, 25], 0.86),
        ([3, 4, 6, 9, 10, 11, 12, 13, 14, 15, 16, 17, 19, 20, 25], 0.78),
        ([1, 2, 5, 7, 8, 10, 13, 14, 15, 18, 21, 22, 23, 24, 25], 1.08)]


def test_checagem_pega_recomendacao_do_cartao_que_divide_mais():
    resp = ("Qual é a melhor escolha?\n> Recomendação final: Aposte **01 02 05 07 08 10 13 14 15 18 21 22 23 24 25**\n"
            "Tem divisão do prêmio de 1,08x a média.")
    av = o.checar_resposta(resp, "lotofacil", [c for c, _ in SUGS], SUGS)
    assert any("divide MAIS" in a and "03 04 06 09" in a for a in av)


def test_checagem_aceita_recomendacao_certa_e_cartao_antigo_citado():
    resp = ("A melhor escolha é 03 04 06 09 10 11 12 13 14 15 16 17 19 20 25, com 0,78x.\n"
            "Sobre a sua aposta 01 02 04 06 11 12 13 14 16 17 18 20 21 22 23: mesma chance de acerto.")
    antiga = [1, 2, 4, 6, 11, 12, 13, 14, 16, 17, 18, 20, 21, 22, 23]
    assert o.checar_resposta(resp, "lotofacil", [c for c, _ in SUGS] + [antiga], SUGS) == []


def test_fatos_sugestoes_ja_ordenados(monkeypatch):
    fat = {tuple(c): f for c, f in SUGS}
    monkeypatch.setattr(o, "fator_divisao", lambda j, c, t=(): fat[tuple(c)])
    txt = o.fatos_sugestoes("lotofacil", [c for c, _ in SUGS])
    assert txt.index("0.78x") < txt.index("0.86x") < txt.index("1.08x")
    assert "MELHOR CARTÃO" in txt and "03 04 06 09 10 11 12 13 14 15 16 17 19 20 25 (0.78x)" in txt


def test_checagem_olha_so_o_primeiro_cartao_recomendado():
    resp = ("Recomendo 03 04 06 09 10 11 12 13 14 15 16 17 19 20 25.\n"
            "Outras sugestões: 01 02 05 07 08 10 13 14 15 18 21 22 23 24 25.")
    assert o.checar_resposta(resp, "lotofacil", [c for c, _ in SUGS], SUGS) == []
