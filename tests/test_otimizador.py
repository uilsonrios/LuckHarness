import json
import random

import pytest

import core
import otimizador as o


def test_prob_premio_valores_conhecidos():
    # Mega-Sena: quadra ou mais = 1 em 2.298 (tabela oficial: quadra 1 em 2.332)
    assert 1 / o.prob_premio("megasena") == pytest.approx(2297.6, abs=0.5)
    assert 1 / o.prob_premio("lotofacil") == pytest.approx(9.4, abs=0.1)
    assert 1 / o.prob_principal("megasena") == pytest.approx(50_063_860)
    # +Milionária: 2 e 3 acertos exigem trevo, então fica bem abaixo de "1 em 6,9"
    assert 1 / o.prob_premio("maismilionaria") > 10
    # mais dezenas => mais chance
    assert o.prob_premio("megasena", 8) > o.prob_premio("megasena", 6)


def test_otimizar_respeita_orcamento():
    rk = o.otimizar(30, "frequente", jogos=["megasena", "lotofacil", "quina"])
    assert all(r["gasto"] <= 30 for r in rk)
    assert rk[0]["jogo"] == "lotofacil"
    assert rk == sorted(rk, key=lambda r: r["p_semana"], reverse=True)
    with pytest.raises(ValueError):
        o.otimizar(1, "frequente", jogos=["megasena"])


def test_cartoes_espalhados_usam_numeros_por_igual():
    cards = o.cartoes_espalhados("megasena", 10, rng=random.Random(1))
    assert len(cards) == 10 and all(len(set(c)) == 6 and all(1 <= x <= 60 for x in c) for c in cards)
    uso = [sum(x in c for c in cards) for x in range(1, 61)]
    assert max(uso) - min(uso) <= 1          # 60 números, 60 posições: cada um uma vez
    ss = o.cartoes_espalhados("supersete", 3, rng=random.Random(1))
    assert all(len(c) == 7 and all(0 <= d <= 9 for d in c) for c in ss)


def test_espalhar_aumenta_chance_de_premiar():
    _, _, p_esp, p_ale = o.comparar_cobertura("lotofacil", 10, n_sim=4000)
    assert p_esp > p_ale


def _concursos(jogo, n, populares, efeito, ruido, seed):
    """Concursos sintéticos: quando saem números 'populares', sai mais gente ganhando."""
    rng = random.Random(seed)
    N, K, mn, fx, preco = core.JOGOS[jogo]
    faixa = min(h for h in fx if h > 0)
    vendas = 10_000_000
    esperado = vendas / preco * core.hypergeom(N, K, mn, faixa)
    with core.db() as c:
        c.execute("DELETE FROM draws WHERE jogo=?", (jogo,))
        for i in range(n):
            dz = sorted(rng.sample(range(1, N + 1), K))
            g = esperado * pow(2.718281828, efeito * sum(x in populares for x in dz) + rng.gauss(0, ruido))
            meta = {"premios": [[f"{faixa} acertos", 10]], "ganhadores": [int(g)], "arrecadado": vendas}
            c.execute("INSERT INTO draws(jogo, concurso, dezenas, meta) VALUES(?,?,?,?)",
                      (jogo, i + 1, json.dumps(dz), json.dumps(meta)))


def test_popularidade_aprende_padrao_real():
    populares = set(range(1, 11))
    _concursos("megasena", 1500, populares, 0.3, 0.1, 1)
    m = o.treinar_popularidade("megasena")
    assert m["confiavel"]
    w = m["pesos"]
    assert min(w[x] for x in populares) > max(w[x] for x in range(11, 61))
    assert o.fator_divisao("megasena", [1, 2, 3, 4, 5, 6]) > 1 > o.fator_divisao("megasena", [50, 51, 52, 53, 54, 55])


def test_popularidade_recusa_ruido():
    _concursos("quina", 1500, set(), 0, 0.5, 2)
    m = o.treinar_popularidade("quina")
    assert not m["confiavel"]
    assert o.pesos_popularidade("quina") is None
