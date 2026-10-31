"""luckHarness core - dados, autenticação, estatística, modelo e IA (sem interface)."""
import hashlib, json, logging, unicodedata, math, os, random, re, secrets, sqlite3, sys, urllib.error, urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from itertools import combinations

BASE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(os.environ.get("APPDATA", BASE), "luckHarness")
os.makedirs(DATA, exist_ok=True)
DB = os.path.join(DATA, "luckharness.db")
__version__ = "1.16.0"
logging.basicConfig(filename=os.path.join(DATA, "luckharness.log"), level=logging.INFO, encoding="utf8",
                    format="%(asctime)s %(levelname)s %(funcName)s: %(message)s")
log = logging.getLogger("luckharness")
AVATAR = os.path.join(BASE, "avatar.png")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen3:4b-instruct")
# API oficial da Caixa (o antigo servicodados.caixa.gov.br não resolve mais)
CAIXA = ("https://servicebus2.caixa.gov.br/portaldeloterias/api/{}/{}",
         "https://servicebus3.caixa.gov.br/portaldeloterias/api/{}/{}")
MIRROR = "https://loteriascaixa-api.herokuapp.com/api/{}/{}"

# Ficha de cada modalidade: a ÚNICA fonte das regras (conferidas nas páginas oficiais de loterias.caixa.gov.br em
# 31/10/2026). As funções perguntam a característica ("colunas", "trevos", "sorteios"...), não o nome do jogo.
#   N: universo | sorteados | escolha: (mínimo, máximo) de números marcados | faixas premiadas | preço da aposta mínima
#   colunas: Super Sete (7 colunas de 0 a 9; N = dígitos por coluna) | trevos: (universo, sorteados, mín, máx)
#   sorteios: 2 na Dupla Sena | extra: (chave no resultado, faixa, chance) | zero_e_100: '00' do volante = 100
#   espelho: aposta-espelho | volante: colunas do bilhete (padrões de escolha) | dias: dias de sorteio (0 = segunda)
MODALIDADES = {
    "megasena": dict(nome="Mega-Sena", N=60, sorteados=6, escolha=(6, 20), faixas=[4, 5, 6], preco=6.00,
                     volante=10, dias=(1, 3, 5)),
    "lotofacil": dict(nome="Lotofácil", N=25, sorteados=15, escolha=(15, 20), faixas=[11, 12, 13, 14, 15], preco=3.50,
                      volante=5, dias=(0, 1, 2, 3, 4, 5)),
    "quina": dict(nome="Quina", N=80, sorteados=5, escolha=(5, 15), faixas=[2, 3, 4, 5], preco=3.00, volante=10,
                  dias=(0, 1, 2, 3, 4, 5)),
    "lotomania": dict(nome="Lotomania", N=100, sorteados=20, escolha=(50, 50), faixas=[0, 15, 16, 17, 18, 19, 20],
                      preco=3.00, volante=10, dias=(0, 2, 4), zero_e_100=True, espelho=True),
    "duplasena": dict(nome="Dupla Sena", N=50, sorteados=6, escolha=(6, 15), faixas=[3, 4, 5, 6], preco=3.00,
                      volante=10, dias=(0, 2, 4), sorteios=2),
    "timemania": dict(nome="Timemania", N=80, sorteados=7, escolha=(10, 10), faixas=[3, 4, 5, 6, 7], preco=3.50,
                      volante=10, dias=(1, 3, 5), extra=("time", "Time do Coração", 1 / 80)),
    "diadesorte": dict(nome="Dia de Sorte", N=31, sorteados=7, escolha=(7, 15), faixas=[4, 5, 6, 7], preco=2.50,
                       volante=7, dias=(1, 3, 5), extra=("mes", "Mês da Sorte", 1 / 12)),
    "maismilionaria": dict(nome="+Milionária", N=50, sorteados=6, escolha=(6, 12), faixas=[2, 3, 4, 5, 6],
                           preco=6.00, volante=10, dias=(2, 5), trevos=(6, 2, 2, 6)),
    "supersete": dict(nome="Super Sete", N=10, sorteados=7, escolha=(7, 21), faixas=[3, 4, 5, 6, 7], preco=3.00,
                      dias=(0, 2, 4), colunas=7),
}
M = MODALIDADES
# visões derivadas, mantidas para o resto do código
JOGOS = {j: (m["N"], m["sorteados"], m["escolha"][0], m["faixas"], m["preco"]) for j, m in M.items()}
# Preço com vigência: (primeiro concurso, preço, fonte). Reajuste de 2025 pelo cronograma oficial da CAIXA
# (caixanoticias.caixa.gov.br, 02/07/2025, imagem LOTERIAS_02072025.jpg). O preço anterior a cada vigência não foi
# encontrado em fonte oficial: as análises usam o atual nesses concursos e avisam.
_REAJUSTE_2025 = "CAIXA Notícias 02/07/2025 (cronograma do reajuste)"
PRECOS = {"megasena": [(2887, 6.00, _REAJUSTE_2025)], "lotofacil": [(3439, 3.50, _REAJUSTE_2025)],
          "quina": [(6770, 3.00, _REAJUSTE_2025)], "duplasena": [(2832, 3.00, _REAJUSTE_2025)],
          "supersete": [(727, 3.00, _REAJUSTE_2025)]}
PRECOS.update({j: [(None, JOGOS[j][4], "loterias.caixa.gov.br, consulta 31/10/2026 (início da vigência não registrado)")]
               for j in ("lotomania", "timemania", "diadesorte", "maismilionaria")})


def preco_vigente(jogo, concurso):
    """(preço do concurso, True se esse preço é conhecido de fonte oficial)."""
    for inicio, preco, _ in sorted(PRECOS[jogo], key=lambda v: v[0] or 0, reverse=True):
        if inicio and concurso >= inicio:
            return preco, True
    return JOGOS[jogo][4], False


def universo(jogo):
    """(menor, maior) número válido em sorteios e apostas. Lotomania: o '00' do volante é guardado como 100."""
    return (0, 9) if M[jogo].get("colunas") else (1, JOGOS[jogo][0])


MESES = ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho", "Agosto", "Setembro", "Outubro",
         "Novembro", "Dezembro"]
# prêmio extra escolhido fora das dezenas: (chave no meta, descrição da faixa, chance de acertar)
EXTRA = {j: m["extra"] for j, m in M.items() if m.get("extra")}
# dezenas por cartão nos jogos de dezenas (Super Sete: 7 colunas; os números extras por coluna ficam em 'escolha')
MAX_DEZ = {j: (m["colunas"] if m.get("colunas") else m["escolha"][1]) for j, m in M.items()}
# Bolão CAIXA com uma aposta de n dezenas: n -> (valor mínimo da cota, máximo de cotas); mínimo de 2 cotas.
# Tabelas das páginas oficiais em loterias.caixa.gov.br (consultadas em 30/10/2026). Com a aposta mínima o bolão
# precisa de várias apostas (não modelado). +Milionária: apostas com 2 trevos. Cotas prontas vendidas na lotérica
# podem custar até 35% a mais (tarifa de serviço).
BOLAO = {
    "megasena": {7: (8.00, 50), 8: (8.00, 100), 9: (8.00, 100), 10: (12.60, 100), 11: (27.72, 100),
                 12: (55.44, 100), 13: (102.96, 100), 14: (180.18, 100), 15: (300.30, 100), 16: (480.48, 100),
                 17: (742.56, 100), 18: (1113.84, 100), 19: (1627.92, 100), 20: (2325.60, 100)},
    "lotofacil": {16: (4.50, 35), 17: (11.90, 40), 18: (57.12, 50), 19: (159.60, 85), 20: (542.64, 100)},
    "quina": {6: (4.00, 25), 7: (4.00, 50), 8: (4.00, 50), 9: (7.56, 50), 10: (15.12, 50), 11: (27.72, 50),
              12: (47.52, 50), 13: (77.22, 50), 14: (120.12, 50), 15: (180.18, 50)},
    "diadesorte": {8: (3.00, 20), 9: (3.60, 25), 10: (6.00, 50), 11: (11.00, 60), 12: (26.40, 60),
                   13: (42.90, 60), 14: (85.80, 100), 15: (160.88, 100)},
    "duplasena": {7: (3.00, 50), 8: (3.00, 50), 9: (5.04, 50), 10: (12.60, 50), 11: (27.72, 50),
                  12: (55.44, 50), 13: (102.96, 50), 14: (180.18, 50), 15: (300.30, 50)},
    "maismilionaria": {7: (6.00, 98), 8: (6.00, 100), 9: (6.00, 100), 10: (12.60, 100), 11: (27.72, 100),
                       12: (55.44, 100)},
}


def cotas_bolao(jogo, n, custo_aposta, ate):
    """Menor número de cotas permitido para a cota caber em 'ate' reais (menos cotas = parte maior do prêmio).
    None se a Caixa não permite esse bolão com cota até esse valor."""
    regra = BOLAO.get(jogo, {}).get(n)
    if not regra:
        return None
    cota_min, maximo = regra
    q = max(2, math.ceil(custo_aposta / ate - 1e-9))
    if q > maximo or custo_aposta / q < cota_min - 1e-9:
        return None
    return q


# ---------------- banco ----------------
_schema_ok = False


class _Conn(sqlite3.Connection):
    """`with db() as c:` faz commit/rollback e também fecha a conexão (o padrão do sqlite3 só faz commit)."""
    def __exit__(self, *exc):
        try:
            return super().__exit__(*exc)
        finally:
            self.close()


def _migra(c):
    """Lotomania gravada com 0 no lugar do 100 e times da Timemania com nomes sujos (versões antigas)."""
    for conc, dz in c.execute("SELECT concurso, dezenas FROM draws WHERE jogo='lotomania'").fetchall():
        d = json.loads(dz)
        if 0 in d:
            c.execute("UPDATE draws SET dezenas=? WHERE jogo='lotomania' AND concurso=?",
                      (json.dumps([100 if x == 0 else x for x in d]), conc))
    for conc, meta in c.execute("SELECT concurso, meta FROM draws WHERE jogo='timemania' AND meta IS NOT NULL"
                                ).fetchall():
        m = json.loads(meta)
        t = norm_time(m.get("time"))
        if t != m.get("time"):
            m["time"] = t
            c.execute("UPDATE draws SET meta=? WHERE jogo='timemania' AND concurso=?", (json.dumps(m), conc))
    c.commit()   # sem 'with c': nesta classe o 'with' fecha a conexão


def db():
    global _schema_ok
    c = sqlite3.connect(DB, timeout=10, factory=_Conn)
    if not _schema_ok:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(name TEXT PRIMARY KEY, salt TEXT, hash TEXT);
        CREATE TABLE IF NOT EXISTS sessions(hash TEXT PRIMARY KEY, name TEXT);
        CREATE TABLE IF NOT EXISTS draws(jogo TEXT, concurso INT, dezenas TEXT, PRIMARY KEY(jogo, concurso));
        CREATE TABLE IF NOT EXISTS bets(id INTEGER PRIMARY KEY, user TEXT, jogo TEXT, dezenas TEXT, trevos TEXT,
                                        desde INT, criado TEXT);
        CREATE TABLE IF NOT EXISTS chats(id INTEGER PRIMARY KEY, user TEXT, role TEXT, content TEXT);
        CREATE TABLE IF NOT EXISTS correcoes(jogo TEXT, concurso INT, antes TEXT, depois TEXT, quando TEXT);
        CREATE TABLE IF NOT EXISTS experimentos(id INTEGER PRIMARY KEY, quando TEXT, jogo TEXT, modo TEXT,
                                                params TEXT, versao TEXT, hash_dados TEXT, resultado TEXT);
        CREATE TABLE IF NOT EXISTS banca(id INTEGER PRIMARY KEY, user TEXT, data TEXT, tipo TEXT, valor REAL,
                                         descr TEXT);
        CREATE INDEX IF NOT EXISTS ix_bets_user ON bets(user);
        CREATE INDEX IF NOT EXISTS ix_banca_user ON banca(user, data);
        CREATE INDEX IF NOT EXISTS ix_chats_user ON chats(user, id);
        """)
        try:
            c.execute("ALTER TABLE draws ADD COLUMN meta TEXT")
        except sqlite3.OperationalError:
            pass
        _migra(c)
        _schema_ok = True
    return c


# ---------------- autenticação ----------------
def hash_pw(pw, salt):
    return hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), 200_000).hex()


def register(name, pw):
    if len(pw) < 6:
        raise ValueError("Senha precisa de ao menos 6 caracteres.")
    salt = secrets.token_hex(16)
    with db() as c:
        c.execute("INSERT INTO users VALUES(?,?,?)", (name, salt, hash_pw(pw, salt)))


def check_login(name, pw):
    row = db().execute("SELECT salt, hash FROM users WHERE name=?", (name,)).fetchone()
    return bool(row) and secrets.compare_digest(hash_pw(pw, row[0]), row[1])


# ---------------- lembrar dados ----------------
PREFS = os.path.join(DATA, "prefs.json")


def load_prefs():
    try:
        with open(PREFS, encoding="utf8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_prefs(**kw):
    p = load_prefs()
    p.update(kw)
    with open(PREFS, "w", encoding="utf8") as f:
        json.dump(p, f)


def remember(name):
    """Guarda só um token aleatório (o banco tem o hash dele), nunca a senha."""
    tok = secrets.token_hex(32)
    with db() as c:
        c.execute("DELETE FROM sessions WHERE name=?", (name,))
        c.execute("INSERT INTO sessions VALUES(?,?)", (hashlib.sha256(tok.encode()).hexdigest(), name))
    save_prefs(token=tok)


def remembered_user():
    tok = load_prefs().get("token")
    if not tok:
        return None
    row = db().execute("SELECT name FROM sessions WHERE hash=?",
                       (hashlib.sha256(tok.encode()).hexdigest(),)).fetchone()
    return row[0] if row else None


def forget(name):
    if remembered_user() in (name, None):
        save_prefs(token=None)
    with db() as c:
        c.execute("DELETE FROM sessions WHERE name=?", (name,))


# ---------------- dados ----------------
def _get(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _from_mirror(d):
    return {"concurso": d["concurso"], "dezenas": d["dezenas"], "data": d.get("data"), "fonte": "espelho",
            "acumulado": d.get("acumulou"), "proximo": d.get("dataProximoConcurso"),
            "trevos": d.get("trevos") or [], "time": d.get("timeCoracao"), "mes": d.get("mesSorte"),
            "estimado": d.get("valorEstimadoProximoConcurso") or 0,
            "arrecadado": d.get("valorArrecadado") or 0,
            "premios": [[p.get("descricao", ""), p.get("valorPremio") or 0] for p in d.get("premiacoes") or []],
            "ganhadores": [p.get("ganhadores") or 0 for p in d.get("premiacoes") or []]}


def _from_caixa(d):
    return {"concurso": d["numero"], "dezenas": d["listaDezenas"], "data": d.get("dataApuracao"), "fonte": "caixa",
            "acumulado": d.get("acumulado"), "proximo": d.get("dataProximoConcurso"),
            "trevos": d.get("trevosSorteados") or [], "dezenas2": d.get("listaDezenasSegundoSorteio") or [],
            "time": d.get("nomeTimeCoracaoMesSorte"), "mes": d.get("nomeTimeCoracaoMesSorte"),
            "estimado": d.get("valorEstimadoProximoConcurso") or 0,
            "arrecadado": d.get("valorArrecadado") or 0,
            "premios": [[p.get("descricaoFaixa", ""), p.get("valorPremio") or 0]
                        for p in d.get("listaRateioPremio") or []],
            "ganhadores": [p.get("numeroDeGanhadores") or 0 for p in d.get("listaRateioPremio") or []]}


def fetch(jogo, concurso=""):
    """Tenta a API oficial da Caixa (dois servidores); se falhar, usa a API espelho. Formato normalizado."""
    e1 = None
    for url in CAIXA:
        try:
            return _from_caixa(_get(url.format(jogo, concurso)))
        except Exception as e:
            e1 = e
    try:
        return _from_mirror(_get(MIRROR.format(jogo, concurso or "latest")))
    except Exception as e2:
        raise RuntimeError(f"Sem acesso aos resultados. Verifique internet/proxy/firewall.\n"
                           f"  Caixa: {e1}\n  Espelho: {e2}") from None


def ultimo_resultado(jogo):
    """Último concurso: pergunta aos dois servidores da Caixa e fica com o mais novo (um deles às vezes fica um
    concurso atrás). Sem a Caixa, usa o espelho."""
    achados = []
    for url in CAIXA:
        try:
            achados.append(_from_caixa(_get(url.format(jogo, ""))))
        except Exception:
            logging.getLogger("luckharness").warning("último %s indisponível em %s", jogo, url, exc_info=True)
    return max(achados, key=lambda d: d["concurso"]) if achados else fetch(jogo)


def _row(jogo, d):
    # dupla sena traz os dois sorteios juntos; usa só o primeiro. Super Sete fica na ordem das colunas.
    K = JOGOS[jogo][1]
    dez = [int(x) for x in d["dezenas"]][:K]
    if M[jogo].get("zero_e_100"):
        dez = [100 if x == 0 else x for x in dez]   # o '00' do volante: uma só representação em todo o app
    meta = {"data": d["data"], "trevos": [int(x) for x in d["trevos"]], "premios": d["premios"],
            "estimado": d.get("estimado", 0), "arrecadado": d.get("arrecadado", 0),
            "ganhadores": d.get("ganhadores") or [], "fonte": d.get("fonte"), "acumulado": d.get("acumulado"),
            "proximo": d.get("proximo")}
    if jogo in EXTRA:
        chave = EXTRA[jogo][0]
        meta[chave] = _extra(jogo, d.get(chave))
    if M[jogo].get("sorteios") == 2:
        # espelho manda as 12 dezenas juntas; a Caixa manda o 2º sorteio separado
        meta["dezenas2"] = [int(x) for x in d.get("dezenas2") or d["dezenas"][K:2 * K]]
    return jogo, d["concurso"], json.dumps(dez), json.dumps(meta)


MAX_BURACOS = 300   # concursos faltando no espelho buscados na Caixa por sincronização


def sync_draws(jogo, n=50, log=print):
    """Baixa o histórico completo de uma vez pela API espelho e completa pela Caixa os concursos que o espelho
    não tem; sem espelho, busca na Caixa os últimos n concursos em paralelo."""
    have = {r[0] for r in db().execute("SELECT concurso FROM draws WHERE jogo=?", (jogo,))}
    try:
        rows = [_row(jogo, _from_mirror(d)) for d in _get(MIRROR.format(jogo, "").rstrip("/"), 60)]
        tem = have | {r[1] for r in rows}
        try:   # o espelho costuma atrasar: o último concurso vem da Caixa e os que faltam entram como buracos
            ultimo = ultimo_resultado(jogo)
            rows.append(_row(jogo, ultimo))
            tem.add(ultimo["concurso"])
        except Exception:
            logging.getLogger("luckharness").warning("último concurso de %s indisponível", jogo, exc_info=True)
        faltam = [k for k in range(max(tem), 0, -1) if k not in tem][:MAX_BURACOS]
    except Exception:
        log("Espelho indisponível, buscando na Caixa (mais lento)...")
        last = ultimo_resultado(jogo)
        num = last["concurso"]
        rows = [_row(jogo, last)]
        faltam = [k for k in range(num - 1, max(0, num - n), -1) if k not in have]
    if faltam:
        def busca(k):
            try:
                return fetch(jogo, k)
            except Exception:
                logging.getLogger("luckharness").warning("concurso %s %s indisponível", jogo, k, exc_info=True)
        with ThreadPoolExecutor(8) as ex:
            achados = [d for d in ex.map(busca, faltam) if d]
        if len(achados) < len(faltam):
            log(f"{len(faltam) - len(achados)} concurso(s) não baixaram; tente de novo depois.")
        rows += [_row(jogo, d) for d in achados]
    validos = {}
    for r in rows:
        erro = valida_resultado(jogo, r[1], json.loads(r[2]), json.loads(r[3]))
        if erro:
            logging.getLogger("luckharness").warning("resultado recusado: %s", erro)
        else:
            validos[r[1]] = r
    if len(validos) < len(rows):
        log(f"{len(rows) - len(validos)} resultado(s) inválido(s) recusado(s) (detalhes no log).")
    rows = list(validos.values())
    mesma = (lambda d: d) if M[jogo].get("colunas") else sorted   # Super Sete: a ordem das colunas importa
    with db() as c:   # uma transação: ou grava tudo, ou nada
        antigos = {k: json.loads(d) for k, d in c.execute("SELECT concurso, dezenas FROM draws WHERE jogo=?",
                                                          (jogo,))}
        agora = datetime.now().isoformat(timespec="seconds")
        corr = [(jogo, k, json.dumps(antigos[k]), dz, agora) for _, k, dz, _ in rows
                if k in antigos and mesma(antigos[k]) != mesma(json.loads(dz))]
        c.executemany("INSERT INTO correcoes(jogo, concurso, antes, depois, quando) VALUES(?,?,?,?,?)", corr)
        c.executemany("INSERT INTO draws(jogo, concurso, dezenas, meta) VALUES(?,?,?,?) "
                      "ON CONFLICT(jogo, concurso) DO UPDATE SET dezenas=excluded.dezenas, meta=excluded.meta",
                      rows)
        novos = c.execute("SELECT COUNT(*) FROM draws WHERE jogo=?", (jogo,)).fetchone()[0] - len(antigos)
    if corr:
        log(f"{len(corr)} resultado(s) corrigido(s) pela fonte: concurso(s) "
            + ", ".join(str(x[1]) for x in corr[:10]) + ".")
    log(f"{len(rows)} concursos disponíveis, {novos} novos salvos.")
    return novos


def valida_resultado(jogo, concurso, dezenas, meta):
    """Motivo para recusar um resultado antes de gravar, ou None se estiver certo."""
    N, K = JOGOS[jogo][:2]
    lo, hi = universo(jogo)
    if not isinstance(concurso, int) or concurso < 1:
        return f"{jogo}: número de concurso inválido ({concurso!r})"
    listas = [("dezenas", dezenas)] + ([("2º sorteio", meta.get("dezenas2") or [])] if M[jogo].get("sorteios") == 2 else [])
    for nome, d in listas:
        if len(d) != K or any(not lo <= x <= hi for x in d) or (not M[jogo].get("colunas") and len(set(d)) != K):
            return f"{jogo} {concurso}: {nome} inválidas {d}"
    if M[jogo].get("trevos"):
        t = meta.get("trevos") or []
        if len(t) != 2 or len(set(t)) != 2 or any(not 1 <= x <= 6 for x in t):
            return f"{jogo} {concurso}: trevos inválidos {t}"
    return None


def load_draws(jogo, n):
    rows = db().execute("SELECT concurso, dezenas FROM draws WHERE jogo=? ORDER BY concurso DESC LIMIT ?",
                        (jogo, n)).fetchall()
    return [(r[0], json.loads(r[1])) for r in rows]


def versao_regra(jogo):
    """Regras e preço usados, com a fonte (para o painel de cada experimento)."""
    m = M[jogo]
    ini, preco, fonte = sorted(PRECOS[jogo], key=lambda v: v[0] or 0)[-1]
    esc = f"{m['escolha'][0]}" + (f" a {m['escolha'][1]}" if m["escolha"][1] != m["escolha"][0] else "")
    return (f"Regras ({m['nome']}): {esc} números de {m['N']}{' por coluna' if m.get('colunas') else ''}, "
            f"{m['sorteados']} sorteados{' (2 sorteios)' if m.get('sorteios') == 2 else ''}, faixas "
            f"{', '.join(map(str, m['faixas']))}; preço R$ {preco:.2f}".replace(".", ",")
            + (f" desde o concurso {ini}" if ini else "") + f" ({fonte}). Fichas conferidas em 31/10/2026.")


def resumo_do_dia():
    """Último resultado de cada loteria: números, faixa principal (ganhadores e prêmio, ou ACUMULOU) e o próximo
    concurso com o prêmio estimado."""
    out = [f"=== RESULTADOS (atualizado em {datetime.now().strftime('%d/%m/%Y %H:%M')}) ===", ""]
    for j, m in M.items():
        r = db().execute("SELECT concurso, dezenas, meta FROM draws WHERE jogo=? ORDER BY concurso DESC LIMIT 1",
                         (j,)).fetchone()
        if not r:
            out += [f"{m['nome']}: sem resultados (clique em 'Atualizar agora').", ""]
            continue
        conc, dz, meta = r[0], json.loads(r[1]), json.loads(r[2] or "{}")
        extra = ""
        if m.get("extra"):
            extra = f"  | {m['extra'][1]}: {meta.get(m['extra'][0]) or '?'}"
        if m.get("trevos"):
            extra = f"  | trevos: {' '.join(map(str, meta.get('trevos') or []))}"
        out.append(f"{m['nome']} — concurso {conc} ({meta.get('data', '?')}): {fmt_bet(j, dz, [])}{extra}")
        if meta.get("dezenas2"):
            out.append(f"   2º sorteio: {fmt_bet(j, meta['dezenas2'], [])}")
        pr, gan = meta.get("premios") or [], meta.get("ganhadores") or []
        acumulou = meta.get("acumulado") if meta.get("acumulado") is not None else bool(gan and gan[0] == 0)
        if pr:
            topo = pr[0][0]
            out.append(f"   {topo}: " + ("ACUMULOU" if acumulou else
                                         f"{gan[0] if gan else '?'} ganhador(es), {brl(pr[0][1])} cada"))
        prox = meta.get("proximo") or proximo_sorteio(j).strftime("%d/%m/%Y")
        out.append(f"   Próximo: concurso {conc + 1} em {prox}"
                   + (f", prêmio estimado {brl(meta['estimado'])}" if meta.get("estimado") else ""))
        out.append("")
    return "\n".join(out)


def load_sorteios(jogo, n):
    """Como load_draws, mas a Dupla Sena traz os dois sorteios de cada concurso (o 2º logo depois do 1º)."""
    if M[jogo].get("sorteios") != 2:
        return load_draws(jogo, n)
    out = []
    for conc, dz, meta in db().execute("SELECT concurso, dezenas, meta FROM draws WHERE jogo=? ORDER BY concurso "
                                       "DESC LIMIT ?", (jogo, n)):
        d2 = json.loads(meta or "{}").get("dezenas2")
        out += ([(conc, d2)] if d2 else []) + [(conc, json.loads(dz))]
    return out


def datas_fora_de_ordem(jogo):
    """Concursos cuja data é anterior à do concurso anterior: erro na fonte (só sinaliza; não corrige sem fonte
    oficial)."""
    out, ant = [], None
    for conc, meta in db().execute("SELECT concurso, meta FROM draws WHERE jogo=? AND meta IS NOT NULL "
                                   "ORDER BY concurso", (jogo,)):
        try:
            d, m, a = map(int, json.loads(meta)["data"].split("/"))
            x = date(a, m, d)
        except (KeyError, ValueError, AttributeError):
            continue
        if ant and x < ant:
            out.append((conc, x.strftime("%d/%m/%Y")))
        ant = x
    return out


def hash_dados(jogo):
    """Impressão digital dos resultados usados num experimento (reprodutibilidade)."""
    rows = db().execute("SELECT concurso, dezenas FROM draws WHERE jogo=? ORDER BY concurso", (jogo,)).fetchall()
    return hashlib.sha256(json.dumps(rows).encode()).hexdigest()[:16]


def load_trevos(jogo, n):
    rows = db().execute("SELECT meta FROM draws WHERE jogo=? ORDER BY concurso DESC LIMIT ?", (jogo, n)).fetchall()
    return [json.loads(r[0]).get("trevos", []) for r in rows if r[0]]


def last_concurso(jogo):
    return db().execute("SELECT MAX(concurso) FROM draws WHERE jogo=?", (jogo,)).fetchone()[0] or 0


# ---------------- matemática ----------------
def hypergeom(N, K, n, k):
    """P(acertar k) apostando n dezenas, sorteio de K entre N."""
    return math.comb(K, k) * math.comb(N - K, n - k) / math.comb(N, n)


def analyze(jogo, draws, n_bet):
    N, K, _, faixas, preco = JOGOS[jogo]
    freq = Counter(x for _, d in draws for x in d)
    total = len(draws)
    expected = total * K / N
    chi2 = sum((freq.get(i, 0) - expected) ** 2 / expected for i in range(1, N + 1)) if total else 0
    atraso = {}
    for i in range(1, N + 1):
        atraso[i] = next((idx for idx, (_, d) in enumerate(draws) if i in d), total)
    somas = [sum(d) for _, d in draws]
    pares = [sum(1 for x in d if x % 2 == 0) for _, d in draws]
    custo = preco * math.comb(n_bet, JOGOS[jogo][2])

    unidade = "sorteios (2 por concurso)" if M[jogo].get("sorteios") == 2 else "concursos"
    out = [f"=== {jogo.upper()} | {total} {unidade} | aposta de {n_bet} dezenas ===", ""]
    out.append("Probabilidades (hipergeométrica):")
    for k in sorted(faixas):
        p = hypergeom(N, K, n_bet, k)
        out.append(f"  {k:>2} acertos: P = {p:.3e}  (1 em {1/p:,.0f})" if p else f"  {k} acertos: 0")
    exp_hits = n_bet * K / N
    out.append(f"Acertos esperados E[X] = n*K/N = {exp_hits:.3f}")
    out.append(f"Custo estimado da aposta: {brl(custo)}")
    out.append("")
    out.append(f"Qui-quadrado uniformidade: {chi2:.2f} (gl={N-1}, crítico 5% ≈ {N-1 + 1.645*math.sqrt(2*(N-1)):.1f})")
    out.append("  -> abaixo do crítico = sorteios compatíveis com aleatoriedade pura.")
    out.append("Mais frequentes: " + ", ".join(f"{d}({c})" for d, c in freq.most_common(10)))
    out.append("Menos frequentes: " + ", ".join(f"{d}({freq.get(d,0)})" for d in
               sorted(range(1, N + 1), key=lambda i: freq.get(i, 0))[:10]))
    out.append("Mais atrasadas: " + ", ".join(f"{d}({a})" for d, a in
               sorted(atraso.items(), key=lambda x: -x[1])[:10]))
    if somas:
        m = sum(somas) / len(somas)
        sd = math.sqrt(sum((s - m) ** 2 for s in somas) / len(somas))
        out.append(f"Soma das dezenas: média {m:.1f}, desvio {sd:.1f}")
        out.append(f"Pares por sorteio: média {sum(pares)/len(pares):.2f} (teórico {K/2:.2f})")
    if M[jogo].get("trevos"):
        tf = Counter(x for t in load_trevos(jogo, total) for x in t)
        out.append("Trevos (1 a 6, saem 2): " + ", ".join(f"{t}({tf.get(t, 0)})" for t in range(1, 7)))
        out.append("  P(acertar os 2 trevos com 2 marcados) = 1/15")
    out += repeticoes(jogo, draws)
    out.append("")
    out.append("AVISO: cada sorteio é independente. Frequência passada NÃO altera a probabilidade futura.")
    out.append("Valor esperado de qualquer aposta é negativo (~50% do arrecadado vira prêmio).")
    return "\n".join(out), freq, atraso, (m, sd) if somas else (None, None), somas


def repeticoes(jogo, draws):
    """Quantas dezenas se repetem de um concurso para o seguinte, real vs. acaso, e a 'regra do +1'
    (jogar cada dezena do último sorteio somada de 1) comparada a qualquer outra aposta."""
    N, K = JOGOS[jogo][:2]
    pares = [(set(a), set(b)) for (_, a), (_, b) in zip(draws[1:], draws)]  # (anterior, seguinte)
    if not pares:
        return []
    rep = Counter(len(a & b) for a, b in pares)
    mais1 = [len({x % N + 1 for x in a} & b) for a, b in pares]
    esp = K * K / N
    out = ["", f"Repetição do concurso anterior ({len(pares)} pares de concursos seguidos):",
           f"  Média real: {sum(k * c for k, c in rep.items()) / len(pares):.2f} dezenas repetidas "
           f"(acaso puro: {esp:.2f})"]
    for k in range(max(0, 2 * K - N), K + 1):
        p = hypergeom(N, K, K, k)
        if rep.get(k) or p > 0.005:
            out.append(f"  {k:>2} repetidas: {rep.get(k, 0) / len(pares):6.1%} dos concursos (teórico {p:6.1%})")
    out.append(f"  'Regra do +1' (dezenas do último sorteio + 1): acertou em média {sum(mais1) / len(pares):.2f} "
               f"(qualquer aposta de {K}: {esp:.2f})")
    out.append("  -> repetir dezenas é o normal; evitar ou deslocar o último resultado não muda a chance.")
    return out


def analyze_ss(draws):
    """Super Sete: 7 colunas independentes, um dígito de 0 a 9 sorteado em cada."""
    cols = [Counter(d[c] for _, d in draws) for c in range(7)]
    total = len(draws)
    exp = total / 10
    out = [f"=== SUPERSETE | {total} concursos | 1 número por coluna ===", "",
           "Probabilidades (binomial, 7 colunas, p = 1/10 em cada):"]
    for k in range(3, 8):
        p = math.comb(7, k) * 0.1 ** k * 0.9 ** (7 - k)
        out.append(f"  {k} acertos: P = {p:.3e}  (1 em {1/p:,.0f})")
    out.append("Acertos esperados E[X] = 7 x 0,1 = 0,700")
    out.append("Custo da aposta simples: R$ 3,00")
    out.append("")
    out.append("Qui-quadrado por coluna (gl=9, crítico 5% ≈ 16,9):")
    for c in range(7):
        chi2 = sum((cols[c].get(i, 0) - exp) ** 2 / exp for i in range(10)) if total else 0
        out.append(f"  Coluna {c+1}: {chi2:5.1f} | mais saem: " +
                   ", ".join(f"{d}({n})" for d, n in cols[c].most_common(3)))
    out.append("")
    out.append("AVISO: cada sorteio é independente. Frequência passada NÃO altera a probabilidade futura.")
    return "\n".join(out), cols


def recent_text(draws, k=15):
    return "\n".join(f"  {c}: " + " ".join(f"{x:02d}" for x in sorted(d)) for c, d in draws[:k])


# ---------------- aprendizado ----------------
JANELAS = (10, 50, 200)


def _features(cum, t, i, atraso, K, N):
    base = K / N
    f = [1.0]
    for w in JANELAS:
        a = max(0, t - w)
        f.append((cum[t][i] - cum[a][i]) / max(1, t - a) / base - 1)
    f.append(min(atraso, 50) / 50)
    return f


def learn(jogo, draws, n_bet, teste=100, epocas=3, lr=0.05):
    """Neurônio logístico (rede neural de 1 camada) treinado em ordem cronológica.
    Para cada concurso estima a chance de cada dezena usando só os concursos anteriores e
    mede no teste (últimos concursos) quantas dezenas o top-n do modelo acertou vs. o acaso."""
    N, K = JOGOS[jogo][:2]
    ds = [set(d) for _, d in sorted(draws)]
    T = len(ds)
    if T < 60:
        return "\nModelo: precisa de ao menos 60 concursos no histórico.", None
    cum = [[0] * (N + 1)]
    for d in ds:
        row = cum[-1][:]
        for x in d:
            row[x] += 1
        cum.append(row)
    teste = min(teste, T // 3) if teste else min(1000, T // 3)
    w = [0.0] * (len(JANELAS) + 2)

    def rodada(t, atr, treinar):
        scores = {}
        for i in range(1, N + 1):
            f = _features(cum, t, i, atr[i], K, N)
            z = sum(a * b for a, b in zip(w, f))
            p = 1 / (1 + math.exp(-max(-30, min(30, z))))
            scores[i] = p
            if treinar:
                g = (1 if i in ds[t] else 0) - p
                for j in range(len(w)):
                    w[j] += lr * g * f[j]
        return scores

    for ep in range(epocas):
        acertos = []
        atr = {i: 0 for i in range(1, N + 1)}
        for t in range(T):
            treino = t < T - teste
            if t >= 20 and (treino or ep == epocas - 1):
                sc = rodada(t, atr, treino)
                if not treino:
                    top = sorted(sc, key=sc.get, reverse=True)[:n_bet]
                    acertos.append(len(ds[t].intersection(top)))
            for i in range(1, N + 1):
                atr[i] = 0 if i in ds[t] else atr[i] + 1
    # o palpite final aprende também com os concursos do teste (já avaliados), em ordem
    atr = {i: 0 for i in range(1, N + 1)}
    for t in range(T):
        if t >= max(20, T - teste):
            rodada(t, atr, True)
        for i in range(1, N + 1):
            atr[i] = 0 if i in ds[t] else atr[i] + 1
    prox = rodada(T, atr, False)
    esperado = n_bet * K / N
    media = sum(acertos) / len(acertos)
    var = n_bet * (K / N) * (1 - K / N) * (N - n_bet) / (N - 1)
    se = math.sqrt(var / len(acertos)) if var else 0
    zs = (media - esperado) / se if se else 0
    nomes = ["viés"] + [f"freq{x}" for x in JANELAS] + ["atraso"]
    out = ["", f"Modelo de aprendizado (neurônio logístico, {epocas} épocas, teste em {len(acertos)} concursos):",
           "  pesos: " + ", ".join(f"{n}={v:+.3f}" for n, v in zip(nomes, w)),
           f"  acertos médios do top-{n_bet}: {media:.3f}  |  acaso: {esperado:.3f}  |  z = {zs:+.2f}",
           "  distribuição: " + ", ".join(f"{k}:{c}" for k, c in sorted(Counter(acertos).items())),
           f"  diferença: {media - esperado:+.3f} acerto (IC 95%: {media - esperado - 1.96 * se:+.3f} a "
           f"{media - esperado + 1.96 * se:+.3f}); com {len(acertos)} concursos o teste só detecta diferenças a partir "
           f"de {2.8 * se:.3f} acerto ({2.8 * se / esperado:.0%})",
           "  -> " + ("|z| < 2: sem evidência de diferença para o acaso (isso não prova que sejam iguais)." if abs(zs) < 2
                      else "|z| >= 2: diferença aparente; pode ser sorte, confirme em concursos futuros.")]
    return "\n".join(out), sorted(sorted(prox, key=prox.get, reverse=True)[:n_bet])


def suggest_ss(cols, qtd=5):
    return [[random.choices(range(10), [1 + cols[c].get(i, 0) for i in range(10)])[0] for c in range(7)]
            for _ in range(qtd)]


# ---------------- retorno financeiro ----------------
IR, IR_ISENTO = 0.30, 2259.20   # imposto de renda sobre prêmios acima do limite de isenção


def prob_faixa(jogo, h, n=None):
    """P(exatamente h acertos) com n dezenas marcadas (padrão: aposta simples)."""
    N, K, mn = JOGOS[jogo][:3]
    if M[jogo].get("colunas"):
        return math.comb(7, h) * 0.1 ** h * 0.9 ** (7 - h)
    return hypergeom(N, K, n or mn, h)


def premios_medios(jogo, n=100):
    """Mediana dos prêmios pagos por faixa nos últimos n concursos + dados do último concurso."""
    rows = db().execute("SELECT meta FROM draws WHERE jogo=? AND meta IS NOT NULL ORDER BY concurso DESC LIMIT ?",
                        (jogo, n)).fetchall()
    metas = [json.loads(r[0]) for r in rows]
    vals = {}
    for m in metas:
        for desc, v in m.get("premios", []):
            if v:
                vals.setdefault(desc, []).append(v)
    med = {d: sorted(v)[len(v) // 2] for d, v in vals.items()}
    return med, metas[0] if metas else {}


def valor_esperado(jogo, liquido=True):
    """Quanto uma aposta simples devolve em média. O prêmio principal usa a estimativa do próximo concurso,
    dividida pelo número esperado de ganhadores (Poisson com as apostas estimadas pela arrecadação)."""
    N, K, mn, faixas, preco = JOGOS[jogo]
    med, last = premios_medios(jogo)
    apostas = (last.get("arrecadado") or 0) / preco
    top = max(faixas)
    trevos = {2: 1 / 15, 1: 8 / 15, 0: 6 / 15} if M[jogo].get("trevos") else {0: 1.0}
    linhas, ev = [], 0.0
    for h in sorted(faixas, reverse=True):
        for th, pt in trevos.items():
            p = prob_faixa(jogo, h) * pt
            m = premio(jogo, h, th, list(med.items()))
            principal = h == top and th == max(trevos)
            if principal and last.get("estimado"):
                lam = apostas * p
                val = last["estimado"] * ((1 - math.exp(-lam)) / lam if lam > 1e-9 else 1)
                nome = f"{h} acertos + 2 trevos" if M[jogo].get("trevos") else f"{h} acertos"
                desc = nome + " (estimado, já dividido)"
            elif m:
                desc, val = m
            else:
                continue
            if liquido and val > IR_ISENTO:
                val *= 1 - IR
            ev += p * val
            if linhas and linhas[-1][0] == desc:   # mesma faixa por caminhos diferentes (trevos)
                d0, p0, v0, c0 = linhas.pop()
                p, val = p0 + p, val
            linhas.append((desc, p, val, p * val))
    if jogo in EXTRA:
        _, desc, p = EXTRA[jogo]
        val = med.get(desc)
        if val:
            ev += p * val
            linhas.append((desc, p, val, p * val))
    if M[jogo].get("sorteios") == 2:
        ev *= 2   # dois sorteios por concurso
    return {"jogo": jogo, "ev": ev, "preco": preco, "retorno": ev / preco, "linhas": linhas,
            "estimado": last.get("estimado") or 0, "apostas": apostas}


def texto_ev(r):
    out = [f"=== RETORNO: {r['jogo'].upper()} (aposta simples de {brl(r['preco'])}) ===",
           f"Prêmio principal estimado: {brl(r['estimado'])} | apostas estimadas no último concurso: "
           + f"{r['apostas']:,.0f}".replace(",", "."), "",
           f"{'Faixa':<52}{'Chance':>16}{'Prêmio líquido':>18}{'Contribui':>12}"]
    for desc, p, val, c in r["linhas"]:
        chance = "1 em " + f"{1 / p:,.0f}".replace(",", ".")
        out.append(f"{desc[:51]:<52}{chance:>16}{brl(val):>18}{brl(c):>12}")
    out += ["", f"Valor esperado estimado: {brl(r['ev'])} por aposta  ->  volta {r['retorno']:.0%} do que você paga",
            "Estimativa: chances exatas x prêmio principal estimado (já dividido entre os ganhadores esperados) e, "
            "nas outras faixas, a mediana paga nos últimos 100 concursos.",
            f"Custo efetivo: cada R$ 1,00 apostado perde em média {brl(1 - r['retorno'])}.",
            "Apostar mais dezenas multiplica custo e valor esperado igualmente: o retorno % não muda.",
            "Prêmios acima de R$ 2.259,20 já descontam 30% de IR."]
    if r["jogo"] == "duplasena":
        out.append("Dupla Sena: valor esperado soma os dois sorteios do concurso.")
    return "\n".join(out)


def popularidade(jogo, nums):
    """Estima o quanto a aposta parece com as que muita gente faz (datas, sequências, padrões).
    Não muda a chance de ganhar; aposta popular divide o prêmio com mais gente."""
    if M[jogo].get("colunas"):
        return 0, []
    N = JOGOS[jogo][0]
    s = sorted(nums)
    n = len(s)
    score, motivos = 0, []
    if N > 31:
        datas = sum(1 for x in s if x <= 31) / n
        if datas >= 0.8:
            score += 35
            motivos.append(f"{datas:.0%} dos números são até 31 (datas de aniversário)")
    run = best = 1
    for a, b in zip(s, s[1:]):
        run = run + 1 if b == a + 1 else 1
        best = max(best, run)
    if best >= 3:
        score += 12 * (best - 2)
        motivos.append(f"{best} números em sequência")
    if n >= 4 and len({b - a for a, b in zip(s, s[1:])}) == 1:
        score += 30
        motivos.append("progressão aritmética (mesmo intervalo entre todos)")
    if n <= 10 and sum(1 for x in s if x % 5 == 0) >= n * 0.6:
        score += 15
        motivos.append("muitos múltiplos de 5")
    dec = Counter((x - 1) // 10 for x in s).most_common(1)[0][1]
    if n <= 10 and dec >= max(4, n * 0.6):
        score += 15
        motivos.append(f"{dec} números na mesma dezena do volante")
    return min(100, score), motivos


def avaliar_aposta(jogo, nums, trevos, r=None, fator=None):
    """Chances, custo e valor esperado da aposta. 'fator' = divisão do prêmio pelo modelo de popularidade
    treinado; sem ele, usa as regras simples (datas, sequências, múltiplos)."""
    r = r or valor_esperado(jogo)
    N, K, mn, faixas, preco = JOGOS[jogo]
    n = len(nums)
    mult = apostas_simples(jogo, nums, trevos)
    c = custo(jogo, nums, trevos)
    pop, motivos = popularidade(jogo, nums)
    out = [f"Aposta: {fmt_bet(jogo, nums, trevos)}  ({mult} aposta(s) simples)",
           f"Custo: {brl(c)} | valor esperado: {brl(r['ev'] * mult)} | retorno {r['retorno']:.0%}"]
    for h in sorted(faixas, reverse=True):
        p = prob_faixa(jogo, h, n)
        if p:
            out.append(f"  P({h} acertos) = 1 em " + f"{1 / p:,.0f}".replace(",", "."))
    if fator is not None:
        out.append(f"Popularidade (modelo treinado): se ganhar, divide o prêmio com {fator:.2f}x a média de "
                   "ganhadores " + ("(boa: pouco jogada)" if fator < 1 else "(jogada acima da média)"))
    else:
        out.append(f"Popularidade (regras simples): {pop}/100 " + (
            "(boa: pouca chance de dividir prêmio)" if pop < 25 else
            "(alta: se ganhar, tende a dividir com mais gente)"))
        out += [f"  - {m}" for m in motivos]
    return "\n".join(out), pop


def bolao(jogo, n, cotas, r=None):
    r = r or valor_esperado(jogo)
    N, K, mn, faixas, preco = JOGOS[jogo]
    mult = 1 if M[jogo].get("colunas") else math.comb(n, mn)
    total = preco * mult
    menor = min(f for f in faixas if f > 0)
    p_algo = sum(prob_faixa(jogo, h, n) for h in faixas if h >= menor)
    out = [f"=== BOLÃO {jogo.upper()}: {n} dezenas, {cotas} cotas ===",
           f"Custo total: {brl(total)} | por cota: {brl(total / cotas)}",
           f"Valor esperado total: {brl(r['ev'] * mult)} | por cota: {brl(r['ev'] * mult / cotas)}",
           f"Chance de ganhar alguma faixa ({menor}+ acertos): 1 em " + f"{1 / p_algo:,.0f}".replace(",", "."),
           "Chance do prêmio principal: 1 em " + f"{1 / prob_faixa(jogo, max(faixas), n):,.0f}".replace(",", "."),
           "No bolão você divide custo e prêmio: a chance por real investido é a mesma de jogar sozinho."]
    return "\n".join(out)


# ---------------- planejamento ----------------
def _sorteia_ganho(faixas, T):
    """Um cenário: quantas vezes cada faixa sai em T apostas (multinomial via binomiais condicionais)."""
    rem, remp, ganho = T, 1.0, 0.0
    for p, val in faixas:
        if rem <= 0 or remp <= 0:
            break
        c = random.binomialvariate(rem, min(1.0, p / remp))
        ganho += c * val
        rem -= c
        remp -= p
    return ganho


def simular(jogo, semanal, anos, n_sim=2000):
    r = valor_esperado(jogo)
    por_semana = int(semanal // r["preco"])
    if por_semana < 1:
        raise ValueError(f"Com {brl(semanal)} por semana não dá para fazer uma aposta de {brl(r['preco'])}.")
    T = por_semana * 52 * anos
    gasto = T * r["preco"]
    # faixas das dezenas se excluem entre si; Time do Coração / Mês da Sorte é outro evento, que pode sair
    # junto com qualquer uma delas na mesma aposta
    extra = EXTRA[jogo][1] if jogo in EXTRA else None
    faixas = [(p, val) for desc, p, val, _ in r["linhas"] if desc != extra]
    p_ex, v_ex = next(((p, val) for desc, p, val, _ in r["linhas"] if desc == extra), (0.0, 0.0))
    sorteios = T * M[jogo].get("sorteios", 1)
    res = sorted(_sorteia_ganho(faixas, sorteios) + (random.binomialvariate(T, p_ex) * v_ex if p_ex else 0)
                 - gasto for _ in range(n_sim))
    pct = lambda q: res[min(len(res) - 1, int(q * len(res)))]
    return {"jogo": jogo, "gasto": gasto, "apostas": T, "por_semana": por_semana, "res": res,
            "mediana": pct(.5), "p10": pct(.1), "p90": pct(.9), "pior": res[0], "melhor": res[-1],
            "p_lucro": sum(x > 0 for x in res) / n_sim, "esperado": r["ev"] * T - gasto}


def texto_sim(m, semanal, anos):
    pos = lambda v: ("+" if v > 0 else "") + brl(v)
    return "\n".join([
        f"=== SIMULAÇÃO: {brl(semanal)} por semana em {m['jogo']} durante {anos} ano(s) ===",
        f"{m['por_semana']} aposta(s) simples por semana, " + f"{m['apostas']:,}".replace(",", ".") + " no total",
        f"Total gasto: {brl(m['gasto'])}", "",
        f"Resultado final em {len(m['res']):,} cenários simulados:".replace(",", "."),
        f"  Pior cenário:        {pos(m['pior'])}",
        f"  10% dos cenários:    {pos(m['p10'])} ou menos",
        f"  Mediana (o típico):  {pos(m['mediana'])}",
        f"  90% dos cenários:    {pos(m['p90'])} ou menos",
        f"  Melhor cenário:      {pos(m['melhor'])}",
        f"  Média estimada:      {pos(m['esperado'])}", "",
        f"Chance de terminar com lucro: {m['p_lucro']:.1%}".replace(".", ","),
        "A média inclui o prêmio principal raríssimo; a mediana mostra o que acontece com quase todo mundo.",
        "Simulação com os prêmios atuais: o prêmio principal estimado do próximo concurso e, nas outras faixas, "
        "a mediana dos últimos 100 concursos. São estimativas; os prêmios futuros variam."])


def historico_retorno(jogo, n=200):
    """Retorno de cada concurso passado com o que se sabia ANTES do sorteio: prêmio anunciado e vendas do concurso
    anterior (as do próprio concurso só saem depois). As faixas menores usam a mediana recente (variam pouco)."""
    base = valor_esperado(jogo)
    top = [(p) for d, p, v, c in base["linhas"] if "estimado" in d]
    if not top:
        return []
    p_top = top[0]
    resto = sum(c for d, p, v, c in base["linhas"] if "estimado" not in d)
    preco = base["preco"]
    rows = db().execute("SELECT concurso, meta FROM draws WHERE jogo=? AND meta IS NOT NULL ORDER BY concurso DESC "
                        "LIMIT ?", (jogo, n + 1)).fetchall()[::-1]
    out = []
    for (_, m0), (conc, m1) in zip(rows, rows[1:]):
        m0, m1 = json.loads(m0), json.loads(m1)
        J, vendas = m0.get("estimado") or 0, m0.get("arrecadado") or 0
        if not J or not vendas:
            continue
        lam = vendas / preco * p_top
        val = J * (1 - math.exp(-lam)) / lam
        if val > IR_ISENTO:
            val *= 1 - IR
        ev = (resto + p_top * val) * M[jogo].get("sorteios", 1)
        out.append((conc, J, ev / preco))
    return out


def texto_hist(jogo, hist, alvo):
    if not hist:
        return "Sem dados de prêmio estimado para este jogo."
    rs = [x[2] for x in hist]
    out = [f"=== HISTÓRICO DO RETORNO: {jogo.upper()} ({len(hist)} concursos) ===",
           f"Retorno médio: {sum(rs) / len(rs):.0%} | menor: {min(rs):.0%} | maior: {max(rs):.0%}", "",
           "Retorno conforme o prêmio principal estimado:"]
    por_j = sorted(hist, key=lambda x: x[1])
    q = max(1, len(por_j) // 5)
    for i in range(0, len(por_j), q):
        g = por_j[i:i + q]
        out.append(f"  {brl(g[0][1]):>20} a {brl(g[-1][1]):>20}:  retorno médio {sum(x[2] for x in g) / len(g):.0%}")
    acima = [x for x in hist if x[2] >= alvo]
    out.append("")
    if acima:
        corte = min(x[1] for x in acima)
        out.append(f"O retorno passou de {alvo:.0%} em {len(acima)} concurso(s); o menor prêmio estimado nesses "
                   f"casos foi {brl(corte)}.")
    else:
        out.append(f"Em nenhum desses concursos o retorno passou de {alvo:.0%}.")
    out.append("Prêmios de concursos antigos usam o preço atual da aposta, então o começo do gráfico é aproximado.")
    return "\n".join(out)


def desempenho(user, n_sim=2000):
    c = db()
    tot = dict(c.execute("SELECT tipo, SUM(valor) FROM banca WHERE user=? GROUP BY tipo", (user,)).fetchall())
    meses = c.execute("SELECT substr(data, 1, 7), tipo, SUM(valor) FROM banca WHERE user=? GROUP BY 1, 2 ORDER BY 1",
                      (user,)).fetchall()
    bets = [b for b in check_bets(user) if b["conferidos"]]
    evs, grupos = {}, {}
    esperado = custo_tot = real = 0.0
    for b in bets:
        j = b["jogo"]
        evs.setdefault(j, valor_esperado(j))
        mult = apostas_simples(j, b["dezenas"], b["trevos"])
        n = mult * b["conferidos"]
        esperado += evs[j]["ev"] * n
        custo_tot += custo(j, b["dezenas"], b["trevos"]) * b["conferidos"]
        real += b["ganhos"]
        grupos[j] = grupos.get(j, 0) + n
    out = ["=== MEU DESEMPENHO ===", "", "Banca (lançamentos):",
           f"  Gasto total: {brl(tot.get('Gasto') or 0)} | prêmios: {brl(tot.get('Prêmio') or 0)} | "
           f"saldo: {brl((tot.get('Prêmio') or 0) - (tot.get('Gasto') or 0))}"]
    if meses:
        por = {}
        for mes, tipo, v in meses:
            por.setdefault(mes, {})[tipo] = v
        out.append("  Por mês:")
        for mes, d in por.items():
            g, pr = d.get("Gasto") or 0, d.get("Prêmio") or 0
            out.append(f"    {mes[5:]}/{mes[:4]}: gasto {brl(g):>14}  prêmios {brl(pr):>14}  saldo {brl(pr - g):>14}")
    out += ["", "Apostas salvas já sorteadas:"]
    if not bets:
        out.append("  Nenhuma aposta salva foi sorteada ainda.")
        return "\n".join(out)
    sims = []
    for _ in range(n_sim):
        g = 0.0
        for j, n in grupos.items():
            g += _sorteia_ganho([(p, v) for _, p, v, _ in evs[j]["linhas"]], n)
        sims.append(g)
    melhor_que = sum(x < real for x in sims) / n_sim
    out += [f"  {len(bets)} aposta(s), {sum(b['conferidos'] for b in bets)} conferência(s), custo {brl(custo_tot)}",
            f"  Prêmios reais: {brl(real)} | esperado pela matemática: {brl(esperado)}",
            f"  Você está melhor que {melhor_que:.0%} dos cenários simulados com as mesmas apostas.",
            "  -> " + ("sorte acima da média até agora." if melhor_que >= .6 else
                       "azar até agora (abaixo da média)." if melhor_que <= .4 and real < esperado else
                       "dentro do normal: nem sorte nem azar."),
            "", "Com poucas apostas o resultado depende quase só das faixas menores; leva muito tempo para "
                "a média aparecer."]
    return "\n".join(out)


# ---------------- alertas ----------------
TAREFA = "LuckHarnessAlerta"
SEM_JANELA = 0x08000000


def notificar(titulo, texto):
    """Notificação do Windows via PowerShell (sem dependências extras)."""
    import subprocess
    esc = lambda t: t.replace("'", "''")
    ps = ("[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
          "ContentType=WindowsRuntime] > $null;"
          "$x=[Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
          "[Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
          "$t=$x.GetElementsByTagName('text');"
          f"$t.Item(0).AppendChild($x.CreateTextNode('{esc(titulo)}')) > $null;"
          f"$t.Item(1).AppendChild($x.CreateTextNode('{esc(texto)}')) > $null;"
          "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
          "'{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe')"
          ".Show([Windows.UI.Notifications.ToastNotification]::new($x))")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], creationflags=SEM_JANELA, timeout=60)


def checar_alertas():
    """Avisa uma vez por concurso quando o retorno de um jogo passa do limite configurado."""
    pr = load_prefs()
    lim = pr.get("alerta_limite")
    if not lim:
        return []
    vistos = pr.get("alerta_vistos", {})
    avisos = []
    for j in JOGOS:
        try:
            r = valor_esperado(j)
        except Exception:
            log.warning("valor_esperado(%s) falhou", j, exc_info=True)
            continue
        conc = last_concurso(j)
        if r["retorno"] * 100 >= lim and vistos.get(j) != conc:
            avisos.append(r)
            vistos[j] = conc
    if avisos:
        save_prefs(alerta_vistos=vistos)
        top = max(avisos, key=lambda r: r["retorno"])
        notificar("LuckHarness: oportunidade de retorno",
                  ", ".join(f"{r['jogo']} {r['retorno']:.0%}" for r in avisos) +
                  f" | prêmio estimado {brl(top['estimado'])}")
    return avisos


def modo_alerta():
    for j in JOGOS:
        try:
            sync_draws(j, log=lambda *_: None)
        except Exception:
            log.warning("sync %s falhou", j, exc_info=True)
    checar_alertas()


def agendar_alerta(ativo):
    import subprocess
    if ativo:
        if getattr(sys, "frozen", False):
            cmd = f'"{sys.executable}" --alerta'
        else:
            cmd = f'"{sys.executable.replace("python.exe", "pythonw.exe")}" "{os.path.join(BASE, 'luckharness.py')}" --alerta'
        args = ["schtasks", "/Create", "/F", "/SC", "DAILY", "/ST", "22:30", "/TN", TAREFA, "/TR", cmd]
    else:
        args = ["schtasks", "/Delete", "/F", "/TN", TAREFA]
    r = subprocess.run(args, capture_output=True, text=True, creationflags=SEM_JANELA)
    return r.returncode == 0, (r.stdout or r.stderr).strip()


def alerta_agendado():
    import subprocess
    return subprocess.run(["schtasks", "/Query", "/TN", TAREFA], capture_output=True,
                          creationflags=SEM_JANELA).returncode == 0


# ---------------- fechamento ----------------
def _fech_bits(v, k, t, m, rng=None, progresso=None):
    """Cobertura gulosa em bits. Com rng, empates e candidatos variam a cada rodada (para os reinícios)."""
    alvos = {sum(1 << i for i in c) for c in combinations(range(v), m)}
    todas = [sum(1 << i for i in c) for c in combinations(range(v), k)] if math.comb(v, k) <= 1500 else None
    rnd = rng or random
    cartoes = []
    while alvos:
        if todas:
            cands = todas
            if rng:
                cands = cands[:]
                rng.shuffle(cands)
        else:
            cands = []
            for a in rnd.sample(list(alvos), min(len(alvos), 120)):
                bits = [i for i in range(v) if a >> i & 1]
                pick = set(rnd.sample(bits, t))
                pick.update(rnd.sample([i for i in range(v) if i not in pick], k - t))
                cands.append(sum(1 << i for i in pick))
        best = max(cands, key=lambda c: sum(1 for a in alvos if (a & c).bit_count() >= t))
        alvos = {a for a in alvos if (a & best).bit_count() < t}
        cartoes.append(best)
        if progresso:
            progresso(len(cartoes), len(alvos))
    return cartoes


def _podar(cartoes, v, t, m, rng=None):
    """Remove cartões redundantes: os que só cobrem combinações já cobertas por outros."""
    alvos = [sum(1 << i for i in c) for c in combinations(range(v), m)]
    cobre = [[a for a in alvos if (a & c).bit_count() >= t] for c in cartoes]
    cnt = Counter(a for cs in cobre for a in cs)
    ordem = list(range(len(cartoes)))
    (rng or random).shuffle(ordem)
    fica = set(ordem)
    for i in ordem:
        if all(cnt[a] >= 2 for a in cobre[i]):
            fica.discard(i)
            for a in cobre[i]:
                cnt[a] -= 1
    return [cartoes[i] for i in sorted(fica)]


def _reduzir(cartoes, v, k, t, m, fim, rng):
    """Busca local (recozimento simulado, como Nurmela-Östergård): tira um cartão e troca uma dezena por vez
    nos outros até cobrir tudo de novo. Se cobrir, tenta com um cartão a menos. Para no prazo 'fim'."""
    import time
    alvos = [sum(1 << i for i in c) for c in combinations(range(v), m)]
    melhor = cartoes
    while time.monotonic() < fim and len(melhor) > 1:
        cur = melhor[:]
        cur.pop(rng.randrange(len(cur)))
        cnt = Counter()
        for c in cur:
            for a in alvos:
                if (a & c).bit_count() >= t:
                    cnt[a] += 1
        desc = [a for a in alvos if not cnt[a]]
        temp = 1.0
        while desc and time.monotonic() < fim:
            alvo = rng.choice(desc)
            i = rng.randrange(len(cur))
            c = cur[i]
            # troca 1 dezena: entra uma do alvo que falta no cartão, sai uma do cartão que não está no alvo
            ent = [b for b in range(v) if alvo >> b & 1 and not c >> b & 1]
            sai = [b for b in range(v) if c >> b & 1 and not alvo >> b & 1]
            if not ent or not sai:
                continue
            novo = c ^ (1 << rng.choice(ent)) ^ (1 << rng.choice(sai))
            perde = [x for x in alvos if (x & c).bit_count() >= t and (x & novo).bit_count() < t]
            ganha = [x for x in alvos if (x & novo).bit_count() >= t and (x & c).bit_count() < t]
            delta = sum(1 for x in perde if cnt[x] == 1) - sum(1 for x in ganha if not cnt[x])
            if delta <= 0 or rng.random() < math.exp(-delta / temp):
                for x in perde:
                    cnt[x] -= 1
                for x in ganha:
                    cnt[x] += 1
                cur[i] = novo
                desc = [x for x in alvos if not cnt[x]]
            temp = max(0.05, temp * 0.999)
        if not desc:
            melhor = cur
    return melhor


def fechamento(nums, k, t, m, progresso=None, tempo=0.0, info=None):
    """Gera cartões de k dezenas tais que, se m das dezenas escolhidas forem sorteadas, pelo menos um cartão
    tenha t acertos. Começa pela cobertura gulosa, poda cartões redundantes e, com 'tempo' em segundos,
    tenta reinícios aleatórios guardando o menor conjunto. Cada dezena vira um bit para contar rápido."""
    import time
    nums = sorted(set(nums))
    v = len(nums)
    if not (t <= m <= v and t <= k <= v):
        raise ValueError("Use: garantia <= condição <= dezenas escolhidas e garantia <= cartão <= dezenas.")
    if math.comb(v, m) > 20000:
        raise ValueError("Combinações demais. Escolha menos dezenas ou uma condição menor.")
    guloso = _fech_bits(v, k, t, m, progresso=progresso)
    melhor = _podar(guloso, v, t, m, random.Random(0))
    inicio = time.monotonic()
    fim, semente = inicio + tempo * 0.3, 1
    while time.monotonic() < fim and len(melhor) > 1:
        rng = random.Random(semente)
        c = _podar(_fech_bits(v, k, t, m, rng), v, t, m, rng)
        if len(c) < len(melhor):
            melhor = c
        semente += 1
    if tempo:
        melhor = _reduzir(melhor, v, k, t, m, inicio + tempo, random.Random(semente))
    if info is not None:
        info.update(guloso=len(guloso), final=len(melhor), rodadas=semente)
    return [sorted(nums[i] for i in range(v) if c >> i & 1) for c in melhor]


# ---------------- apostas ----------------
def parse_bet(jogo, txt):
    """Lê '01 02 03 ...' e o que vem depois de '+': trevos da +Milionária, time da Timemania ou mês do
    Dia de Sorte (esses dois vão na mesma lista 'trevos', com um item só)."""
    main, _, tr = txt.partition("+")
    nums = [int(x) for x in re.findall(r"\d+", main)]
    N, K, mn = JOGOS[jogo][:3]
    if M[jogo].get("colunas"):
        cols = [sorted({int(c) for c in re.findall(r"\d", tok)}) for tok in main.split()]
        total = sum(map(len, cols))
        por_col = (1, 1) if total == 7 else (1, 2) if total <= 14 else (2, 3)
        if len(cols) != 7 or not 7 <= total <= 21 or any(not por_col[0] <= len(c) <= por_col[1] for c in cols):
            raise ValueError("Super Sete: 7 colunas separadas por espaço, de 0 a 9; mais números numa coluna com "
                             "vírgula (ex.: 1,5 3 4 0 9 2 7). Total de 7 a 21: com 8 a 14 números, 1 ou 2 por "
                             "coluna; com 15 a 21, 2 ou 3 por coluna.")
        return [c[0] if len(c) == 1 else c for c in cols], []
    if M[jogo].get("zero_e_100"):
        nums = [100 if x == 0 else x for x in nums]   # 00 no volante
    nums = sorted(set(nums))
    lo, hi = universo(jogo)
    if any(not lo <= x <= hi for x in nums):
        raise ValueError(f"{jogo}: as dezenas vão de " + ("00 a 99." if M[jogo].get("zero_e_100") else f"{lo} a {hi}."))
    if not mn <= len(nums) <= MAX_DEZ[jogo]:
        faixa = str(mn) if mn == MAX_DEZ[jogo] else f"{mn} a {MAX_DEZ[jogo]}"
        raise ValueError(f"{jogo}: marque {faixa} dezenas (você marcou {len(nums)}).")
    trevos = []
    if tr.strip() and jogo not in EXTRA and not M[jogo].get("trevos"):
        raise ValueError(f"{jogo}: este jogo não tem nada depois de '+' (sem trevo, time ou mês).")
    if jogo in EXTRA and tr.strip():
        trevos = [_extra(jogo, tr, validar=True)]
    if M[jogo].get("trevos"):
        trevos = sorted({int(x) for x in re.findall(r"\d+", tr)})
        if not 2 <= len(trevos) <= 6 or any(not 1 <= x <= 6 for x in trevos):
            raise ValueError("+Milionária: informe de 2 a 6 trevos (1 a 6) depois de '+', ex.: 01 02 03 04 05 06 + 1 4")
    return nums, trevos


def _norm(t):
    t = unicodedata.normalize("NFKD", str(t)).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", t).strip().upper()


def norm_time(t):
    """Uma grafia por time, no formato da Caixa: 'ABC/RN', 'A B C/RN' e 'AMERICA' com bytes nulos viram 'ABC /RN' e
    'AMERICA /RJ'."""
    if not t:
        return t
    t = _norm(str(t).replace("\x00", " "))
    nome, sep, uf = t.rpartition("/")
    if not sep:
        return t
    nome = nome.strip()
    if re.fullmatch(r"(\w )+\w", nome):   # 'A B C' -> 'ABC'
        nome = nome.replace(" ", "")
    return f"{nome} /{uf.strip()}"


# Lista oficial dos 80 times do biênio (Portaria do Ministério do Esporte; 2026-2028: Portaria MESP nº 32/2026).
# Coloque em timemania_times.json: {"fonte": "...", "vigencia": "2026-2028", "times": ["ABC/RN", ...]}.
TIMES_OFICIAIS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "timemania_times.json")


def times_validos():
    """(times que dá para marcar no volante, True se a lista é a oficial). Sem o arquivo oficial, usa os times já
    sorteados desde o início do biênio: todos saíram do volante atual, então são válidos (a lista fica incompleta,
    mas nunca sugere time de biênio antigo). Com menos de 30 times no biênio, completa com os últimos 200 concursos."""
    try:
        with open(TIMES_OFICIAIS, encoding="utf8") as f:
            ts = [norm_time(t) for t in json.load(f)["times"]]
        if ts:
            return sorted(set(ts)), True
    except (OSError, ValueError, KeyError):
        pass
    rows = [json.loads(m) for (m,) in db().execute("SELECT meta FROM draws WHERE jogo='timemania' AND meta IS NOT "
                                                   "NULL ORDER BY concurso DESC LIMIT 200")]

    def data(m):
        try:
            d, mm, a = map(int, m["data"].split("/"))
            return date(a, mm, d)
        except (KeyError, ValueError, AttributeError):
            return date.min
    bienio = {norm_time(m.get("time")) for m in rows if m.get("time") and data(m) >= BIENIO_TIMEMANIA}
    if len(bienio) >= 30:
        return sorted(bienio), False
    return sorted({norm_time(m.get("time")) for m in rows if m.get("time")}), False


# Início do biênio 2026-2028 da Timemania (Portaria MESP nº 32/2026; vigência de 02/05/2026 a 01/05/2028 segundo
# a imprensa especializada, que cita a portaria). Atualize junto com a lista a cada biênio.
BIENIO_TIMEMANIA = date(2026, 5, 2)


_TIMES = {}


def times_conhecidos():
    """Times que já saíram na Timemania. Guardado em memória até chegar concurso novo (ler o JSON de todos os
    concursos a cada cartão deixava o teste no passado lento)."""
    chave = db().execute("SELECT COUNT(*), MAX(concurso) FROM draws WHERE jogo='timemania'").fetchone()
    if chave not in _TIMES:
        rows = db().execute("SELECT DISTINCT json_extract(meta, '$.time') FROM draws WHERE jogo='timemania'").fetchall()
        _TIMES.clear()
        _TIMES[chave] = sorted(r[0] for r in rows if r[0])
    return _TIMES[chave]


def _extra(jogo, txt, validar=False):
    """Normaliza o time (ex.: 'Vitória' -> 'VITORIA /BA') ou o mês (ex.: '10', 'out' -> 'Outubro')."""
    if not txt:
        return None
    t = _norm(txt)
    if M[jogo].get("extra", ("",))[0] == "mes":
        if t.isdigit() and 1 <= int(t) <= 12:
            return MESES[int(t) - 1]
        achou = [m for m in MESES if t and _norm(m).startswith(t)]
        if len(achou) == 1:
            return achou[0]
        if len(achou) > 1 and validar:   # "ma" = março ou maio, "ju" = junho ou julho
            raise ValueError(f"Dia de Sorte: '{txt}' pode ser {' ou '.join(achou)}. Escreva mais letras ou o número do mês.")
        if validar:
            raise ValueError("Dia de Sorte: informe o mês depois de '+', ex.: 01 02 03 04 05 06 07 + outubro")
        return txt
    if not validar:
        return norm_time(txt)
    times = times_conhecidos()
    if not times:
        return t
    for cond in (lambda x: _norm(x) == t, lambda x: _norm(x).split(" /")[0] == t, lambda x: _norm(x).startswith(t)):
        achou = [x for x in times if cond(x)]
        if len(achou) == 1:
            return achou[0]
        if len(achou) > 1:
            raise ValueError(f"Timemania: mais de um time combina com '{txt}': {', '.join(achou[:8])}. Inclua o estado.")
    raise ValueError(f"Timemania: time '{txt}' não encontrado. Use o nome como aparece nos resultados, ex.: {times[0]}")


def extra_aleatorio(jogo):
    """Time/mês sorteado para apostas sugeridas: nenhum tem mais chance que outro, então qualquer um serve."""
    if M[jogo].get("extra", ("",))[0] == "time":
        times = times_validos()[0]
        return [random.choice(times)] if times else []
    if M[jogo].get("extra", ("",))[0] == "mes":
        return [random.choice(MESES)]
    if M[jogo].get("trevos"):
        return sorted(random.sample(range(1, 7), 2))
    return []


def editar_extra(user, bid, txt):
    """Troca trevos/time/mês de uma aposta salva, validando como se fosse digitada de novo."""
    row = db().execute("SELECT jogo, dezenas FROM bets WHERE id=? AND user=?", (bid, user)).fetchone()
    if not row:
        raise ValueError("Aposta não encontrada.")
    jogo, dz = row[0], json.loads(row[1])
    if jogo not in EXTRA and not M[jogo].get("trevos"):
        raise ValueError(f"{jogo} não tem trevos, time nem mês para editar.")
    _, tv = parse_bet(jogo, " ".join(map(str, dz)) + " + " + txt)
    if not tv:
        raise ValueError("Informe o valor depois do '+'.")
    with db() as c:
        c.execute("UPDATE bets SET trevos=? WHERE id=? AND user=?", (json.dumps(tv), bid, user))
        # mais trevos na +Milionária mudam o custo: o gasto lançado na banca acompanha
        c.execute("UPDATE banca SET valor=? WHERE user=? AND tipo='Gasto' AND descr=?",
                  (custo(jogo, dz, tv), user, f"Aposta #{bid} {jogo}"))
    return jogo, tv


REPO = "uilsonrios/LuckHarness"


def _versao(v):
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3])


def checar_update():
    """Última versão publicada nos Releases do GitHub, se for mais nova que esta. None se não houver ou sem rede."""
    try:
        r = _get(f"https://api.github.com/repos/{REPO}/releases/latest", 5)
    except Exception:
        log.info("sem acesso aos releases", exc_info=True)
        return None
    tag = r.get("tag_name") or ""
    if _versao(tag) > _versao(__version__):
        return {"versao": tag.lstrip("v"), "url": r.get("html_url") or f"https://github.com/{REPO}/releases/latest",
                "notas": (r.get("body") or "").strip()}
    return None


def backup_db(path):
    """Cópia consistente do banco (funciona com o app aberto)."""
    with db() as src, sqlite3.connect(path) as dst:
        src.backup(dst)
    dst.close()


def restore_db(path):
    """Substitui o banco atual pelo backup, depois de checar que é um banco do LuckHarness."""
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as src:
        tabelas = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"users", "bets", "draws", "banca"} <= tabelas:
            raise ValueError("Esse arquivo não é um backup do LuckHarness.")
        with db() as dst:
            src.backup(dst)
    src.close()


def apostas_simples(jogo, nums, trevos):
    """Quantas apostas simples a aposta equivale (dezenas a mais e trevos a mais multiplicam)."""
    if M[jogo].get("colunas"):
        return math.prod(len(c) for c in colunas(nums))
    mult = math.comb(len(nums), JOGOS[jogo][2])
    if M[jogo].get("trevos") and trevos:
        mult *= math.comb(len(trevos), 2)
    return mult


def custo(jogo, nums, trevos):
    return JOGOS[jogo][4] * apostas_simples(jogo, nums, trevos)


NOMES = {j: m["nome"] for j, m in M.items()}


# dias de sorteio (0 = segunda). Feriados e concursos especiais podem mudar a data: confira na lotérica.
DIAS_SORTEIO = {j: m["dias"] for j, m in M.items()}
DIAS = ("segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo")


def proximo_sorteio(jogo, hoje=None):
    """Data do próximo sorteio pelo calendário fixo (hoje conta: o sorteio é à noite)."""
    from datetime import timedelta
    hoje = hoje or date.today()
    return next(d for d in (hoje + timedelta(i) for i in range(8)) if d.weekday() in DIAS_SORTEIO[jogo])


def texto_calendario(hoje=None):
    hoje = hoje or date.today()
    out = ["Próximos sorteios (à noite, horário de Brasília; as apostas fecham cerca de 1 h antes):"]
    for j in sorted(DIAS_SORTEIO, key=lambda j: proximo_sorteio(j, hoje)):
        d = proximo_sorteio(j, hoje)
        quando = "hoje" if d == hoje else DIAS[d.weekday()] + " " + d.strftime("%d/%m")
        dias = ", ".join(DIAS[x][:3] for x in DIAS_SORTEIO[j])
        out.append(f"  {NOMES[j]:<14} {quando:<17} (sorteia {dias})")
    out.append("Feriados e concursos especiais podem mudar a data.")
    return "\n".join(out)


def brl(v):
    return "R$ " + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def colunas(card):
    """Super Sete: cada coluna como lista de dígitos (aposta simples tem um por coluna)."""
    return [c if isinstance(c, list) else [c] for c in card]


def dist_colunas(card, sorteio):
    """Super Sete: quantas apostas simples contidas na aposta fazem h colunas certas (h = 0..7)."""
    dist = [1]
    for col, d in zip(colunas(card), sorteio):
        ac = 1 if d in col else 0
        nova = [0] * (len(dist) + 1)
        for h, q in enumerate(dist):
            nova[h] += q * (len(col) - ac)
            nova[h + 1] += q * ac
        dist = nova
    return dist


def contidas(jogo, n, h, nt=0, th=0):
    """Apostas simples dentro de uma aposta de n números com h acertos (e, na +Milionária, nt trevos marcados com th
    certos): lista de (acertos, trevos certos, quantidade) só para as faixas premiadas. Fonte única para a
    conferência, o teste no passado e a 'aposta mínima' (antes eram três cópias da mesma conta)."""
    mn, fx = JOGOS[jogo][2], JOGOS[jogo][3]
    pares = ({2: math.comb(th, 2), 1: th * (nt - th), 0: math.comb(nt - th, 2)}
             if M[jogo].get("trevos") and nt else {th: 1})
    out = []
    for j in fx:
        for t2, q2 in pares.items():
            q = math.comb(h, j) * math.comb(n - h, mn - j) * q2
            if q:
                out.append((j, t2, q))
    return out


def espelho(nums):
    """Lotomania: a aposta-espelho marca os 50 números que ficaram de fora (é uma nova aposta, com preço próprio)."""
    return [x for x in range(1, 101) if x not in set(nums)]


def fmt_bet(jogo, nums, trevos):
    if M[jogo].get("colunas"):
        s = " ".join(",".join(map(str, c)) for c in colunas(nums))
    else:
        s = " ".join("00" if M[jogo].get("zero_e_100") and x == 100 else f"{x:02d}" for x in nums)
    return s + (f"  + {' '.join(map(str, trevos))}" if trevos else "")


def save_bet(user, jogo, nums, trevos, desde):
    with db() as c:
        cur = c.execute("INSERT INTO bets(user, jogo, dezenas, trevos, desde, criado) VALUES(?,?,?,?,?,?)",
                        (user, jogo, json.dumps(nums), json.dumps(trevos), desde, date.today().isoformat()))
        return cur.lastrowid


def premio(jogo, hits, th, premios):
    """Acha a faixa de prêmio do concurso para 'hits' acertos (e 'th' trevos na +Milionária)."""
    for desc, val in premios:
        d = desc.lower()
        m = re.match(r"(\d+) acertos?", d)
        if not m or int(m.group(1)) != hits:
            continue
        if M[jogo].get("trevos"):
            r = d[m.end():]
            if not (("2 trevos" in r and th == 2) or ("1 ou nenhum" in r and th <= 1) or
                    ("+ 1 trevo" in r and th == 1)):
                continue
        return desc, val
    return None


def _sorteios(jogo, rows):
    """Cada sorteio conferível como (rótulo, dezenas, meta). Dupla Sena vira dois, cada um com sua metade dos prêmios."""
    for conc, d, meta in rows:
        d, meta = json.loads(d), json.loads(meta) if meta else {}
        d2 = meta.get("dezenas2")
        if M[jogo].get("sorteios") != 2 or not d2:
            yield conc, d, meta
            continue
        pr = meta.get("premios", [])
        h = len(pr) // 2
        yield f"{conc} (1º sorteio)", d, {**meta, "premios": pr[:h]}
        yield f"{conc} (2º sorteio)", d2, {**meta, "premios": pr[h:]}


def check_bets(user):
    c = db()
    bets = c.execute("SELECT id, jogo, dezenas, trevos, desde FROM bets WHERE user=? ORDER BY id DESC",
                     (user,)).fetchall()
    out = []
    for bid, jogo, dz, tv, desde in bets:
        dz, tv = json.loads(dz), json.loads(tv or "[]")
        rows = c.execute("SELECT concurso, dezenas, meta FROM draws WHERE jogo=? AND concurso>=? ORDER BY concurso",
                         (jogo, desde)).fetchall()
        res, melhor, ganhos = [], 0, 0.0
        for conc, d, meta in _sorteios(jogo, rows):
            if M[jogo].get("colunas"):
                h, th = sum(b in c for c, b in zip(colunas(dz), d)), 0
            else:
                h, th = len(set(dz) & set(d)), len(set(tv) & set(meta.get("trevos", [])))
            melhor = max(melhor, h)
            if jogo in EXTRA and tv and meta.get(EXTRA[jogo][0]) == tv[0]:
                desc = EXTRA[jogo][1]
                val = dict(meta.get("premios", [])).get(desc) or 0
                ganhos += val
                res.append((conc, desc, val))
            if M[jogo].get("colunas"):   # aposta com mais números = todas as apostas simples contidas nela
                for hh, qtd in enumerate(dist_colunas(dz, d)):
                    if hh not in JOGOS[jogo][3] or not qtd:
                        continue
                    p = premio(jogo, hh, 0, meta.get("premios", []))
                    val = (p[1] or 0) * qtd if p else 0
                    ganhos += val
                    desc = p[0] if p else f"{hh} acertos"
                    res.append((conc, desc if qtd == 1 else f"{qtd}x {desc}", val))
                continue
            # aposta com mais dezenas/trevos = todas as apostas simples contidas nela
            for j, t2, qtd in contidas(jogo, len(dz), h, len(tv), th):
                p = premio(jogo, j, t2, meta.get("premios", []))
                if M[jogo].get("trevos") and not p:
                    continue
                val = (p[1] or 0) * qtd if p else 0
                ganhos += val
                desc = p[0] if p else f"{j} acertos"
                res.append((conc, desc if qtd == 1 else f"{qtd}x {desc}", val))
        out.append({"id": bid, "jogo": jogo, "dezenas": dz, "trevos": tv, "desde": desde,
                    "conferidos": len(rows), "melhor": melhor, "ganhos": ganhos, "premiadas": res})
    return out


def export_pdf(path, title, lines):
    """Gera PDF com Pillow (já é dependência do app): desenha o texto em páginas A4."""
    from PIL import Image, ImageDraw, ImageFont
    W, H, M = 1240, 1754, 90
    try:
        f = ImageFont.truetype("C:/Windows/Fonts/consola.ttf", 22)
        fb = ImageFont.truetype("C:/Windows/Fonts/segoeuib.ttf", 34)
    except Exception:
        f = fb = ImageFont.load_default()
    pages, y, d = [], H, None
    for line in lines:
        if y > H - M:
            pages.append(Image.new("RGB", (W, H), "white"))
            d = ImageDraw.Draw(pages[-1])
            d.text((M, M), title, font=fb, fill="black")
            y = M + 70
        d.text((M, y), line, font=f, fill="black")
        y += 32
    if not pages:
        pages.append(Image.new("RGB", (W, H), "white"))
        ImageDraw.Draw(pages[0]).text((M, M), title, font=fb, fill="black")
    pages[0].save(path, save_all=True, append_images=pages[1:], resolution=150)


# ---------------- banca ----------------
def add_banca(user, tipo, valor, descr):
    with db() as c:
        c.execute("INSERT INTO banca(user, data, tipo, valor, descr) VALUES(?,?,?,?,?)",
                  (user, date.today().isoformat(), tipo, valor, descr))


def banca_mes(user):
    rows = db().execute("SELECT tipo, SUM(valor) FROM banca WHERE user=? AND data LIKE ? GROUP BY tipo",
                        (user, date.today().strftime("%Y-%m") + "%")).fetchall()
    d = dict(rows)
    return d.get("Gasto") or 0.0, d.get("Prêmio") or 0.0


# ---------------- IA ----------------
SYSTEM = ("Você é Harness IA, analista de loterias do LuckHarness. Responda em português, com rigor matemático "
          "(probabilidade, combinatória, valor esperado, testes estatísticos). Use as contas já feitas pelo app (probabilidades, valor esperado, popularidade): "
          "não refaça cálculos, explique-os. Use os dados, o modelo de "
          "aprendizado, as apostas e a banca enviados no contexto e lembre o que já foi conversado. Seja honesto: "
          "loterias são aleatórias e nenhum modelo prevê o próximo sorteio; ajude com cobertura, custo e gestão de banca. "
          "O bloco FATOS DO APP é a fonte de verdade: nunca o contrarie, nunca recomende números quentes, frios ou "
          "atrasados e nunca invente números de aposta; indique só as apostas que o app gerou. "
          "Quando o contexto trouxer o plano do Otimizador ou o modelo de popularidade, baseie a recomendação neles: "
          "escolha de jogo pela chance de prêmio por real, cartões espalhados, fechamento com garantia e números "
          "pouco populares (dividem menos o prêmio). Nunca prometa acerto nem lucro. "
          "Escreva em texto simples, sem markdown (nada de **, ##, tabelas com | ou emojis): o chat não formata. "
          "Use parágrafos curtos e listas com hífen.")


def ask_ollama(model, messages, on_chunk):
    # think=False: sem isso modelos de raciocínio passam minutos "pensando" antes de escrever a resposta
    # temperatura baixa: respostas consistentes, menos "criatividade" com números
    # num_ctx: o padrão do Ollama (4096 tokens) não cabe análise + sugestões + plano + fatos
    body = json.dumps({"model": model, "stream": True, "think": False,
                       "options": {"temperature": 0.2, "num_ctx": 8192},
                       "messages": [{"role": "system", "content": SYSTEM}] + messages}).encode()
    req = urllib.request.Request(OLLAMA_URL + "/api/chat", body, {"Content-Type": "application/json"})
    full = []
    try:
        resp = urllib.request.urlopen(req, timeout=600)
    except urllib.error.HTTPError as e:
        motivo = e.read().decode("utf8", "replace")
        log.warning("Ollama %s: %s", e.code, motivo[:500])
        if "context size" in motivo:
            raise RuntimeError("a pergunta ficou maior que a memória de contexto do modelo. Clique em 'Limpar' "
                               "no chat e tente de novo") from None
        raise RuntimeError(f"o Ollama recusou o pedido (HTTP {e.code}): {motivo[:200]}") from None
    with resp as r:
        for line in r:
            if line.strip():
                t = json.loads(line).get("message", {}).get("content", "")
                full.append(t)
                on_chunk(t)
    return "".join(full)


def list_models():
    try:
        return [m["name"] for m in _get(OLLAMA_URL + "/api/tags", 5)["models"]]
    except Exception:
        return []


def save_chat(user, role, content):
    with db() as c:
        c.execute("INSERT INTO chats(user, role, content) VALUES(?,?,?)", (user, role, content))


def load_chat(user, n=20):
    rows = db().execute("SELECT role, content FROM chats WHERE user=? ORDER BY id DESC LIMIT ?", (user, n)).fetchall()
    return [{"role": r, "content": c} for r, c in reversed(rows)]
