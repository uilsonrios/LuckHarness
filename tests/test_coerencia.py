"""App, Otimizador e IA precisam dizer a mesma coisa."""
import io
import json
import random

import core
import otimizador as o


def test_sugestoes_sao_as_mesmas_do_otimizador():
    a = o.sugestoes("lotofacil", 5, 15, random.Random(3))
    b = o.cartoes_espalhados("lotofacil", 5, 15, o.pesos_popularidade("lotofacil"), random.Random(3))
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
