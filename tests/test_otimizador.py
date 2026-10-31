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
        o.otimizar(1, "frequente", jogos=["supersete"])
    tipos = {r["tipo"] for r in o.opcoes(30, ["lotofacil", "diadesorte"])}
    assert tipos == {"simples", "bolão", "fechamento"}


def test_meses_diferentes_garantem_mes_da_sorte():
    assert o.prob_cartoes("diadesorte", 0.01, 12) == pytest.approx(1)
    assert o.prob_cartoes("diadesorte", 0.0, 6) == pytest.approx(0.5)


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


def test_extras_espalhados_cobrem_todos_os_meses():
    ex = o.extras_espalhados("diadesorte", 12, random.Random(1))
    assert sorted(e[0] for e in ex) == sorted(core.MESES)
    tv = o.extras_espalhados("maismilionaria", 15, random.Random(1))
    assert len({tuple(t) for t in tv}) == 15


def test_testar_no_passado_paga_premios_reais():
    with core.db() as c:
        c.execute("DELETE FROM draws WHERE jogo='diadesorte'")
        for i in range(30):
            meta = {"premios": [["4 acertos", 5.0], ["Mês da Sorte", 2.5]], "trevos": [],
                    "mes": core.MESES[i % 12], "arrecadado": 1}
            c.execute("INSERT INTO draws(jogo, concurso, dezenas, meta) VALUES(?,?,?,?)",
                      ("diadesorte", i + 1, json.dumps([1, 2, 3, 4, 5, 6, 7]), json.dumps(meta)))
    r = o.testar_no_passado("diadesorte", 12, 7, concursos=30)
    esp = r["esp"]
    assert esp["jogados"] == 30 and esp["gasto"] == 30 * 12 * 2.5
    assert esp["com_premio"] == 30                     # 12 meses diferentes: Mês da Sorte garantido
    assert esp["faixas"]["Mês da Sorte"] == 30
    assert "TESTE NO PASSADO" in o.texto_teste(r)


def test_ganho_cartao_com_mais_dezenas():
    meta = {"premios": [["6 acertos", 0], ["5 acertos", 1000.0], ["4 acertos", 100.0]]}
    # 7 dezenas com 5 acertos = 2 quinas + 5 quadras
    g, _ = o._ganho_cartao("megasena", [1, 2, 3, 4, 5, 50, 51], [], [1, 2, 3, 4, 5, 60], meta)
    assert g == 2 * 1000 + 5 * 100


def test_v2_aprende_padrao_de_escolha():
    """Público que joga sequências: a v2 (com padrões) deve vencer a v1 no teste."""
    rng = random.Random(5)
    N, K, mn, fx, preco = core.JOGOS["megasena"]
    vendas = 10_000_000
    esperado = vendas / preco * core.hypergeom(N, K, mn, 4)
    with core.db() as c:
        c.execute("DELETE FROM draws WHERE jogo='megasena'")
        for i in range(1500):
            dz = sorted(rng.sample(range(1, N + 1), K))
            seq = sum(b - a == 1 for a, b in zip(dz, dz[1:]))
            g = esperado * pow(2.718281828, 0.4 * seq + rng.gauss(0, 0.1))
            meta = {"premios": [["4 acertos", 10]], "ganhadores": [int(g)], "arrecadado": vendas}
            c.execute("INSERT INTO draws(jogo, concurso, dezenas, meta) VALUES(?,?,?,?)",
                      ("megasena", i + 1, json.dumps(dz), json.dumps(meta)))
    m = o.treinar_popularidade("megasena")
    assert m["cfg"]["padroes"] and m["r2_teste"] > m["r2_base"] + 0.2
    assert o.fator_divisao("megasena", [1, 2, 3, 4, 5, 6]) > o.fator_divisao("megasena", [3, 14, 25, 36, 47, 58])
    r = o.estudo_acumulados("megasena")
    assert r["casos"] == []   # sem ganhadores da sena nos dados sintéticos


def test_popularidade_supersete_e_maismilionaria():
    """Jogos novos no modelo: Super Sete (coluna x dígito) e +Milionária (números + trevos)."""
    rng = random.Random(9)
    with core.db() as c:
        c.execute("DELETE FROM draws WHERE jogo IN ('supersete', 'maismilionaria')")
        for i in range(800):
            ss = [rng.randrange(10) for _ in range(7)]
            g = 10_000 * pow(2.718281828, 0.3 * (ss[0] == 7) + rng.gauss(0, 0.05))
            meta = {"premios": [["3 acertos", 5]], "ganhadores": [int(g)], "arrecadado": 2.5 * 10_000 / 0.0081}
            c.execute("INSERT INTO draws(jogo, concurso, dezenas, meta) VALUES(?,?,?,?)",
                      ("supersete", i + 1, json.dumps(ss), json.dumps(meta)))
            mm = sorted(rng.sample(range(1, 51), 6))
            tv = sorted(rng.sample(range(1, 7), 2))
            g = 50_000 * pow(2.718281828, 0.3 * (1 in tv) + rng.gauss(0, 0.05))
            meta = {"premios": [["2 acertos + 1 trevo", 6]], "ganhadores": [int(g)], "trevos": tv,
                    "arrecadado": 6.0 * 50_000 / (0.1 * 8 / 15)}
            c.execute("INSERT INTO draws(jogo, concurso, dezenas, meta) VALUES(?,?,?,?)",
                      ("maismilionaria", i + 1, json.dumps(mm), json.dumps(meta)))
    m = o.treinar_popularidade("supersete", rapido=True)
    assert m["confiavel"]
    assert o.fator_divisao("supersete", [7, 0, 0, 0, 0, 0, 0]) > o.fator_divisao("supersete", [3, 0, 0, 0, 0, 0, 0])
    m = o.treinar_popularidade("maismilionaria", rapido=True)
    assert m["confiavel"]
    assert (o.fator_divisao("maismilionaria", [1, 2, 3, 4, 5, 6], [1, 2]) >
            o.fator_divisao("maismilionaria", [1, 2, 3, 4, 5, 6], [3, 4]))


def test_chances_dos_cartoes():
    cards = [[1, 2, 3, 4, 7, 8, 10, 12, 14, 16, 17, 18, 20, 23, 24],
             [2, 3, 4, 5, 6, 9, 11, 13, 14, 15, 18, 19, 21, 22, 25]]
    t = o.texto_chances_cartoes("lotofacil", cards)
    assert "15 acertos: 1 em 3.268.760" in t and "1 em 1.634.380" in t
    m = o.texto_chances_cartoes("megasena", [[1, 2, 3, 4, 5, 6], [7, 8, 9, 10, 11, 12]])
    assert "algum prêmio: 1 em 1.149" in m


def test_cobertura_duplasena_conta_os_dois_sorteios():
    import random as rd
    p1 = o.prob_premio("duplasena", 6, extra=False)
    sim = o.simular_cobertura("duplasena", [[1, 2, 3, 4, 5, 6]], 40000, rd.Random(3))
    assert sim == pytest.approx(p1, rel=0.1)


@pytest.mark.parametrize("txt", [
    "O modelo de popularidade consegue prever com 63% de acerto se a combinação de números é muito jogada.",
    "O app usa o modelo para prever quais números o público mais joga.",
    "É impossível prever os números sorteados.",
    "Ninguém consegue prever o resultado.",
    "Fechamentos oferecem garantia de prêmio se 4 dos seus números saírem."])
def test_checagem_sem_alarme_falso(txt):
    assert o.checar_resposta(txt, "megasena") == []


def test_checagem_ainda_pega_previsao_e_garantia():
    assert o.checar_resposta("Use o modelo para prever o próximo resultado.", "megasena")
    assert o.checar_resposta("Este método dá garantia de lucro.", "megasena")


@pytest.mark.parametrize("txt", [
    "**Estratégias sem efeito**\n- Números quentes/frios/atrasados\n- Modelos de previsão (incluindo redes neurais)",
    "Testado sem efeito:\n- números quentes\n- números atrasados",
    "Tudo que parece funcionar (números quentes, repetição) é indistinguível do acaso.",
    "Nada garante lucro.",
    "### Observações:\n- **Números quentes, frios, atrasados**: Não afetam a chance de acerto."])
def test_checagem_respostas_reais_corretas(txt):
    # respostas reais do qwen3:4b que estavam certas e eram acusadas
    assert o.checar_resposta(txt, "quina") == []


def test_checagem_lista_sem_negacao_ainda_acusa():
    assert o.checar_resposta("Minhas dicas:\n- jogue os números quentes", "quina")
    assert o.checar_resposta("Dica:\n- **Números quentes**: jogue estes.", "quina")


def test_frequente_nao_premia_so_o_mes_da_sorte():
    for orc in (30, 100):
        top = o.otimizar(orc, "frequente")[0]
        assert not (top["jogo"] == "diadesorte" and top["p_dezenas"] < 0.5)
        assert top["p_dezenas"] <= top["p_semana"]


def test_teste_no_passado_treina_popularidade_so_com_o_passado(monkeypatch):
    with core.db() as c:
        c.execute("DELETE FROM draws WHERE jogo='diadesorte'")
        for i in range(40):
            meta = {"premios": [["4 acertos", 5.0]], "trevos": [], "mes": core.MESES[i % 12]}
            c.execute("INSERT INTO draws(jogo, concurso, dezenas, meta) VALUES(?,?,?,?)",
                      ("diadesorte", i + 1, json.dumps([1, 2, 3, 4, 5, 6, 7]), json.dumps(meta)))
    vistos = []
    monkeypatch.setattr(o, "pesos_popularidade", lambda j: {i: 0.0 for i in range(1, 32)})
    monkeypatch.setattr(o, "pesos_ate", lambda j, ate: vistos.append(ate) or {i: 0.0 for i in range(1, 32)})
    r = o.testar_no_passado("diadesorte", 3, 7, concursos=30)
    assert vistos == [11] and r["pop_ate"] == 11   # testa 11..40, o modelo só vê até o 10
    assert "anteriores ao 11" in o.texto_teste(r)


def test_cobertura_considera_sobreposicao_dos_cartoes():
    # cartões espalhados premiam juntos mais do que a conta de cartões independentes
    sim = o.cobertura_dezenas("lotofacil", 10)
    pd = sum(core.hypergeom(25, 15, 15, h) for h in core.JOGOS["lotofacil"][3])
    assert sim > o.prob_cartoes("lotofacil", pd, 10, extra=False) + 0.03
    simples = [x for x in o.opcoes(35, ["lotofacil"]) if x["tipo"] == "simples" and x["n"] == 15]
    assert simples[0]["p_dezenas"] == sim


def test_bolao_segue_as_regras_da_caixa():
    assert core.cotas_bolao("lotofacil", 16, 56, 6) == 10        # 10 cotas de R$ 5,60
    assert core.cotas_bolao("lotofacil", 17, 476, 6) is None     # precisaria de 80 cotas: o máximo é 40
    assert core.cotas_bolao("megasena", 8, 168, 6) is None       # cota mínima de R$ 8,00
    assert core.cotas_bolao("megasena", 8, 168, 10) == 17
    assert core.cotas_bolao("megasena", 6, 6, 6) is None         # uma aposta mínima sozinha não vira bolão
    for j in core.BOLAO:
        for b in o._opcoes_bolao(j, 30, None):
            cota_min, maximo = core.BOLAO[j][b["n"]]
            assert 2 <= b["cotas"] <= maximo and cota_min - 1e-9 <= b["gasto"] <= 30


MEDIANAS = {"lotofacil": {"15 acertos": 1_500_000, "14 acertos": 1800, "13 acertos": 35, "12 acertos": 14,
                          "11 acertos": 7},
            "megasena": {"6 acertos": 50_000_000, "5 acertos": 45_000, "4 acertos": 1000}}


def test_aposta_minima_mira_o_premio_escolhido(monkeypatch):
    monkeypatch.setattr(o, "premios_medios", lambda j, n=100: (MEDIANAS.get(j, {}), {}))
    monkeypatch.setattr(o, "valor_esperado", lambda j: {"retorno": 0.4})
    # R$ 3,50 e prêmio de R$ 1 mil: só a Lotofácil cabe, e chega lá com 14 ou 15 acertos
    rk = o.aposta_minima(3.5, 1000)
    assert [r["jogo"] for r in rk] == ["lotofacil"]
    assert rk[0]["h_min"] == "14+" and rk[0]["p"] == pytest.approx(151 / 3_268_760)
    # R$ 1 milhão com até R$ 10: 1/17 de um bolão de 8 dezenas da Mega tem 28 vezes a chance da aposta simples
    # (com 9 dezenas seriam 51 cotas e a parte da sena não chega a R$ 1 milhão)
    mega = next(r for r in o.aposta_minima(10, 1e6) if r["jogo"] == "megasena")
    assert (mega["tipo"], mega["n"], mega["cotas"]) == ("bolão", 8, 17)
    assert mega["p"] == pytest.approx(28 / 50_063_860)
    assert "APOSTA MÍNIMA" in o.texto_aposta_minima(10, 1e6, [mega])
    with pytest.raises(ValueError):
        o.aposta_minima(3.5, 1e9)


def test_cartoes_dividem_menos_sem_perder_cobertura(monkeypatch):
    # modelo de mentira: quanto maiores os números, mais "popular" o cartão
    monkeypatch.setattr(o, "_prever", lambda j, m, a: sum(a["dz"]) / 100)
    for jogo, k in (("megasena", 5), ("lotofacil", 4)):
        base = o.cartoes_espalhados(jogo, k, rng=random.Random(3))
        novos = o._menos_divididos(jogo, base, {}, random.Random(4))
        uso = [sum(x in c for c in novos) for x in range(1, o.JOGOS[jogo][0] + 1)]
        uso0 = [sum(x in c for c in base) for x in range(1, o.JOGOS[jogo][0] + 1)]
        assert max(uso) - min(uso) <= max(uso0) - min(uso0)          # números usados por igual, como antes
        s, s0 = o._sobreposicao(novos), o._sobreposicao(base)
        assert s[0] <= s0[0] and s[1] <= s0[1]                       # cartões não ficam mais parecidos
        assert all(len(set(c)) == o.JOGOS[jogo][2] for c in novos)
        assert sum(map(sum, novos)) < sum(map(sum, base))            # e o conjunto divide menos o prêmio
