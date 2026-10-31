"""Integridade da bateria de aprendizado sequencial (auditoria da rede, 2ª etapa)."""
import json

import numpy as np
import pytest

ap = pytest.importorskip("aprendizado")


def _serie(T=900, seed=1):
    s = ap.sintetica("nulo", seed=seed, T=T)
    s["ini"], s["aq"] = 50, 100
    return s


@pytest.mark.parametrize("modelo", ap.REFERENCIAS + [ap.Rede])
def test_previsao_so_usa_o_passado(modelo):
    # mudar todo o futuro (concursos >= t) não pode mudar a previsão feita para t
    s1, t = _serie(), 400
    s2 = {**s1, "Y": s1["Y"].copy()}
    s2["Y"][t:] = np.random.default_rng(5).permutation(s2["Y"][t:].T).T
    previsoes = []
    for s in (s1, s2):
        m = modelo(s, 3)
        for k in range(t):
            m.atualizar(k)
        previsoes.append(m.prever(t))
    assert np.allclose(previsoes[0], previsoes[1])


def test_registro_encadeado_detecta_alteracao_e_reproduz(tmp_path):
    s = _serie()
    arq = tmp_path / "rede.jsonl"
    r = ap.rodar(s, ap.Rede, seed=4, registro=str(arq))
    assert ap.verificar_cadeia(str(arq))
    assert ap.rodar(s, ap.Rede, seed=4)["cadeia"] == r["cadeia"]           # mesma semente, mesmas previsões
    linhas = arq.read_text(encoding="utf8").splitlines()
    i = next(k for k, li in enumerate(linhas) if "previsao" in li)
    reg = json.loads(linhas[i])
    reg["previsao"][0] = (reg["previsao"][0] + 1) % 25                     # "corrige" uma previsão antiga
    linhas[i] = json.dumps(reg)
    arq.write_text("\n".join(linhas), encoding="utf8")
    assert not ap.verificar_cadeia(str(arq))
    # a previsão é gravada antes do resultado do mesmo concurso
    primeiro = [json.loads(li) for li in linhas[:2]]
    assert "previsao" in primeiro[0] and "resultado" in primeiro[1]


def test_expectativa_exata_por_modalidade():
    assert ap.referencia({"Y": np.zeros((1, 60)), "sorteados": 6, "escolhe": 6})[0] == pytest.approx(0.6)
    assert ap.referencia({"Y": np.zeros((1, 100)), "sorteados": 20, "escolhe": 50})[0] == pytest.approx(10.0)
    assert ap.referencia({"Y": np.zeros((1, 70)), "grupos": [[0]], "sorteados": 7, "escolhe": 7})[0] == 0.7


def test_aprende_padrao_informado_e_nao_inventa_no_acaso():
    forte = ap.sintetica("repetir", 1.0, seed=7, T=1500)
    r = ap.estudar(forte, sementes=2, selecionar=False, log=lambda *_: None)
    assert r["rede"]["z"] > 5 and r["reproduz"]
    nulo = ap.sintetica("nulo", seed=3, T=1500)
    r0 = ap.estudar(nulo, sementes=2, selecionar=False, log=lambda *_: None)
    assert abs(r0["rede"]["z"]) < 4
