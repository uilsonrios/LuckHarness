"""luckHarness - análise estatística de loterias com login e IA local (Ollama)."""
import csv, math, os, queue, re, sys, threading, webbrowser
from datetime import date
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, filedialog, simpledialog

from core import *  # noqa: F401,F403
from core import __version__, log
from otimizador import *  # noqa: F401,F403
import laboratorio as lab


# ---------------- interface ----------------
BG, CARD, FIELD, FG, MUTED, ACCENT = "#0f1117", "#181b24", "#232734", "#e6e8ee", "#8a90a2", "#00d4aa"
WARN, IA_COLOR = "#ffb454", "#b8a4ff"
FONT = "Segoe UI"


class JogoBox(ttk.Combobox):
    """Mostra o nome amigável do jogo, mas get()/set() continuam usando a chave interna (ex.: 'lotofacil')."""

    def __init__(self, parent, **kw):
        self._chaves = list(JOGOS)
        super().__init__(parent, values=[NOMES.get(j, j) for j in self._chaves], state="readonly", **kw)

    def get(self):
        nome = super().get()
        return next((j for j in self._chaves if NOMES.get(j, j) == nome), nome)

    def set(self, jogo):
        super().set(NOMES.get(jogo, jogo))


def load_avatar(size):
    try:
        from PIL import Image, ImageTk
        return ImageTk.PhotoImage(Image.open(AVATAR).resize((size, size), Image.LANCZOS))
    except Exception:
        try:
            img = tk.PhotoImage(file=AVATAR)
            return img.subsample(max(1, img.width() // size))
        except Exception:
            return None


def theme(root):
    root.configure(bg=BG)
    try:
        root.iconbitmap(os.path.join(BASE, "avatar.ico"))
    except Exception:
        pass
    st = ttk.Style(root)
    st.theme_use("clam")
    st.configure(".", background=BG, foreground=FG, font=(FONT, 10), borderwidth=0)
    st.configure("Card.TFrame", background=CARD)
    st.configure("TLabel", background=BG, foreground=FG)
    st.configure("Card.TLabel", background=CARD, foreground=FG)
    st.configure("Muted.TLabel", background=CARD, foreground=MUTED, font=(FONT, 9))
    st.configure("Title.TLabel", background=CARD, foreground=FG, font=(FONT, 18, "bold"))
    st.configure("Big.TLabel", background=CARD, foreground=FG, font=(FONT, 12, "bold"))
    st.configure("TEntry", fieldbackground=FIELD, foreground=FG, insertcolor=FG, padding=8, bordercolor=FIELD,
                 lightcolor=FIELD, darkcolor=FIELD)
    st.map("TEntry", bordercolor=[("focus", ACCENT)], lightcolor=[("focus", ACCENT)])
    st.configure("TSpinbox", fieldbackground=FIELD, foreground=FG, arrowcolor=FG, padding=4, bordercolor=FIELD,
                 lightcolor=FIELD, darkcolor=FIELD, background=FIELD)
    st.configure("TCombobox", fieldbackground=FIELD, foreground=FG, arrowcolor=FG, padding=4, bordercolor=FIELD,
                 lightcolor=FIELD, darkcolor=FIELD, background=FIELD)
    st.map("TCombobox", fieldbackground=[("readonly", FIELD)], foreground=[("readonly", FG)])
    root.option_add("*TCombobox*Listbox.background", FIELD)
    root.option_add("*TCombobox*Listbox.foreground", FG)
    st.configure("Accent.TButton", background=ACCENT, foreground="#0b0d12", font=(FONT, 10, "bold"), padding=(14, 8))
    st.map("Accent.TButton", background=[("active", "#00f0c0")])
    st.configure("Ghost.TButton", background=FIELD, foreground=FG, padding=(14, 8))
    st.map("Ghost.TButton", background=[("active", "#2e3344")])
    st.configure("Small.TButton", background=FIELD, foreground=FG, padding=(8, 4), font=(FONT, 9))
    st.map("Small.TButton", background=[("active", "#2e3344")])
    st.configure("TPanedwindow", background=BG)
    st.configure("Card.TCheckbutton", background=CARD, foreground=FG, indicatorbackground=FIELD,
                 indicatorforeground=ACCENT)
    st.map("Card.TCheckbutton", background=[("active", CARD)], indicatorbackground=[("selected", FIELD)])
    st.configure("TNotebook", background=BG, borderwidth=0, tabmargins=0, bordercolor=BG, lightcolor=BG,
                 darkcolor=BG)
    st.configure("TNotebook.Tab", background=FIELD, foreground=MUTED, padding=(16, 7), borderwidth=0,
                 bordercolor=BG, lightcolor=FIELD)
    st.map("TNotebook.Tab", background=[("selected", CARD)], foreground=[("selected", ACCENT)])
    st.configure("Treeview", background=CARD, fieldbackground=CARD, foreground=FG, rowheight=26, borderwidth=0,
                 bordercolor=CARD, lightcolor=CARD, darkcolor=CARD)
    st.configure("Treeview.Heading", background=FIELD, foreground=FG, font=(FONT, 9, "bold"), relief="flat")
    st.map("Treeview", background=[("selected", "#2e3344")])
    st.map("Treeview.Heading", background=[("active", "#2e3344")])


def text_box(parent, **kw):
    return scrolledtext.ScrolledText(parent, bg=CARD, fg=FG, insertbackground=FG, relief="flat",
                                     borderwidth=0, padx=12, pady=10, **kw)


def _mix(c1, c2, t):
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(a, b))


class Chart(tk.Canvas):
    """Gráficos desenhados direto no Canvas (sem dependências extras); redesenha ao redimensionar."""

    def __init__(self, parent):
        super().__init__(parent, bg=CARD, highlightthickness=0)
        self.spec = None
        self.bind("<Configure>", lambda e: self.draw())

    def show(self, kind, **kw):
        self.spec = (kind, kw)
        self.draw()

    def draw(self):
        self.delete("all")
        W, H = self.winfo_width(), self.winfo_height()
        if not self.spec:
            self.create_text(W / 2, H / 2, text="Clique em Analisar para gerar os gráficos.", fill=MUTED,
                             font=(FONT, 11))
            return
        kind, kw = self.spec
        getattr(self, "_" + kind)(W, H, **kw)

    def _bars(self, W, H, labels, values, title, ref=None, reflabel="", color=ACCENT):
        L, R, T, B = 48, 16, 40, 34
        self.create_text(L, 18, text=title, anchor="w", fill=FG, font=(FONT, 11, "bold"))
        mx = max(max(values, default=0), ref or 0) or 1
        ph, n = H - T - B, max(1, len(values))
        bw = (W - L - R) / n
        for g in range(5):
            y = T + ph * g / 4
            val = mx * (1 - g / 4)
            self.create_line(L, y, W - R, y, fill=FIELD)
            self.create_text(L - 6, y, text=f"{val:.0f}" if mx >= 10 else f"{val:.1f}", anchor="e", fill=MUTED,
                             font=(FONT, 8))
        step = max(1, math.ceil(n * 24 / max(1, W - L - R)))
        for i, (lb, v) in enumerate(zip(labels, values)):
            x0, x1 = L + i * bw + bw * 0.15, L + (i + 1) * bw - bw * 0.15
            self.create_rectangle(x0, T + ph * (1 - v / mx), x1, H - B, fill=color, width=0)
            if i % step == 0:
                self.create_text((x0 + x1) / 2, H - B + 12, text=str(lb), fill=MUTED, font=(FONT, 8))
        if ref:
            y = T + ph * (1 - ref / mx)
            self.create_line(L, y, W - R, y, fill=WARN, dash=(4, 3), width=2)
            self.create_text(W - R, y - 9, text=reflabel, anchor="e", fill=WARN, font=(FONT, 8))

    def _line(self, W, H, xs, ys, title, ref=None, reflabel=""):
        L, R, T, B = 56, 16, 40, 34
        self.create_text(L, 18, text=title, anchor="w", fill=FG, font=(FONT, 11, "bold"))
        if len(ys) < 2:
            return
        lo, hi = min(ys + [ref or min(ys)]), max(ys + [ref or max(ys)])
        pad = (hi - lo) * .1 or .05
        lo, hi = max(0, lo - pad), hi + pad
        ph, pw = H - T - B, W - L - R
        for g in range(5):
            y = T + ph * g / 4
            self.create_line(L, y, W - R, y, fill=FIELD)
            self.create_text(L - 6, y, text=f"{hi - (hi - lo) * g / 4:.0%}", anchor="e", fill=MUTED, font=(FONT, 8))
        pts = []
        for i, v in enumerate(ys):
            pts += [L + pw * i / (len(ys) - 1), T + ph * (1 - (v - lo) / (hi - lo))]
        self.create_line(*pts, fill=ACCENT, width=2)
        for i in (0, len(xs) // 2, len(xs) - 1):
            self.create_text(L + pw * i / (len(xs) - 1), H - B + 12, text=str(xs[i]), fill=MUTED, font=(FONT, 8))
        if ref:
            y = T + ph * (1 - (ref - lo) / (hi - lo))
            self.create_line(L, y, W - R, y, fill=WARN, dash=(4, 3), width=2)
            self.create_text(W - R, y - 9, text=reflabel, anchor="e", fill=WARN, font=(FONT, 8))

    def _heat(self, W, H, cols, title):
        L, T = 80, 50
        self.create_text(16, 18, text=title, anchor="w", fill=FG, font=(FONT, 11, "bold"))
        cw, ch = (W - L - 16) / 10, (H - T - 16) / 7
        mx = max((v for c in cols for v in c.values()), default=1) or 1
        mn = min((c.get(i, 0) for c in cols for i in range(10)), default=0)
        for i in range(10):
            self.create_text(L + cw * (i + .5), T - 12, text=str(i), fill=MUTED, font=(FONT, 9))
        for r, c in enumerate(cols):
            self.create_text(L - 10, T + ch * (r + .5), text=f"Coluna {r+1}", anchor="e", fill=MUTED, font=(FONT, 9))
            for i in range(10):
                v = c.get(i, 0)
                t = (v - mn) / (mx - mn) if mx > mn else .5
                x, y = L + cw * i, T + ch * r
                self.create_rectangle(x + 2, y + 2, x + cw - 2, y + ch - 2, fill=_mix(FIELD, ACCENT, t), width=0)
                self.create_text(x + cw / 2, y + ch / 2, text=str(v), fill=FG if t < .6 else "#0b0d12",
                                 font=(FONT, 9))


class Login(tk.Tk):
    def __init__(self):
        super().__init__()
        theme(self)
        self.title("LuckHarness")
        self.geometry("380x560")
        self.resizable(False, False)
        card = ttk.Frame(self, style="Card.TFrame", padding=28)
        card.place(relx=0.5, rely=0.5, anchor="center", width=330, height=510)
        self.av = load_avatar(120)
        if self.av:
            tk.Label(card, image=self.av, bg=CARD).pack(pady=(0, 10))
        ttk.Label(card, text="LuckHarness", style="Title.TLabel").pack()
        ttk.Label(card, text="Estatística e probabilidade para loterias", style="Muted.TLabel").pack(pady=(0, 16))
        ttk.Label(card, text="Usuário", style="Muted.TLabel").pack(anchor="w")
        self.u = ttk.Entry(card); self.u.pack(fill="x", pady=(2, 10))
        ttk.Label(card, text="Senha", style="Muted.TLabel").pack(anchor="w")
        self.p = ttk.Entry(card, show="•"); self.p.pack(fill="x", pady=(2, 10))
        self.keep = tk.BooleanVar(value=True)
        ttk.Checkbutton(card, text="Manter conectado", variable=self.keep, style="Card.TCheckbutton").pack(
            anchor="w", pady=(0, 14))
        ttk.Button(card, text="Entrar", style="Accent.TButton", command=self.login).pack(fill="x")
        ttk.Button(card, text="Criar conta", style="Ghost.TButton", command=self.create).pack(fill="x", pady=(8, 0))
        self.p.bind("<Return>", lambda e: self.login())
        last = load_prefs().get("user")
        if last:
            self.u.insert(0, last)
            self.p.focus()
        else:
            self.u.focus()
        self.user = None

    def login(self):
        if check_login(self.u.get(), self.p.get()):
            self.user = self.u.get()
            save_prefs(user=self.user)
            if self.keep.get():
                remember(self.user)
            self.destroy()
        else:
            messagebox.showerror("Erro", "Usuário ou senha inválidos.")

    def create(self):
        try:
            register(self.u.get().strip(), self.p.get())
            messagebox.showinfo("OK", "Conta criada. Clique em Entrar.")
        except sqlite3.IntegrityError:
            messagebox.showerror("Erro", "Usuário já existe.")
        except ValueError as e:
            messagebox.showerror("Erro", str(e))


QUICK = [
    ("Explicar análise", "Explique a análise atual de forma simples: o que os números mostram e o que NÃO mostram."),
    ("Custo de cobertura", "Compare custo e probabilidade de apostar mais dezenas neste jogo (mínimo até +4 dezenas)."),
    ("Banca R$ 100", "Monte uma gestão de banca para R$ 100 por mês neste jogo, com regras de parada."),
    ("Minhas apostas", "Avalie minhas apostas salvas e os resultados delas. Vale continuar com elas?"),
    ("Avaliar sugestões", "Avalie as apostas sugeridas: chances, valor esperado, custo efetivo e popularidade. "
                          "Qual delas é a melhor escolha e por quê?"),
    ("Melhor jogo agora", "Qual jogo tem o melhor retorno agora e vale a pena apostar nele? Use a comparação."),
]


class App(tk.Tk):
    def __init__(self, user):
        super().__init__()
        self._fila = queue.Queue()
        self._drenar()
        theme(self)
        self.user = user
        self.title(f"LuckHarness {__version__}")
        self.geometry("1280x800")
        self.minsize(1000, 640)
        self.last_report = ""
        self.last_sugs = []
        self.dados = {}
        self.logout = False
        pr = load_prefs()
        self.model = pr.get("model") or OLLAMA_MODEL

        head = ttk.Frame(self, style="Card.TFrame", padding=(16, 10)); head.pack(fill="x")
        ttk.Label(head, text="LuckHarness", style="Title.TLabel").pack(side="left")
        # Backup/Restaurar/Sair num menu só: a barra não corta em telas de 1000 px
        mb = ttk.Menubutton(head, text=f"● {user} ▾", style="Ghost.TButton")
        menu = tk.Menu(mb, tearoff=0)
        menu.add_command(label="Fazer backup...", command=self.backup)
        menu.add_command(label="Restaurar backup...", command=self.restaurar)
        menu.add_separator()
        menu.add_command(label="Sair da conta", command=self.sair)
        mb["menu"] = menu; mb.pack(side="right")
        self.gasto_lbl = ttk.Label(head, text="", style="Muted.TLabel"); self.gasto_lbl.pack(side="right", padx=12)

        jogo0 = pr.get("jogo") if pr.get("jogo") in JOGOS else "lotofacil"
        bar = ttk.Frame(self, padding=(16, 12)); bar.pack(fill="x")
        ttk.Label(bar, text="Jogo").pack(side="left")
        self.jogo = JogoBox(bar, width=13)
        self.jogo.set(jogo0); self.jogo.pack(side="left", padx=(6, 14))
        self.jogo.bind("<<ComboboxSelected>>", lambda e: self.on_jogo())
        ttk.Label(bar, text="Concursos").pack(side="left")
        self.n = tk.IntVar(value=pr.get("n", 300))
        ttk.Spinbox(bar, from_=10, to=5000, increment=10, textvariable=self.n, width=5).pack(side="left", padx=(6, 14))
        ttk.Label(bar, text="Dezenas/aposta").pack(side="left")
        self.bet = tk.IntVar(value=pr.get("bet", JOGOS[jogo0][2]))
        ttk.Spinbox(bar, from_=5, to=50, textvariable=self.bet, width=4).pack(side="left", padx=(6, 14))
        ttk.Button(bar, text="Sugerir apostas", style="Accent.TButton", command=self.sug).pack(side="right")
        ttk.Button(bar, text="Analisar", style="Ghost.TButton", command=self.run).pack(side="right", padx=6)
        ttk.Button(bar, text="Baixar resultados", style="Ghost.TButton", command=self.sync).pack(side="right")
        ttk.Button(bar, text="Limpar", style="Ghost.TButton",
                   command=lambda: self.out.delete("1.0", "end")).pack(side="right", padx=6)
        self.protocol("WM_DELETE_WINDOW", self.fechar)

        body = ttk.PanedWindow(self, orient="horizontal"); body.pack(fill="both", expand=True, padx=16, pady=(0, 16))
        self.nb = ttk.Notebook(body); body.add(self.nb, weight=3)
        self.out = text_box(self.nb, font=("Consolas", 10))
        self.out.tag_configure("head", foreground=ACCENT, font=("Consolas", 11, "bold"))
        self.out.tag_configure("err", foreground="#ff6b6b")
        self.build_semana()
        self.nb.add(self.out, text="Análise")
        self.build_charts()
        self.build_bets()
        self.build_fech()
        self.build_retorno()
        self.build_plano()
        self.build_otim()
        self.build_lab()
        self.build_banca()
        self.build_chat(body)

        self.refresh_banca()
        if not pr.get(f"assistente_{user}"):
            self.after(400, self.assistente)
        self.bg(self.auto_update)
        self.bg(self.ver_update)

    def ver_update(self):
        u = checar_update()
        if not u:
            return
        self.log(f"Nova versão {u['versao']} disponível: {u['url']}", "head")

        def perguntar():
            if messagebox.askyesno("Atualização", f"A versão {u['versao']} do LuckHarness está disponível "
                                                  f"(você tem a {__version__}).\n\nAbrir a página de download?"):
                webbrowser.open(u["url"])
        self.after(0, perguntar)

    def after(self, ms, func=None, *args):
        """Threads em background não podem mexer no Tk: o que vier delas entra numa fila que a thread
        principal executa (evita 'main thread is not in main loop')."""
        if threading.current_thread() is threading.main_thread():
            return super().after(ms, func, *args)
        self._fila.put(lambda: func(*args))

    def _drenar(self):
        while True:
            try:
                fn = self._fila.get_nowait()
            except queue.Empty:
                break
            try:
                fn()
            except Exception:
                log.exception("atualização da tela")
        super().after(50, self._drenar)

    # ---------- abas ----------
    def tab(self, title):
        f = ttk.Frame(self.nb, style="Card.TFrame", padding=12)
        self.nb.add(f, text=title)
        return f

    def build_semana(self):
        """Tela inicial: quanto quero gastar -> cartões prontos, chances e botão 'Apostei'."""
        f = self.tab("Minha aposta da semana")
        row = ttk.Frame(f, style="Card.TFrame"); row.pack(fill="x", pady=(0, 8))
        ttk.Label(row, text="Quanto quer gastar (R$)", style="Card.TLabel").pack(side="left")
        self.s_val = ttk.Entry(row, width=8); self.s_val.pack(side="left", padx=6)
        self.s_val.insert(0, str(load_prefs().get("orcamento") or 15))
        ttk.Button(row, text="Montar minha aposta", style="Accent.TButton", command=self.montar_semana).pack(
            side="left", padx=6)
        self.s_ok = ttk.Button(row, text="Apostei", style="Ghost.TButton", command=self.apostei, state="disabled")
        self.s_ok.pack(side="left")
        ttk.Button(row, text="Calendário de sorteios", style="Small.TButton", command=lambda: (
            self.s_out.delete("1.0", "end"), self.s_out.insert("end", texto_calendario()))).pack(side="right")
        self.s_out = text_box(f, wrap="word", font=(FONT, 10)); self.s_out.pack(fill="both", expand=True)
        self.s_out.tag_configure("head", foreground=ACCENT, font=(FONT, 11, "bold"))
        self.s_out.insert("end", "Escolha o jogo lá em cima, diga quanto quer gastar e clique em "
                                 "'Montar minha aposta'.\n\nO app monta cartões que cobrem números diferentes "
                                 "(mais chance de algum prêmio) e, quando possível, com números pouco jogados "
                                 "(se ganhar, divide menos o prêmio). Ninguém prevê os números sorteados.")
        self.s_cards = []

    def assistente(self):
        """Primeiro uso: o que o app faz (sem promessa), jogo, quanto gastar por semana e limite do mês."""
        w = tk.Toplevel(self); w.title("Bem-vindo ao LuckHarness"); w.configure(bg=CARD); w.transient(self)
        fr = ttk.Frame(w, style="Card.TFrame", padding=20); fr.pack(fill="both", expand=True)
        ttk.Label(fr, text="Antes de começar", style="Card.TLabel", font=(FONT, 13, "bold")).pack(anchor="w")
        ttk.Label(fr, style="Card.TLabel", wraplength=440, justify="left", text=(
            "Ninguém consegue prever os números sorteados: o app testou isso em milhares de concursos. "
            "O que ele faz é tirar o máximo de cada real: escolhe o jogo que premia mais por real, espalha os "
            "números entre os cartões e evita combinações muito jogadas (se ganhar, divide menos o prêmio). "
            "Loteria é diversão, não investimento.")).pack(anchor="w", pady=(6, 14))
        g = ttk.Frame(fr, style="Card.TFrame"); g.pack(fill="x")
        ttk.Label(g, text="Jogo preferido", style="Card.TLabel").grid(row=0, column=0, sticky="w", pady=4)
        jb = JogoBox(g, width=16); jb.set(self.jogo.get()); jb.grid(row=0, column=1, sticky="w", padx=8)
        ttk.Label(g, text="Quanto quer gastar por semana (R$)", style="Card.TLabel").grid(row=1, column=0, sticky="w",
                                                                                        pady=4)
        orc = ttk.Entry(g, width=10); orc.insert(0, "15"); orc.grid(row=1, column=1, sticky="w", padx=8)
        ttk.Label(g, text="Limite de gasto no mês (R$, opcional)", style="Card.TLabel").grid(row=2, column=0,
                                                                                           sticky="w", pady=4)
        lim = ttk.Entry(g, width=10); lim.grid(row=2, column=1, sticky="w", padx=8)

        def valor(e):
            try:
                return max(0.0, float(e.get().replace("R$", "").replace(",", ".").strip() or 0))
            except ValueError:
                return 0.0

        def pronto():
            jogo = jb.get()
            save_prefs(**{f"assistente_{self.user}": True, "jogo": jogo, "orcamento": valor(orc) or 15})
            if valor(lim):
                save_prefs(**{f"limite_{self.user}": valor(lim)})
            self.jogo.set(jogo); self.on_jogo()
            self.s_val.delete(0, "end"); self.s_val.insert(0, f"{valor(orc) or 15:g}")
            self.b_lim.delete(0, "end"); self.b_lim.insert(0, f"{valor(lim):g}" if valor(lim) else "")
            self.refresh_banca()
            self.nb.select(0)
            w.destroy()
        b = ttk.Frame(fr, style="Card.TFrame"); b.pack(fill="x", pady=(16, 0))
        ttk.Button(b, text="Começar", style="Accent.TButton", command=pronto).pack(side="right")
        ttk.Button(b, text="Pular", style="Ghost.TButton", command=lambda: (
            save_prefs(**{f"assistente_{self.user}": True}), w.destroy())).pack(side="right", padx=6)
        self.janela_assistente = w

    def volante(self):
        """Marca as dezenas clicando, como no volante da lotérica."""
        jogo = self.jogo.get()
        if M[jogo].get("colunas"):
            messagebox.showinfo("Volante", "No Super Sete digite as 7 colunas separadas por espaço (0 a 9). Para "
                                           "mais números numa coluna use vírgula, ex.: 1,5 3 4 0 9 2 7 (até 21).")
            return
        N, mx = JOGOS[jogo][0], MAX_DEZ[jogo]
        main, _, extra = self.bet_in.get().partition("+")
        marcadas = {int(x) for x in re.findall(r"[0-9]+", main) if 1 <= int(x) <= N}
        w = tk.Toplevel(self); w.title(f"Volante - {NOMES[jogo]}"); w.configure(bg=CARD); w.transient(self)
        info = ttk.Label(w, style="Card.TLabel"); info.pack(pady=(10, 4))
        grade = tk.Frame(w, bg=CARD); grade.pack(padx=12, pady=4)
        botoes = {}
        cols = 10

        def pinta():
            for x, bt in botoes.items():
                bt.configure(bg=ACCENT if x in marcadas else FIELD, fg=BG if x in marcadas else FG)
            info.configure(text=f"{len(marcadas)} marcada(s): mínimo {JOGOS[jogo][2]}, máximo {mx}")

        def clica(x):
            if x in marcadas:
                marcadas.discard(x)
            elif len(marcadas) < mx:
                marcadas.add(x)
            pinta()
        for x in range(1, N + 1):
            num = x % 100 if M[jogo].get("zero_e_100") else x   # a Lotomania marca 00 no lugar do 100
            bt = tk.Button(grade, text=f"{num:02d}", width=3, relief="flat", font=(FONT, 9, "bold"),
                           command=lambda x=x: clica(x), cursor="hand2")
            bt.grid(row=(x - 1) // cols, column=(x - 1) % cols, padx=1, pady=1)
            botoes[x] = bt
        pinta()

        def ok():
            txt = " ".join(f"{x:02d}" for x in sorted(marcadas))
            self.bet_in.delete(0, "end")
            self.bet_in.insert(0, txt + (" +" + extra if extra.strip() else ""))
            w.destroy()
        b = tk.Frame(w, bg=CARD); b.pack(fill="x", padx=12, pady=10)
        ttk.Button(b, text="Usar estas dezenas", style="Accent.TButton", command=ok).pack(side="right")
        ttk.Button(b, text="Limpar", style="Ghost.TButton",
                   command=lambda: (marcadas.clear(), pinta())).pack(side="right", padx=6)
        if jogo in EXTRA or M[jogo].get("trevos"):
            ttk.Label(b, style="Muted.TLabel", text="Trevos/time/mês: escreva depois de '+' no campo.").pack(
                side="left")
        self.janela_volante = w

    def montar_semana(self):
        try:
            orc = float(self.s_val.get().replace("R$", "").replace(",", ".").strip())
        except ValueError:
            messagebox.showerror("Aposta da semana", "Informe um valor, ex.: 15")
            return
        jogo = self.jogo.get()
        preco = JOGOS[jogo][4]
        if orc < preco:
            messagebox.showinfo("Aposta da semana", f"Um cartão de {NOMES[jogo]} custa {brl(preco)}.")
            return
        save_prefs(orcamento=orc)
        pedido = orc
        # o limite do mês vale já na hora de montar, não só ao clicar em 'Apostei'
        lim = load_prefs().get(f"limite_{self.user}") or 0
        g, _ = banca_mes(self.user)
        avisos = []
        if lim:
            resta = max(0.0, lim - g)
            if resta < preco:
                messagebox.showinfo("Aposta da semana", f"Seu limite do mês é {brl(lim)} e você já gastou {brl(g)}: "
                                                        f"não sobra para um cartão de {brl(preco)}. O limite muda "
                                                        "na aba Banca.")
                return
            if orc > resta:
                avisos.append(f"Você pediu {brl(orc)}, mas o seu limite do mês é {brl(lim)} (já gastou {brl(g)}): "
                              f"a aposta foi montada com {brl(resta)}. O limite muda na aba Banca.")
                orc = resta
        qtd = int(orc // preco)
        if qtd > 20:
            avisos.append(f"{brl(orc)} dariam {qtd} cartões; o app monta no máximo 20 por vez.")
            qtd = 20
        self.s_ok.configure(state="disabled")

        def f():
            cards = sugestoes(jogo, qtd)
            itens = [(a, extra_aleatorio(jogo)) for a in cards]
            linhas = [f"{NOMES[jogo]}: {qtd} cartão(ões), total {brl(qtd * preco)} (você pediu até {brl(pedido)})\n"]
            linhas += [a + "\n" for a in avisos]
            for i, (a, t) in enumerate(itens, 1):
                fd = fator_divisao(jogo, a)
                linhas.append(f"  {i}. {fmt_bet(jogo, a, t)}"
                              + (f"   (se ganhar, divide {'menos' if fd < 1 else 'mais'} que a média)" if fd else ""))
            txt = "\n".join(linhas) + "\n\n" + texto_chances_cartoes(jogo, cards)
            d = proximo_sorteio(jogo)
            quando = "hoje à noite" if d == date.today() else f"{DIAS[d.weekday()]}, {d.strftime('%d/%m')}"
            txt += (f"\n\nPróximo sorteio: {quando}. Gasto no mês até agora: {brl(g)}. "
                    "Clique em 'Apostei' depois de jogar na lotérica.")

            def mostra():
                self.s_cards = [(jogo, a, t) for a, t in itens]
                self.s_out.delete("1.0", "end"); self.s_out.insert("end", txt)
                self.s_ok.configure(state="normal")
            self.after(0, mostra)
        self.bg(f)

    def apostei(self):
        if not self.s_cards:
            return
        total = sum(custo(j, a, t) for j, a, t in self.s_cards)
        lim = load_prefs().get(f"limite_{self.user}") or 0
        g, _ = banca_mes(self.user)
        if lim and g + total > lim and not messagebox.askyesno(
                "Limite da banca", f"Com esta aposta o gasto do mês vai a {brl(g + total)}, acima do seu limite "
                                   f"de {brl(lim)}.\n\nRegistrar mesmo assim?"):
            return
        for jogo, nums, trevos in self.s_cards:
            bid = save_bet(self.user, jogo, nums, trevos, last_concurso(jogo) + 1)
            add_banca(self.user, "Gasto", custo(jogo, nums, trevos), f"Aposta #{bid} {jogo}")
        self.s_cards = []
        self.s_ok.configure(state="disabled")
        self.s_out.insert("end", f"\n\nRegistrado: {brl(total)}. As apostas são conferidas sozinhas em "
                                 "'Minhas apostas' quando sair o resultado.", "head")
        self.refresh_bets()
        self.refresh_banca()

    def build_charts(self):
        f = self.tab("Gráficos")
        row = ttk.Frame(f, style="Card.TFrame"); row.pack(fill="x", pady=(0, 8))
        for nome, kind in [("Frequência", "freq"), ("Atraso", "atraso"), ("Soma das dezenas", "soma")]:
            ttk.Button(row, text=nome, style="Small.TButton",
                       command=lambda k=kind: self.show_chart(k)).pack(side="left", padx=(0, 6))
        self.chart = Chart(f); self.chart.pack(fill="both", expand=True)

    def build_bets(self):
        f = self.tab("Minhas apostas")
        r1 = ttk.Frame(f, style="Card.TFrame"); r1.pack(fill="x")
        ttk.Label(r1, text="Dezenas", style="Muted.TLabel").pack(side="left")
        self.bet_in = ttk.Entry(r1, width=38); self.bet_in.pack(side="left", padx=6, fill="x", expand=True)
        ttk.Button(r1, text="Volante", style="Small.TButton", command=self.volante).pack(side="left")
        ttk.Label(r1, text="a partir do concurso", style="Muted.TLabel").pack(side="left")
        self.desde = tk.IntVar(value=last_concurso(self.jogo.get()) + 1)
        ttk.Spinbox(r1, from_=1, to=99999, textvariable=self.desde, width=7).pack(side="left", padx=6)
        self.lanca = tk.BooleanVar(value=True)
        ttk.Checkbutton(r1, text="lançar custo na banca", variable=self.lanca,
                        style="Card.TCheckbutton").pack(side="left", padx=6)
        ttk.Label(f, text="Ex.: 05 12 23 34 45 56  |  +Milionária: 01 02 03 04 05 06 + 1 4  |  "
                          "Super Sete: 8 7 8 7 2 0 4 (uma por coluna)\n"
                          "Timemania: dezenas + time (… + vitoria)  |  Dia de Sorte: dezenas + mês (… + outubro)",
                  style="Muted.TLabel").pack(anchor="w", pady=4)
        r2 = ttk.Frame(f, style="Card.TFrame"); r2.pack(fill="x", pady=(0, 8))
        ttk.Button(r2, text="Salvar aposta", style="Accent.TButton", command=self.add_bet).pack(side="left")
        ttk.Button(r2, text="Salvar sugeridas", style="Ghost.TButton", command=self.add_sugs).pack(side="left", padx=6)
        ttk.Button(r2, text="Conferir", style="Ghost.TButton", command=self.refresh_bets).pack(side="left")
        ttk.Button(r2, text="Excluir", style="Ghost.TButton", command=self.del_bet).pack(side="left", padx=6)
        ttk.Button(r2, text="Editar trevos/time/mês", style="Ghost.TButton",
                   command=self.edit_bet).pack(side="left")
        ttk.Button(r2, text="Exportar PDF", style="Ghost.TButton", command=lambda: self.export("pdf")).pack(side="right")
        ttk.Button(r2, text="Exportar planilha", style="Ghost.TButton",
                   command=lambda: self.export("csv")).pack(side="right", padx=6)
        cols = ("id", "jogo", "dezenas", "desde", "conferidos", "melhor", "ganhos")
        heads = ("#", "Jogo", "Dezenas", "Desde", "Conferidos", "Melhor", "Prêmios")
        widths = (40, 110, 250, 70, 80, 60, 100)
        self.tree = ttk.Treeview(f, columns=cols, show="headings", height=8)
        for c, h, w in zip(cols, heads, widths):
            self.tree.heading(c, text=h)
            self.tree.column(c, width=w, anchor="w" if c == "dezenas" else "center", stretch=c == "dezenas")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.show_bet())
        self.bet_info = text_box(f, height=7, font=("Consolas", 10)); self.bet_info.pack(fill="x", pady=(8, 0))
        self.bets = []
        ttk.Label(f, text="Prêmios calculados para aposta simples com os valores oficiais de cada concurso.",
                  style="Muted.TLabel").pack(anchor="w", pady=(4, 0))

    def build_fech(self):
        f = self.tab("Fechamento")
        ttk.Label(f, text="Escolha suas dezenas e o app gera o menor número de cartões que encontrar com a garantia "
                          "pedida.", style="Card.TLabel").pack(anchor="w")
        r1 = ttk.Frame(f, style="Card.TFrame"); r1.pack(fill="x", pady=8)
        ttk.Label(r1, text="Dezenas", style="Muted.TLabel").pack(side="left")
        self.f_nums = ttk.Entry(r1); self.f_nums.pack(side="left", fill="x", expand=True, padx=6)
        # números quentes/do modelo de previsão não ajudam (testado); pouco jogados dividem menos o prêmio
        ttk.Button(r1, text="Pouco jogados", style="Small.TButton", command=self.fill_fech).pack(side="left")
        r2 = ttk.Frame(f, style="Card.TFrame"); r2.pack(fill="x")
        self.f_k, self.f_t, self.f_m = tk.IntVar(), tk.IntVar(), tk.IntVar()
        for txt, var in [("Dezenas por cartão", self.f_k), ("Garantir", self.f_t),
                         ("acertos se saírem", self.f_m)]:
            ttk.Label(r2, text=txt, style="Muted.TLabel").pack(side="left")
            ttk.Spinbox(r2, from_=1, to=50, textvariable=var, width=4).pack(side="left", padx=(6, 12))
        ttk.Label(r2, text="das escolhidas", style="Muted.TLabel").pack(side="left")
        r3 = ttk.Frame(f, style="Card.TFrame"); r3.pack(fill="x", pady=(8, 0))
        ttk.Button(r3, text="Gerar fechamento", style="Accent.TButton", command=self.run_fech).pack(side="left")
        ttk.Button(r3, text="Salvar como apostas", style="Ghost.TButton",
                   command=self.save_fech).pack(side="left", padx=6)
        self.f_out = text_box(f, font=("Consolas", 10)); self.f_out.pack(fill="both", expand=True, pady=(8, 0))
        self.f_cards = []
        self.fech_defaults()

    def build_retorno(self):
        f = self.tab("Retorno")
        r1 = ttk.Frame(f, style="Card.TFrame"); r1.pack(fill="x")
        ttk.Button(r1, text="Comparar todos os jogos", style="Accent.TButton",
                   command=self.comparar).pack(side="left")
        ttk.Button(r1, text="Retorno deste jogo", style="Ghost.TButton", command=self.ret_jogo).pack(side="left",
                                                                                                    padx=6)
        r2 = ttk.Frame(f, style="Card.TFrame"); r2.pack(fill="x", pady=8)
        ttk.Label(r2, text="Avaliar aposta", style="Muted.TLabel").pack(side="left")
        self.r_bet = ttk.Entry(r2); self.r_bet.pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(r2, text="Avaliar", style="Ghost.TButton", command=self.avaliar).pack(side="left")
        r3 = ttk.Frame(f, style="Card.TFrame"); r3.pack(fill="x")
        ttk.Label(r3, text="Bolão: dezenas", style="Muted.TLabel").pack(side="left")
        self.r_n = tk.IntVar(value=8)
        ttk.Spinbox(r3, from_=1, to=50, textvariable=self.r_n, width=4).pack(side="left", padx=6)
        ttk.Label(r3, text="cotas", style="Muted.TLabel").pack(side="left")
        self.r_cotas = tk.IntVar(value=10)
        ttk.Spinbox(r3, from_=1, to=500, textvariable=self.r_cotas, width=5).pack(side="left", padx=6)
        ttk.Button(r3, text="Simular bolão", style="Ghost.TButton", command=self.sim_bolao).pack(side="left")
        self.r_out = text_box(f, font=("Consolas", 10)); self.r_out.pack(fill="both", expand=True, pady=(8, 0))
        self.r_out.insert("end", "Aqui o app calcula quanto cada jogo e cada aposta devolve em média, com as "
                                 "probabilidades exatas e os prêmios reais pagos nos últimos 100 concursos.\n\n"
                                 "Nenhum cálculo prevê os números sorteados. O que dá para escolher com matemática é "
                                 "o jogo e o momento com melhor retorno, o custo, e apostas pouco populares, que "
                                 "dividem menos o prêmio quando ganham.")
        self.ranking = self.ev_txt = ""
        r4 = ttk.Frame(f, style="Card.TFrame"); r4.pack(fill="x", pady=(8, 0))
        ttk.Label(r4, text="Alertar quando o retorno passar de", style="Muted.TLabel").pack(side="left")
        self.al_lim = ttk.Entry(r4, width=5); self.al_lim.pack(side="left", padx=6)
        self.al_lim.insert(0, str(load_prefs().get("alerta_limite") or 80))
        ttk.Label(r4, text="%", style="Muted.TLabel").pack(side="left")
        self.al_auto = tk.BooleanVar(value=False)
        ttk.Checkbutton(r4, text="verificar todo dia às 22h30, mesmo com o app fechado", variable=self.al_auto,
                        style="Card.TCheckbutton").pack(side="left", padx=10)
        ttk.Button(r4, text="Salvar alerta", style="Ghost.TButton", command=self.save_alerta).pack(side="left")
        threading.Thread(target=lambda: (lambda v: self.after(0, lambda: self.al_auto.set(v)))(alerta_agendado()),
                         daemon=True).start()

    def save_alerta(self):
        try:
            lim = float(self.al_lim.get().replace(",", ".").replace("%", ""))
        except ValueError:
            messagebox.showerror("Alerta", "Informe o limite em %, ex.: 80")
            return
        save_prefs(alerta_limite=lim, alerta_vistos={})
        ok, msg = agendar_alerta(self.al_auto.get())
        if self.al_auto.get() and not ok:
            messagebox.showerror("Alerta", f"Não consegui agendar a verificação diária:\n{msg}")
            return
        messagebox.showinfo("Alerta", f"Alerta salvo: aviso quando algum jogo passar de {lim:.0f}% de retorno."
                            + (" Verificação diária às 22h30 ativada." if self.al_auto.get() else ""))
        self.bg(lambda: [self.log(f"Alerta: {a['jogo']} com retorno de {a['retorno']:.0%}.", "head")
                         for a in checar_alertas()])

    def build_otim(self):
        f = self.tab("Otimizador")
        r1 = ttk.Frame(f, style="Card.TFrame"); r1.pack(fill="x")
        ttk.Label(r1, text="R$ por semana", style="Muted.TLabel").pack(side="left")
        self.o_val = ttk.Entry(r1, width=8); self.o_val.pack(side="left", padx=6)
        self.o_val.insert(0, str(load_prefs().get("orcamento") or 30))
        ttk.Label(r1, text="objetivo", style="Muted.TLabel").pack(side="left")
        self.o_obj = ttk.Combobox(r1, values=list(OBJETIVOS.values()), state="readonly", width=38)
        self.o_obj.set(OBJETIVOS["frequente"]); self.o_obj.pack(side="left", padx=6)
        ttk.Button(r1, text="Otimizar", style="Accent.TButton", command=self.run_otim).pack(side="left", padx=6)
        ttk.Button(r1, text="Salvar cartões", style="Ghost.TButton", command=self.save_otim).pack(side="left")
        r1b = ttk.Frame(f, style="Card.TFrame"); r1b.pack(fill="x", pady=(8, 0))
        ttk.Label(r1b, text="Aposta mínima: até R$", style="Muted.TLabel").pack(side="left")
        self.o_min = ttk.Entry(r1b, width=7); self.o_min.pack(side="left", padx=6)
        self.o_min.insert(0, "6,00")
        ttk.Label(r1b, text="prêmio de pelo menos", style="Muted.TLabel").pack(side="left")
        self.o_alvo = ttk.Combobox(r1b, values=list(ALVOS), state="readonly", width=12)
        self.o_alvo.set("R$ 10 mil"); self.o_alvo.pack(side="left", padx=6)
        ttk.Button(r1b, text="Aposta mínima, prêmio maior", style="Accent.TButton",
                   command=self.run_minima).pack(side="left", padx=6)
        r2 = ttk.Frame(f, style="Card.TFrame"); r2.pack(fill="x", pady=8)
        ttk.Button(r2, text="Treinar modelo de popularidade (jogo selecionado)", style="Ghost.TButton",
                   command=self.run_pop).pack(side="left")
        ttk.Button(r2, text="Chances de todos os jogos", style="Ghost.TButton",
                   command=lambda: self.bg_otim(tabela_chances)).pack(side="left", padx=6)
        ttk.Button(r2, text="Acumulados que saíram (jogo)", style="Ghost.TButton",
                   command=self.run_acumulados).pack(side="left")
        r3 = ttk.Frame(f, style="Card.TFrame"); r3.pack(fill="x", pady=(0, 8))
        ttk.Label(r3, text="Testar o plano nos últimos", style="Muted.TLabel").pack(side="left")
        self.o_conc = tk.IntVar(value=104)
        ttk.Spinbox(r3, from_=20, to=2000, increment=20, textvariable=self.o_conc, width=6).pack(side="left", padx=6)
        ttk.Label(r3, text="concursos, jogando só com retorno acima de", style="Muted.TLabel").pack(side="left")
        self.o_rmin = tk.IntVar(value=0)
        ttk.Spinbox(r3, from_=0, to=200, increment=10, textvariable=self.o_rmin, width=5).pack(side="left", padx=6)
        ttk.Label(r3, text="%", style="Muted.TLabel").pack(side="left")
        ttk.Button(r3, text="Testar no passado", style="Ghost.TButton", command=self.run_teste).pack(side="left",
                                                                                                   padx=6)
        r4 = ttk.Frame(f, style="Card.TFrame"); r4.pack(fill="x", pady=(0, 8))
        ttk.Label(r4, text="Banca R$", style="Muted.TLabel").pack(side="left")
        self.o_banca = ttk.Entry(r4, width=8); self.o_banca.pack(side="left", padx=6)
        self.o_banca.insert(0, "500")
        ttk.Button(r4, text="Risco de ruína do plano", style="Ghost.TButton", command=self.run_ruina).pack(side="left")
        ttk.Button(r4, text="Auditoria de aleatoriedade (jogo)", style="Ghost.TButton",
                   command=self.run_auditoria).pack(side="left", padx=6)
        ttk.Button(r4, text="Previsão do acumulado", style="Ghost.TButton",
                   command=lambda: self.bg_otim(lambda: texto_previsao(previsoes()))).pack(side="left")
        self.o_out = text_box(f, font=("Consolas", 10)); self.o_out.pack(fill="both", expand=True)
        self.o_out.insert("end", "O Otimizador escolhe o jogo e o formato de aposta que mais rendem para o seu "
                                 "orçamento, gera cartões espalhados (menos números repetidos = mais chance de "
                                 "pelo menos um premiar) e, com o modelo de popularidade treinado, evita os números "
                                 "que o público mais joga para dividir menos o prêmio.\n\n"
                                 "Ele não prevê sorteios: nenhum método faz isso. Ele tira o máximo de cada real.")
        self.otim_cards, self.otim_txt, self.otim_plan = [], "", None

    def build_lab(self):
        f = self.tab("Laboratório")
        r1 = ttk.Frame(f, style="Card.TFrame"); r1.pack(fill="x")
        ttk.Label(r1, text="Modo", style="Muted.TLabel").pack(side="left")
        self.l_modo = ttk.Combobox(r1, state="readonly", width=44, values=[
            "Teste cego (esconde os últimos concursos)", "Teste contínuo (cada concurso só com o passado)"])
        self.l_modo.current(0); self.l_modo.pack(side="left", padx=6)
        ttk.Label(r1, text="concursos", style="Muted.TLabel").pack(side="left")
        self.l_n = tk.IntVar(value=100)
        ttk.Spinbox(r1, from_=20, to=1000, increment=20, textvariable=self.l_n, width=6).pack(side="left", padx=6)
        r2 = ttk.Frame(f, style="Card.TFrame"); r2.pack(fill="x", pady=6)
        self.l_est = {}
        for nome in lab.ESTRATEGIAS:
            v = tk.BooleanVar(value=nome != "minha aposta")
            ttk.Checkbutton(r2, text=nome, variable=v, style="Card.TCheckbutton").pack(side="left", padx=(0, 8))
            self.l_est[nome] = v
        r3 = ttk.Frame(f, style="Card.TFrame"); r3.pack(fill="x")
        ttk.Label(r3, text="Minha aposta", style="Muted.TLabel").pack(side="left")
        self.l_minha = ttk.Entry(r3, width=30); self.l_minha.pack(side="left", padx=6)
        ttk.Button(r3, text="Rodar teste", style="Accent.TButton", command=self.run_lab).pack(side="left", padx=6)
        ttk.Button(r3, text="Registrar para o próximo sorteio", style="Ghost.TButton",
                   command=self.reg_lab).pack(side="left")
        ttk.Button(r3, text="Conferir registros", style="Ghost.TButton",
                   command=lambda: self.bg_lab(lambda: lab.conferir_registros(self.user))).pack(side="left", padx=6)
        self.l_out = text_box(f, font=("Consolas", 10)); self.l_out.pack(fill="both", expand=True, pady=(8, 0))
        self.l_out.insert("end", "Teste qualquer ideia de aposta com os resultados reais, sem trapaça: cada "
                                 "estratégia só enxerga os concursos anteriores ao que está apostando.\n\n"
                                 "- Teste cego: esconde os últimos concursos, aposta em todos de uma vez e grava uma "
                                 "impressão digital das apostas antes de revelar os resultados.\n"
                                 "- Teste contínuo: aposta concurso a concurso e inclui o 'aprende com erros e "
                                 "acertos' (Hedge).\n"
                                 "- Registrar: grava apostas para o próximo sorteio, que ainda não aconteceu. Confira "
                                 "depois que o resultado sair.\n\n"
                                 "O valor-p ajustado desconta o fato de testar várias ideias ao mesmo tempo: só "
                                 "abaixo de 0,05 a diferença é real e não sorte.")

    def _lab_params(self):
        jogo = self.jogo.get()
        est = [n for n, v in self.l_est.items() if v.get()]
        minha = None
        if "minha aposta" in est:
            minha = lab.minha_aposta(jogo, self.l_minha.get())
        if not est:
            raise ValueError("Escolha pelo menos uma estratégia.")
        return jogo, est, minha

    def run_lab(self):
        try:
            jogo, est, minha = self._lab_params()
        except ValueError as e:
            messagebox.showerror("Laboratório", str(e))
            return
        n, cego = self.l_n.get(), self.l_modo.current() == 0
        fn = lab.teste_cego if cego else lab.teste_continuo
        self.bg_lab(lambda: lab.texto_resultado(fn(jogo, n, est, minha)))

    def reg_lab(self):
        try:
            jogo, est, minha = self._lab_params()
        except ValueError as e:
            messagebox.showerror("Laboratório", str(e))
            return
        self.bg_lab(lambda: lab.registrar_proximo(self.user, jogo, est, minha))

    def bg_lab(self, fn):
        self.l_out.delete("1.0", "end")
        self.l_out.insert("end", "Calculando...")

        def f():
            try:
                t = fn()
            except Exception as e:
                log.exception("laboratório")
                t = f"ERRO: {e}"
            self.after(0, lambda: (self.l_out.delete("1.0", "end"), self.l_out.insert("end", t)))
        threading.Thread(target=f, daemon=True).start()

    def bg_otim(self, fn):
        self.o_out.delete("1.0", "end")
        self.o_out.insert("end", "Calculando...")

        def f():
            try:
                t = fn()
            except Exception as e:
                log.exception("otimizador")
                t = f"ERRO: {e}"
            self.after(0, lambda: (self.o_out.delete("1.0", "end"), self.o_out.insert("end", t)))
        threading.Thread(target=f, daemon=True).start()

    def run_otim(self):
        try:
            orc = float(self.o_val.get().replace(",", "."))
        except ValueError:
            messagebox.showerror("Otimizador", "Valor por semana inválido.")
            return
        obj = next(k for k, v in OBJETIVOS.items() if v == self.o_obj.get())
        save_prefs(orcamento=orc)

        def f():
            rk = otimizar(orc, obj)
            return self._mostra_plano(texto_plano(orc, obj, rk), rk[0])
        self.bg_otim(f)

    def run_minima(self):
        try:
            orc = float(self.o_min.get().replace(",", "."))
        except ValueError:
            messagebox.showerror("Otimizador", "Valor da aposta mínima inválido.")
            return
        alvo = ALVOS[self.o_alvo.get()]

        def f():
            rk = aposta_minima(orc, alvo)
            return self._mostra_plano(texto_aposta_minima(orc, alvo, rk), rk[0], cobertura=False)
        self.bg_otim(f)

    def _mostra_plano(self, texto, b, cobertura=True):
        """Gera os cartões do plano escolhido, guarda para 'Salvar cartões' e monta o texto."""
        self.otim_plan = b
        j = b["jogo"]
        p_esp = None
        if b["tipo"] == "simples" and cobertura and not b.get("tamanhos"):
            cards, tv, p_esp, p_ale = comparar_cobertura(j, b["k"], b["n"])
        elif b["tipo"] == "simples":
            cards, tv = sugestoes(j, b["k"], b["n"]), None
        else:
            # dezenas do bolão/fechamento: as menos repetidas e menos populares
            v = b.get("v", b["n"])
            base = sugestoes(j, 1, v)[0]
            cards = ([base] if b["tipo"] == "bolão" else
                     [sorted(base[x - 1] for x in c) for c in b["cards_base"]])
            tv = None
        extras = tv or extras_espalhados(j, len(cards))
        self.otim_cards = [(j, c, t) for c, t in zip(cards, extras)]
        linhas = [texto, "", f"Cartões sugeridos ({j}):"]
        for i, (c, t) in enumerate(zip(cards, extras), 1):
            fd = fator_divisao(j, c, t if M[j].get("trevos") else ())
            linhas.append(f"  {i:>2}. {fmt_bet(j, c, t)}" + (f"   divisão do prêmio: {fd:.2f}x a média"
                                                               if fd else ""))
        if p_esp is not None:
            linhas += ["", f"Chance de pelo menos um cartão premiar nas dezenas (20.000 sorteios simulados): "
                           f"{p_esp:.1%} com estes cartões x {p_ale:.1%} com cartões ao acaso."]
        if b["tipo"] == "bolão":
            linhas += ["", f"Bolão: registre o jogo de {b['n']} dezenas com {b['cotas']} cotas numa lotérica; "
                           "'Salvar cartões' lança só o valor da sua cota na banca."]
        if not pesos_popularidade(j) and j in POP_JOGOS:
            linhas.append("Dica: treine o modelo de popularidade deste jogo para os cartões evitarem os "
                          "números mais jogados.")
        self.otim_txt = "\n".join(linhas)
        return self.otim_txt

    def run_teste(self):
        b = self.otim_plan
        jogo = b["jogo"] if b else self.jogo.get()
        k, n = (b["k"], b["n"]) if b else (5, JOGOS[jogo][2])
        conc, rmin = self.o_conc.get(), self.o_rmin.get() / 100
        self.bg_otim(lambda: ("" if b else f"(Sem plano do Otimizador: testando {k} cartões de {jogo}.)\n\n")
                     + texto_teste(testar_no_passado(jogo, k, n, conc, rmin)))

    def run_ruina(self):
        if not self.otim_plan:
            messagebox.showinfo("Risco de ruína", "Clique em 'Otimizar' primeiro: o risco é calculado para o plano.")
            return
        try:
            banca = float(self.o_banca.get().replace(",", "."))
        except ValueError:
            messagebox.showerror("Risco de ruína", "Banca inválida.")
            return
        plano = self.otim_plan
        self.bg_otim(lambda: texto_ruina(plano, risco_ruina(plano, banca)))

    def run_auditoria(self):
        jogo = self.jogo.get()
        self.bg_otim(lambda: texto_auditoria(auditoria(jogo)))

    def run_acumulados(self):
        jogo = self.jogo.get()
        if jogo not in POP_JOGOS:
            messagebox.showinfo("Acumulados", f"Disponível para: {', '.join(POP_JOGOS)}.")
            return
        self.bg_otim(lambda: texto_acumulados(estudo_acumulados(jogo)))

    def run_pop(self):
        jogo = self.jogo.get()
        self.bg_otim(lambda: texto_popularidade(treinar_popularidade(jogo)))

    def save_otim(self):
        if not self.otim_cards:
            messagebox.showinfo("Otimizador", "Clique em 'Otimizar' primeiro.")
            return
        b = self.otim_plan
        if b and b["tipo"] == "bolão":
            j, c, t = self.otim_cards[0]
            bid = save_bet(self.user, j, c, t, last_concurso(j) + 1)
            if self.lanca.get():
                add_banca(self.user, "Gasto", b["gasto"], f"Cota bolão (aposta #{bid}) {j}")
            self.log(f"Bolão salvo (aposta #{bid}); sua cota: {brl(b['gasto'])}.", "head")
            self.refresh_bets()
            self.refresh_banca()
            self.otim_cards = []
            return
        self._save_bets(self.otim_cards)
        self.otim_cards = []

    def bg_ret(self, fn):
        self.r_out.delete("1.0", "end")
        self.r_out.insert("end", "Calculando...")

        def f():
            try:
                t = fn()
            except Exception as e:
                t = f"ERRO: {e}"
            self.after(0, lambda: (self.r_out.delete("1.0", "end"), self.r_out.insert("end", t)))
        threading.Thread(target=f, daemon=True).start()

    def ret_jogo(self):
        jogo = self.jogo.get()

        def f():
            self.ev_txt = texto_ev(valor_esperado(jogo))
            return self.ev_txt
        self.bg_ret(f)

    def comparar(self):
        def f():
            rs = sorted((valor_esperado(j) for j in JOGOS), key=lambda r: -r["retorno"])
            out = ["=== MELHOR RETORNO AGORA (aposta simples, prêmios líquidos de IR) ===", "",
                   f"{'Jogo':<16}{'Aposta':>10}{'Valor esperado':>17}{'Retorno':>10}{'Prêmio principal':>22}"]
            for r in rs:
                out.append(f"{r['jogo']:<16}{brl(r['preco']):>10}{brl(r['ev']):>17}{r['retorno']:>10.0%}"
                           f"{brl(r['estimado']):>22}")
            out += ["", f"Melhor agora: {rs[0]['jogo']} (volta {rs[0]['retorno']:.0%} em média).",
                    "Todos abaixo de 100% significa que, em média, toda aposta perde dinheiro. O retorno sobe "
                    "quando o prêmio principal acumula."]
            self.ranking = "\n".join(out)
            return self.ranking
        self.bg_ret(f)

    def avaliar(self):
        jogo = self.jogo.get()
        try:
            nums, trevos = parse_bet(jogo, self.r_bet.get())
        except ValueError as e:
            messagebox.showerror("Aposta inválida", str(e))
            return
        def f():
            fd = fator_divisao(jogo, nums, trevos if M[jogo].get("trevos") else ())
            return avaliar_aposta(jogo, nums, trevos, fator=fd)[0]
        self.bg_ret(f)

    def sim_bolao(self):
        jogo = self.jogo.get()
        n, cotas = self.r_n.get(), max(1, self.r_cotas.get())
        if not M[jogo].get("colunas") and not JOGOS[jogo][2] <= n <= MAX_DEZ[jogo]:
            messagebox.showerror("Bolão", f"{jogo}: use de {JOGOS[jogo][2]} a {MAX_DEZ[jogo]} dezenas.")
            return
        self.bg_ret(lambda: bolao(jogo, n, cotas))

    def build_plano(self):
        f = self.tab("Planejamento")
        r1 = ttk.Frame(f, style="Card.TFrame"); r1.pack(fill="x")
        ttk.Label(r1, text="Concursos", style="Muted.TLabel").pack(side="left")
        self.h_n = tk.IntVar(value=200)
        ttk.Spinbox(r1, from_=20, to=3000, increment=20, textvariable=self.h_n, width=6).pack(side="left", padx=6)
        ttk.Button(r1, text="Histórico do retorno", style="Accent.TButton", command=self.run_hist).pack(side="left")
        ttk.Button(r1, text="Meu desempenho", style="Ghost.TButton", command=self.run_desemp).pack(side="right")
        r2 = ttk.Frame(f, style="Card.TFrame"); r2.pack(fill="x", pady=8)
        ttk.Label(r2, text="Simular: R$ por semana", style="Muted.TLabel").pack(side="left")
        self.s_val = ttk.Entry(r2, width=8); self.s_val.pack(side="left", padx=6); self.s_val.insert(0, "50")
        ttk.Label(r2, text="durante", style="Muted.TLabel").pack(side="left")
        self.s_anos = tk.IntVar(value=5)
        ttk.Spinbox(r2, from_=1, to=50, textvariable=self.s_anos, width=4).pack(side="left", padx=6)
        ttk.Label(r2, text="anos", style="Muted.TLabel").pack(side="left")
        ttk.Button(r2, text="Simular", style="Ghost.TButton", command=self.run_sim).pack(side="left", padx=6)
        pw = ttk.PanedWindow(f, orient="vertical"); pw.pack(fill="both", expand=True)
        self.p_chart = Chart(pw); pw.add(self.p_chart, weight=1)
        self.p_out = text_box(pw, font=("Consolas", 10), height=10); pw.add(self.p_out, weight=1)

    def plano(self, fn):
        self.p_out.delete("1.0", "end")
        self.p_out.insert("end", "Calculando...")

        def f():
            try:
                txt, chart = fn()
            except Exception as e:
                txt, chart = f"ERRO: {e}", None
            self.after(0, lambda: (self.p_out.delete("1.0", "end"), self.p_out.insert("end", txt),
                                   chart and self.p_chart.show(chart[0], **chart[1])))
        threading.Thread(target=f, daemon=True).start()

    def run_hist(self):
        jogo, n = self.jogo.get(), self.h_n.get()
        alvo = (load_prefs().get("alerta_limite") or 80) / 100

        def f():
            h = historico_retorno(jogo, n)
            chart = ("line", {"xs": [x[0] for x in h], "ys": [x[2] for x in h], "ref": alvo,
                              "reflabel": f"alerta {alvo:.0%}",
                              "title": f"Retorno por concurso: {jogo}"}) if len(h) > 1 else None
            return texto_hist(jogo, h, alvo), chart
        self.plano(f)

    def run_sim(self):
        jogo = self.jogo.get()
        try:
            semanal = float(self.s_val.get().replace(",", "."))
        except ValueError:
            messagebox.showerror("Simular", "Valor por semana inválido.")
            return
        anos = max(1, self.s_anos.get())

        def f():
            m = simular(jogo, semanal, anos)
            res = m["res"]
            lo, hi = res[len(res) // 100], res[-len(res) // 100 - 1]
            nb = 20
            w = (hi - lo) / nb or 1
            cnt = Counter(min(nb - 1, int((x - lo) / w)) for x in res if lo <= x <= hi)
            chart = ("bars", {"labels": [f"{(lo + i * w) / 1000:.1f}k" for i in range(nb)],
                              "values": [cnt.get(i, 0) for i in range(nb)], "color": IA_COLOR,
                              "title": "Resultado final (R$) nos cenários simulados, sem os 1% extremos"})
            return texto_sim(m, semanal, anos), chart
        self.plano(f)

    def run_desemp(self):
        self.plano(lambda: (desempenho(self.user), None))

    def build_banca(self):
        f = self.tab("Banca")
        r1 = ttk.Frame(f, style="Card.TFrame"); r1.pack(fill="x")
        ttk.Label(r1, text="Valor R$", style="Muted.TLabel").pack(side="left")
        self.b_val = ttk.Entry(r1, width=10); self.b_val.pack(side="left", padx=6)
        self.b_tipo = ttk.Combobox(r1, values=["Gasto", "Prêmio"], width=8, state="readonly")
        self.b_tipo.set("Gasto"); self.b_tipo.pack(side="left", padx=6)
        ttk.Label(r1, text="Descrição", style="Muted.TLabel").pack(side="left")
        self.b_desc = ttk.Entry(r1); self.b_desc.pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(r1, text="Adicionar", style="Accent.TButton", command=self.add_mov).pack(side="left")
        r2 = ttk.Frame(f, style="Card.TFrame"); r2.pack(fill="x", pady=8)
        ttk.Label(r2, text="Limite mensal R$", style="Muted.TLabel").pack(side="left")
        self.b_lim = ttk.Entry(r2, width=10); self.b_lim.pack(side="left", padx=6)
        self.b_lim.insert(0, str(load_prefs().get(f"limite_{self.user}") or ""))
        ttk.Button(r2, text="Salvar limite", style="Ghost.TButton", command=self.save_lim).pack(side="left")
        ttk.Button(r2, text="Excluir lançamento", style="Ghost.TButton", command=self.del_mov).pack(side="right")
        self.b_sum = ttk.Label(f, text="", style="Big.TLabel"); self.b_sum.pack(anchor="w", pady=(4, 8))
        cols, heads = ("id", "data", "tipo", "valor", "descr"), ("#", "Data", "Tipo", "Valor", "Descrição")
        self.b_tree = ttk.Treeview(f, columns=cols, show="headings")
        for c, h, w in zip(cols, heads, (40, 100, 80, 100, 400)):
            self.b_tree.heading(c, text=h)
            self.b_tree.column(c, width=w, anchor="w" if c == "descr" else "center", stretch=c == "descr")
        self.b_tree.pack(fill="both", expand=True)

    def build_chat(self, body):
        side = ttk.Frame(body, style="Card.TFrame", padding=12); body.add(side, weight=2)
        hdr = ttk.Frame(side, style="Card.TFrame"); hdr.pack(fill="x", pady=(0, 8))
        self.av = load_avatar(56)
        if self.av:
            tk.Label(hdr, image=self.av, bg=CARD).pack(side="left")
        info = ttk.Frame(hdr, style="Card.TFrame"); info.pack(side="left", padx=10)
        ttk.Label(info, text="Harness IA", style="Card.TLabel", font=(FONT, 12, "bold")).pack(anchor="w")
        self.model_cb = ttk.Combobox(info, values=[self.model], width=22, state="readonly")
        self.model_cb.set(self.model); self.model_cb.pack(anchor="w", pady=(2, 0))
        self.model_cb.bind("<<ComboboxSelected>>", lambda e: self.set_model())
        ttk.Button(hdr, text="Limpar", style="Small.TButton", command=self.clear_chat).pack(side="right")
        self.chat = text_box(side, wrap="word", font=(FONT, 10)); self.chat.configure(bg=BG)
        self.chat.pack(fill="both", expand=True)
        self.chat.tag_configure("you", foreground=ACCENT, font=(FONT, 10, "bold"), spacing1=10)
        self.chat.tag_configure("ia", foreground=IA_COLOR, font=(FONT, 10, "bold"), spacing1=6)
        quick = ttk.Frame(side, style="Card.TFrame"); quick.pack(fill="x", pady=(8, 0))
        for i, (nome, q) in enumerate(QUICK):
            ttk.Button(quick, text=nome, style="Small.TButton", command=lambda q=q: self.ai(q)).grid(
                row=i // 2, column=i % 2, sticky="ew", padx=2, pady=2)
        quick.columnconfigure((0, 1), weight=1)
        row = ttk.Frame(side, style="Card.TFrame"); row.pack(fill="x", pady=(6, 0))
        self.q = ttk.Entry(row); self.q.pack(side="left", fill="x", expand=True)
        self.q.bind("<Return>", lambda e: self.ai())
        ttk.Button(row, text="Enviar", style="Accent.TButton", command=self.ai).pack(side="left", padx=(6, 0))
        for m in load_chat(self.user):
            self.chat.insert("end", "\nVocê\n" if m["role"] == "user" else "\nHarness IA\n",
                             "you" if m["role"] == "user" else "ia")
            self.chat.insert("end", m["content"].strip() + "\n")
        self.chat.see("end")
        threading.Thread(target=lambda: (lambda ms: self.after(0, lambda: self.model_cb.configure(
            values=ms or [self.model])))(list_models()), daemon=True).start()

    # ---------- utilidades ----------
    def log(self, t, tag=None):
        self.after(0, lambda: (self.out.insert("end", t + "\n", tag), self.out.see("end")))

    def bg(self, fn):
        def wrap():
            try:
                fn()
            except Exception as e:
                log.exception("tarefa em background")
                self.log(f"ERRO: {e}", "err")
        threading.Thread(target=wrap, daemon=True).start()

    def fechar(self):
        try:
            save_prefs(jogo=self.jogo.get(), n=self.n.get(), bet=self.bet.get(), model=self.model)
        except Exception:
            log.warning("save_prefs falhou", exc_info=True)
        self.destroy()

    def sair(self):
        forget(self.user)
        self.logout = True
        self.fechar()

    def on_jogo(self):
        jogo = self.jogo.get()
        self.bet.set(JOGOS[jogo][2])
        self.desde.set(last_concurso(jogo) + 1)
        self.fech_defaults()

    # ---------- resultados e análise ----------
    def auto_update(self):
        self.log("Atualizando resultados de todos os jogos...", "head")
        total = 0
        for j in JOGOS:
            try:
                total += sync_draws(j, log=lambda *_: None)
            except Exception as e:
                self.log(f"  {j}: sem acesso ({str(e).splitlines()[0]})", "err")
        self.log(f"Resultados atualizados: {total} concursos novos.", "head")
        self.after(0, lambda: self.desde.set(last_concurso(self.jogo.get()) + 1))
        self.refresh_bets(avisar=True)
        treinar_todos(lambda t: self.log(t, "head"))
        lim = (load_prefs().get("alerta_limite") or 0) / 100
        if lim:
            for p in previsoes():
                if p["retorno_se_acumular"] >= lim > p["retorno_atual"]:
                    self.log(f"Previsão: {p['jogo']} pode passar de {lim:.0%} de retorno se acumular "
                             f"({p['p_acumula']:.0%} de chance).", "head")
        for a in checar_alertas():
            self.log(f"Alerta: {a['jogo']} com retorno de {a['retorno']:.0%} (prêmio {brl(a['estimado'])}).", "head")

    def sync(self):
        self.log("Baixando resultados...", "head")
        jogo, n = self.jogo.get(), self.n.get()
        self.bg(lambda: (sync_draws(jogo, n, self.log),
                         self.log(f"Concluído. O histórico completo fica salvo; 'Analisar' usa os últimos {n} "
                                  "concursos (o modelo de aprendizado usa pelo menos 300).", "head"),
                         self.refresh_bets(), treinar_todos(lambda t: self.log(t, "head"))))

    def _params(self):
        """Lê a tela na thread principal; as tarefas em background recebem só os valores."""
        return self.jogo.get(), self.n.get(), self.bet.get()

    def _analysis(self, jogo, n, bet, aviso=True):
        draws = load_sorteios(jogo, n)
        if not draws:
            raise RuntimeError("Sem dados. Clique em 'Baixar resultados'.")
        if aviso:
            self.log("Analisando e treinando o modelo...", "head")
        if M[jogo].get("colunas"):
            rep, cols = analyze_ss(draws)
            self.dados = {"jogo": jogo, "cols": cols}
            self.last_report = rep + "\n\nÚltimos concursos:\n" + "\n".join(
                f"  {c}: " + " ".join(map(str, d)) for c, d in draws[:15])
        else:
            rep, freq, atr, st, somas = analyze(jogo, draws, bet)
            # o modelo usa o histórico inteiro (teste de até 1.000 concursos), mesmo quando a análise usa poucos
            mrep, pick = learn(jogo, load_sorteios(jogo, 100000), bet, teste=0)   # todo o histórico: mais poder
            rep += "\n" + mrep
            self.dados = {"jogo": jogo, "freq": freq, "atraso": atr, "soma": st, "somas": somas, "pick": pick}
            self.last_report = rep + "\n\nÚltimos concursos:\n" + recent_text(draws)
        self.after(0, lambda: self.show_chart("freq"))
        return rep

    def run(self):
        p = self._params()
        self.bg(lambda: self.log(self._analysis(*p)))

    def sug(self):
        p = self._params()

        def f():
            self.log("Gerando apostas sugeridas...", "head")
            self._analysis(*p, aviso=False)
            jogo = self.dados["jogo"]
            self.log("\nApostas sugeridas (as mesmas do Otimizador: cartões espalhados e, com o modelo de "
                     "popularidade, números pouco jogados):", "head")
            orc = float(load_prefs().get("orcamento") or 0)
            qtd = max(1, min(5, int(orc // JOGOS[jogo][4]))) if orc else 5   # respeita o orçamento salvo
            sugs = sugestoes(jogo, qtd, p[2] if not M[jogo].get("colunas") else None)
            self.last_sugs = [(jogo, a) for a in sugs]
            for a in sugs:
                fd = fator_divisao(jogo, a)
                self.log("  " + fmt_bet(jogo, a, []) + (f"   (se ganhar, divide com {fd:.2f}x a média de ganhadores)"
                                                        if fd else ""))
            self.log(texto_chances_cartoes(jogo, sugs))
            ev = valor_esperado(jogo)
            self.ev_txt = texto_ev(ev)
            c = custo(jogo, sugs[0], [])
            self.log(f"Cada aposta: custo {brl(c)}, valor esperado {brl(ev['ev'] * c / ev['preco'])}, "
                     f"retorno {ev['retorno']:.0%}. Detalhes na aba Retorno.")
            self.log("Use 'Salvar sugeridas' na aba Minhas apostas para acompanhar estas apostas."
                     + (" Trevos/time/mês são sorteados ao salvar (dá para trocar em 'Editar')."
                        if jogo in EXTRA or M[jogo].get("trevos") else ""))
        self.bg(f)

    def show_chart(self, kind):
        s = self.dados
        if not s:
            return
        if s["jogo"] == "supersete":
            self.chart.show("heat", cols=s["cols"], title="Super Sete: quantas vezes cada número saiu em cada coluna")
            return
        N, K = JOGOS[s["jogo"]][:2]
        nums = list(range(1, N + 1))
        if kind == "freq":
            total = sum(s["freq"].values()) / K
            self.chart.show("bars", labels=nums, values=[s["freq"].get(i, 0) for i in nums],
                            title=f"Frequência de cada dezena ({total:.0f} concursos)",
                            ref=total * K / N, reflabel="esperado pelo acaso")
        elif kind == "atraso":
            self.chart.show("bars", labels=nums, values=[s["atraso"][i] for i in nums], color=IA_COLOR,
                            title="Atraso: concursos desde a última vez que cada dezena saiu",
                            ref=N / K, reflabel="atraso médio esperado")
        elif s["somas"]:
            lo, hi = min(s["somas"]), max(s["somas"])
            nb = 20
            w = max(1, math.ceil((hi - lo + 1) / nb))
            cnt = Counter((x - lo) // w for x in s["somas"])
            self.chart.show("bars", labels=[lo + i * w for i in range(nb)], values=[cnt.get(i, 0) for i in range(nb)],
                            color=WARN, title=f"Distribuição da soma das dezenas (faixas de {w})")

    # ---------- apostas ----------
    def add_bet(self):
        jogo = self.jogo.get()
        txt = self.bet_in.get()
        # Lotomania: '+ espelho' registra também a aposta-espelho (os outros 50 números, uma aposta a mais)
        com_espelho = M[jogo].get("zero_e_100") and "espelho" in txt.lower()
        try:
            nums, trevos = parse_bet(jogo, re.sub(r"\+?\s*espelho", "", txt, flags=re.I) if com_espelho else txt)
        except ValueError as e:
            messagebox.showerror("Aposta inválida", str(e))
            return
        self._save_bets([(jogo, nums, trevos)] + ([(jogo, espelho(nums), [])] if com_espelho else []))
        self.bet_in.delete(0, "end")

    def add_sugs(self):
        if not self.last_sugs:
            messagebox.showinfo("Sugeridas", "Clique em 'Sugerir apostas' primeiro.")
            return
        self._save_bets([(j, a, extra_aleatorio(j)) for j, a in self.last_sugs])
        self.last_sugs = []

    def _save_bets(self, items):
        total = 0.0
        for jogo, nums, trevos in items:
            desde = self.desde.get() if jogo == self.jogo.get() else last_concurso(jogo) + 1
            bid = save_bet(self.user, jogo, nums, trevos, desde)
            total += custo(jogo, nums, trevos)
            if self.lanca.get():
                add_banca(self.user, "Gasto", custo(jogo, nums, trevos), f"Aposta #{bid} {jogo}")
        self.log(f"{len(items)} aposta(s) salva(s). Custo: {brl(total)}", "head")
        self.refresh_bets()
        self.refresh_banca()
        if self.lanca.get():
            self.check_limit()

    def edit_bet(self):
        sel = self.tree.selection()
        if len(sel) != 1:
            messagebox.showinfo("Editar", "Selecione uma aposta.")
            return
        bid, jogo = int(self.tree.set(sel[0], "id")), self.tree.set(sel[0], "jogo")
        dica = {"timemania": "Time do Coração (ex.: flamengo)", "diadesorte": "Mês da Sorte (ex.: outubro ou 10)",
                "maismilionaria": "Trevos (ex.: 1 4)"}.get(jogo)
        if not dica:
            messagebox.showinfo("Editar", f"{jogo} não tem trevos, time nem mês.")
            return
        txt = simpledialog.askstring("Editar aposta", f"Aposta #{bid} ({jogo})\n{dica}:", parent=self)
        if not txt:
            return
        try:
            _, tv = editar_extra(self.user, bid, txt)
        except ValueError as e:
            messagebox.showerror("Editar", str(e))
            return
        self.log(f"Aposta #{bid} atualizada: + {' '.join(map(str, tv))}", "head")
        self.refresh_bets()

    def backup(self):
        path = filedialog.asksaveasfilename(defaultextension=".db", filetypes=[("Backup LuckHarness", "*.db")],
                                            initialfile=f"luckharness-backup-{date.today().isoformat()}.db")
        if path:
            try:
                backup_db(path)
                messagebox.showinfo("Backup", f"Backup salvo em:\n{path}")
            except Exception as e:
                log.exception("backup")
                messagebox.showerror("Backup", str(e))

    def restaurar(self):
        path = filedialog.askopenfilename(filetypes=[("Backup LuckHarness", "*.db")])
        if not path or not messagebox.askyesno(
                "Restaurar", "Isso substitui TODOS os dados atuais (usuários, apostas, banca) pelo backup. Continuar?"):
            return
        try:
            restore_db(path)
        except Exception as e:
            log.exception("restore")
            messagebox.showerror("Restaurar", str(e))
            return
        messagebox.showinfo("Restaurar", "Backup restaurado. O app vai fechar; abra de novo.")
        self.fechar()

    def del_bet(self):
        ids = [int(self.tree.set(i, "id")) for i in self.tree.selection()]
        if ids and messagebox.askyesno("Excluir", f"Excluir {len(ids)} aposta(s)?"):
            with db() as c:
                c.executemany("DELETE FROM bets WHERE id=? AND user=?", [(i, self.user) for i in ids])
            self.refresh_bets()

    def refresh_bets(self, avisar=False):
        def f():
            bets = check_bets(self.user)
            self.after(0, lambda: self._fill_bets(bets))
            if avisar and bets:
                n = sum(len(b["premiadas"]) for b in bets)
                tot = sum(b["ganhos"] for b in bets)
                self.log(f"Suas apostas: {len(bets)} salvas, {n} premiação(ões), total {brl(tot)}.", "head")
        threading.Thread(target=f, daemon=True).start()

    def _fill_bets(self, bets):
        self.bets = bets
        self.tree.delete(*self.tree.get_children())
        for b in bets:
            self.tree.insert("", "end", values=(b["id"], b["jogo"], fmt_bet(b["jogo"], b["dezenas"], b["trevos"]),
                                                b["desde"], b["conferidos"], b["melhor"], brl(b['ganhos'])))

    def show_bet(self):
        sel = self.tree.selection()
        if not sel:
            return
        b = next((x for x in self.bets if x["id"] == int(self.tree.set(sel[0], "id"))), None)
        if not b:
            return
        self.bet_info.delete("1.0", "end")
        info = [f"Aposta #{b['id']} ({b['jogo']}): {fmt_bet(b['jogo'], b['dezenas'], b['trevos'])}",
                f"Custo: {brl(custo(b['jogo'], b['dezenas'], b['trevos']))} | conferida em {b['conferidos']} "
                f"concurso(s) desde o {b['desde']} | melhor resultado: {b['melhor']} acertos"]
        if not b["conferidos"]:
            info.append("Ainda não houve sorteio para esta aposta.")
        for conc, desc, val in b["premiadas"]:
            info.append(f"  concurso {conc}: {desc}" + (f"  ->  {brl(val)}" if val else
                                                         "  ->  valor não informado (faixa acumulou)"))
        if b["conferidos"] and not b["premiadas"]:
            info.append("Nenhuma premiação até agora.")
        self.bet_info.insert("end", "\n".join(info))

    def export(self, fmt):
        if not self.bets:
            messagebox.showinfo("Exportar", "Nenhuma aposta salva.")
            return
        path = filedialog.asksaveasfilename(defaultextension="." + fmt, initialfile=f"apostas.{fmt}",
                                            filetypes=[("PDF", "*.pdf")] if fmt == "pdf" else [("Planilha", "*.csv")])
        if not path:
            return
        try:
            if fmt == "csv":
                with open(path, "w", newline="", encoding="utf-8-sig") as fh:
                    w = csv.writer(fh, delimiter=";")
                    w.writerow(["#", "Jogo", "Dezenas", "Trevos", "Desde", "Custo", "Melhor", "Prêmios"])
                    for b in self.bets:
                        w.writerow([b["id"], b["jogo"], " ".join(map(str, b["dezenas"])),
                                    " ".join(map(str, b["trevos"])), b["desde"],
                                    f"{custo(b['jogo'], b['dezenas'], b['trevos']):.2f}".replace(".", ","),
                                    b["melhor"], f"{b['ganhos']:.2f}".replace(".", ",")])
            else:
                lines = []
                for b in self.bets:
                    lines.append(f"#{b['id']:<4} {b['jogo']:<15} {fmt_bet(b['jogo'], b['dezenas'], b['trevos'])}")
                    lines.append(f"      a partir do concurso {b['desde']}  |  "
                                 + brl(custo(b['jogo'], b['dezenas'], b['trevos'])))
                    lines.append("")
                export_pdf(path, f"LuckHarness - apostas de {self.user} ({date.today():%d/%m/%Y})", lines)
            self.log(f"Exportado: {path}", "head")
        except Exception as e:
            messagebox.showerror("Exportar", str(e))

    # ---------- fechamento ----------
    def fech_defaults(self):
        jogo = self.jogo.get()
        N, K, mn, faixas = JOGOS[jogo][:4]
        self.f_k.set(mn)
        self.f_t.set(min(f for f in faixas if f > 0))
        self.f_m.set(K)

    def fill_fech(self):
        jogo = self.jogo.get()
        if M[jogo].get("colunas"):
            messagebox.showinfo("Fechamento", "O Super Sete não tem fechamento.")
            return
        qtd = min(JOGOS[jogo][2] + 4, MAX_DEZ[jogo], JOGOS[jogo][0])
        nums = sugestoes(jogo, 1, qtd)[0]
        self.f_nums.delete(0, "end")
        self.f_nums.insert(0, " ".join(f"{x:02d}" for x in sorted(nums)))

    def run_fech(self):
        jogo = self.jogo.get()
        if M[jogo].get("colunas"):
            messagebox.showinfo("Fechamento", "Fechamento não se aplica à Super Sete (um número por coluna).")
            return
        N, K = JOGOS[jogo][:2]
        nums = sorted({int(x) for x in re.findall(r"\d+", self.f_nums.get()) if 1 <= int(x) <= N})
        k, t, m = self.f_k.get(), self.f_t.get(), self.f_m.get()
        if m > K:
            messagebox.showerror("Fechamento", f"No {jogo} saem só {K} dezenas: a condição deve ser no máximo {K}.")
            return
        self.f_out.delete("1.0", "end")
        self.f_out.insert("end", "Calculando...\n")
        prog = lambda n, falta: self.after(0, lambda: (self.f_out.delete("1.0", "end"), self.f_out.insert(
            "end", f"Calculando... {n} cartões, faltam {falta} combinações para cobrir\n")))

        def f():
            try:
                info = {}
                cards = fechamento(nums, k, t, m, prog, tempo=4, info=info)
            except Exception as e:
                msg = f"ERRO: {e}\n"
                self.after(0, lambda: (self.f_out.delete("1.0", "end"), self.f_out.insert("end", msg)))
                return
            preco = JOGOS[jogo][4] * math.comb(k, JOGOS[jogo][2])
            eco = info["guloso"] - info["final"]
            txt = [f"{len(cards)} cartões de {k} dezenas | custo {brl(len(cards) * preco)}"
                   + (f"  (a otimização tirou {eco} cartão(ões): economia de {brl(eco * preco)})" if eco else ""),
                   f"Garantia: se {m} das {len(nums)} dezenas escolhidas forem sorteadas, pelo menos um cartão "
                   f"faz {t} acertos.",
                   f"Para comparar: jogar todas as combinações custaria {math.comb(len(nums), k):,} cartões.", ""]
            txt += [f"  {i+1:>3}: " + " ".join(f"{x:02d}" for x in c) for i, c in enumerate(cards)]
            txt += ["", "O fechamento organiza a cobertura, mas não muda a probabilidade por real apostado."]
            self.f_cards = [(jogo, c) for c in cards]
            self.after(0, lambda: (self.f_out.delete("1.0", "end"), self.f_out.insert("end", "\n".join(txt))))
        threading.Thread(target=f, daemon=True).start()

    def save_fech(self):
        if not self.f_cards:
            messagebox.showinfo("Fechamento", "Gere um fechamento primeiro.")
            return
        if self.f_cards[0][0] == "maismilionaria":
            self._save_bets([(j, c, [1, 2]) for j, c in self.f_cards])
        else:
            self._save_bets([(j, c, []) for j, c in self.f_cards])
        self.f_cards = []

    # ---------- banca ----------
    def add_mov(self):
        try:
            s = self.b_val.get().replace("R$", "").strip()
            v = float(s.replace(".", "").replace(",", ".") if "," in s else s)
            if v <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("Banca", "Informe um valor válido, ex.: 25,50")
            return
        add_banca(self.user, self.b_tipo.get(), v, self.b_desc.get().strip())
        self.b_val.delete(0, "end"); self.b_desc.delete(0, "end")
        self.refresh_banca()
        if self.b_tipo.get() == "Gasto":
            self.check_limit()

    def del_mov(self):
        ids = [int(self.b_tree.set(i, "id")) for i in self.b_tree.selection()]
        if ids:
            with db() as c:
                c.executemany("DELETE FROM banca WHERE id=? AND user=?", [(i, self.user) for i in ids])
            self.refresh_banca()

    def save_lim(self):
        try:
            v = float(self.b_lim.get().replace(",", ".") or 0)
        except ValueError:
            messagebox.showerror("Banca", "Limite inválido.")
            return
        save_prefs(**{f"limite_{self.user}": v})
        self.refresh_banca()

    def refresh_banca(self):
        g, p = banca_mes(self.user)
        lim = load_prefs().get(f"limite_{self.user}") or 0
        self.gasto_lbl.configure(text=f"Gasto no mês: {brl(g)}" + (f" de {brl(lim)}" if lim else ""),
                                 foreground="#ff6b6b" if lim and g >= lim else WARN if lim and g >= .8 * lim
                                 else MUTED)
        txt = f"Este mês: gastos {brl(g)}  |  prêmios {brl(p)}  |  saldo {brl(p - g)}"
        if lim:
            txt += f"  |  {g / lim:.0%} do limite de {brl(lim)}"
        self.b_sum.configure(text=txt, foreground="#ff6b6b" if lim and g >= lim else WARN if lim and g >= .8 * lim
                             else FG)
        self.b_tree.delete(*self.b_tree.get_children())
        for r in db().execute("SELECT id, data, tipo, valor, descr FROM banca WHERE user=? ORDER BY id DESC LIMIT 300",
                              (self.user,)):
            self.b_tree.insert("", "end", values=(r[0], date.fromisoformat(r[1]).strftime("%d/%m/%Y"), r[2],
                                                  brl(r[3]), r[4]))

    def check_limit(self):
        lim = load_prefs().get(f"limite_{self.user}") or 0
        g, _ = banca_mes(self.user)
        if lim and g >= lim:
            messagebox.showwarning("Limite da banca", f"Você já gastou {brl(g)} este mês, acima do seu limite "
                                                      f"de {brl(lim)}. Hora de parar.")
        elif lim and g >= .8 * lim:
            messagebox.showinfo("Limite da banca", f"Atenção: {brl(g)} gastos, {g / lim:.0%} do limite mensal.")

    # ---------- IA ----------
    def set_model(self):
        self.model = self.model_cb.get()
        save_prefs(model=self.model)

    def clear_chat(self):
        if messagebox.askyesno("Limpar", "Apagar o histórico de conversa com a Harness IA?"):
            with db() as c:
                c.execute("DELETE FROM chats WHERE user=?", (self.user,))
            self.chat.delete("1.0", "end")

    # limite de caracteres de cada parte do contexto da IA (~4 caracteres por token; cabe em num_ctx=8192
    # junto com o sistema, o histórico e a resposta)
    CTX_LIMITES = {"fatos": 2000, "analise": 2500, "apostas": 1200, "sugestoes": 1800, "retorno": 1500,
                   "plano": 2500, "ranking": 1000}

    def context(self):
        L = self.CTX_LIMITES
        corta = lambda t, k: t if len(t) <= L[k] else t[:L[k]] + "\n[...resumido]"
        partes = [corta(fatos_app(self.jogo.get()), "fatos"),
                  corta(self.last_report or "(nenhuma análise ainda)", "analise")]
        if self.bets:
            partes.append(corta("Apostas salvas:\n" + "\n".join(
                f"  #{b['id']} {b['jogo']} {fmt_bet(b['jogo'], b['dezenas'], b['trevos'])} | conferida em "
                f"{b['conferidos']} | melhor {b['melhor']} | prêmios {brl(b['ganhos'])}" for b in self.bets[:10]),
                "apostas"))
        if self.last_sugs:
            j0 = self.last_sugs[0][0]
            ev = valor_esperado(j0)
            partes.append(corta(fatos_sugestoes(j0, [a for _, a in self.last_sugs[:6]])
                                + f"\nCada uma: custo {brl(JOGOS[j0][4])}, retorno {ev['retorno']:.0%}.", "sugestoes"))
        if self.ev_txt:
            partes.append(corta(self.ev_txt, "retorno"))
        if self.otim_txt:
            partes.append(corta("Plano do Otimizador:\n" + self.otim_txt, "plano"))
        if self.ranking:
            partes.append(corta(self.ranking, "ranking"))
        g, p = banca_mes(self.user)
        lim = load_prefs().get(f"limite_{self.user}") or 0
        partes.append(f"Banca do mês: gastos {brl(g)}, prêmios {brl(p)}, limite {brl(lim)}")
        return "\n\n".join(partes)

    def ai(self, pergunta=None):
        q = pergunta or self.q.get().strip() or "Avalie esta análise e proponha estratégia de aposta e gestão de banca."
        self.q.delete(0, "end")
        self.chat.insert("end", "\nVocê\n", "you"); self.chat.insert("end", q + "\n")
        self.chat.insert("end", "\nHarness IA\n", "ia")
        self.chat.insert("end", "Pensando…", "pensando")
        self.chat.see("end")
        msgs = load_chat(self.user, 6) + [{"role": "user", "content": f"{q}\n\nContexto:\n{self.context()}"}]
        jogo = self.jogo.get()
        # números que vieram do app: sugestões (atuais e anteriores), Otimizador, apostas salvas e o que o próprio
        # usuário citou na pergunta (ex.: uma sugestão antiga)
        self.sugs_vistas = getattr(self, "sugs_vistas", []) + [a for _, a in self.last_sugs]
        N = JOGOS[self.jogo.get()][0]
        da_pergunta = [int(x) for x in re.findall(r"\b\d{1,2}\b", q) if 1 <= int(x) <= N]
        permitidos = (self.sugs_vistas + [c for _, c, _ in self.otim_cards] + [b["dezenas"] for b in self.bets]
                      + ([da_pergunta] if da_pergunta else []))
        sugs_fator = [(a, fator_divisao(j, a)) for j, a in self.last_sugs]
        save_chat(self.user, "user", q)

        def escreve(t):
            if self.chat.tag_ranges("pensando"):
                self.chat.delete("pensando.first", "pensando.last")
            self.chat.insert("end", t); self.chat.see("end")
        cb = lambda t: self.after(0, lambda: escreve(t))

        def f():
            try:
                resp = ask_ollama(self.model, msgs, cb)
                save_chat(self.user, "assistant", resp)
                avisos = checar_resposta(resp, jogo, permitidos, sugs_fator)
                if avisos:
                    cb("\n\n[Aviso do LuckHarness: a resposta acima contradiz o que o app testou]\n"
                       + "\n".join(f"- A IA {a}" for a in avisos))
            except Exception as e:
                if any(k in str(e).lower() for k in ("10061", "refused", "recusou")):
                    cb("A IA local (Ollama) não está aberta. Instale em ollama.com, abra o programa e rode "
                       f"'ollama pull {self.model}' uma vez. O resto do app funciona sem ela.")
                else:
                    cb(f"[falha Ollama: {e}. Abra o Ollama e rode 'ollama pull {self.model}']")
            cb("\n")
        threading.Thread(target=f, daemon=True).start()


if __name__ == "__main__":
    if "--alerta" in sys.argv:
        modo_alerta()
        sys.exit()
    user = remembered_user()
    while True:
        if not user:
            lg = Login()
            lg.mainloop()
            user = lg.user
        if not user:
            break
        app = App(user)
        app.mainloop()
        if not app.logout:
            break
        user = None
