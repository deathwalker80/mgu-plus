"""MGU Plus - root SSH e App Store (mgu-store) per le head unit BMW MGU
(iDrive 7 / iDrive 8), in una sola schermata: Connessione vettura, Accesso SSH
MGU, Applicazioni MGU.

Interfaccia Tkinter sopra ssh_enable.py: ogni azione esegue ssh_enable.main in
un thread e ne mostra il registro nella finestra, esattamente come da riga di
comando. "Connetti vettura" (azione `check`) scrive data/mgu-store/state.json,
che riempie i tre riquadri: la vettura, se root SSH risponde, e le versioni
dello Store sulla head unit confrontate con le ultime pubblicate.

Si impacchetta in un .exe per Windows con PyInstaller (build_exe.bat).
"""

import json
import os
import queue
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser

import tkinter as tk
from tkinter import messagebox, ttk

if getattr(sys, "frozen", False):
    HERE = os.path.dirname(os.path.abspath(sys.executable))
else:
    HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
# File di sola lettura distribuiti con il programma (logo): PyInstaller li
# scompatta in sys._MEIPASS (la cartella _internal della build).
RESOURCES = getattr(sys, "_MEIPASS", HERE)


class _NullWriter:
    """Stand-in for sys.stdout/err. In a windowed (--noconsole) PyInstaller exe
    sys.stdout and sys.stderr are None, so any print() before the GUI redirect
    would raise and the exe would vanish on launch. Keep writes harmless."""

    def write(self, *_):
        pass

    def flush(self):
        pass


if sys.stdout is None:
    sys.stdout = _NullWriter()
if sys.stderr is None:
    sys.stderr = _NullWriter()

import ssh_enable
from i18n import Translator
from mgu7 import discovery
from mgu7 import store as store_mod
from mgu7 import coding as coding_mod
from mgu7.flash import ID7, ID8_BDC3, ID8_BCP

APP_NAME = "MGU Plus"
APP_VERSION = "1.3.0"
WINDOW_TITLE = "%s %s" % (APP_NAME, APP_VERSION)
LANGUAGE = "it"
# Dove mgu-store pubblica le sue versioni (release "catalog" su GitHub).
STORE_REPO = "robertnisipeanu/mgu-store"
# Dove si pubblicano le versioni di MGU Plus (GitHub releases del fork).
UPDATE_REPO = "deathwalker80/mgu-plus"
UPDATE_API = "https://api.github.com/repos/%s/releases/latest" % UPDATE_REPO

ARCHES = (ID7, ID8_BDC3, ID8_BCP)
# The flash contract MUST match the head unit. Flashing an ID8 with the ID7
# image (or vice-versa) partially writes the wrong regions and leaves the unit
# stuck in programming mode (issue #47). The software baseline alone (030 vs
# 036) is not a reliable enough discriminator to auto-pick, so we make the user
# choose their exact variant - there is no silent default.
VARIANT_PROMPT = "— scegli —"
VARIANT_CHOICES = (
    (ID7, "ID7  (software 030.xxx)"),
    (ID8_BDC3, "ID8 BDC3  (036.xxx, sperimentale)"),
    (ID8_BCP, "ID8 BCP  (036.xxx, sperimentale)"),
)
_VARIANT_BY_LABEL = {label: code for code, label in VARIANT_CHOICES}

# Tavolozza: lo scuro dell'iDrive - superfici antracite, accento blu BMW.
BG = "#0a0d12"
SURFACE = "#11161e"
SURFACE2 = "#171e28"
HOVER = "#1f2836"
BORDER = "#242f3f"
TEXT = "#e6ebf2"
MUTED = "#8793a5"
ACCENT = "#1c69d4"
ACCENT_HI = "#2f7ff0"
ACCENT_LO = "#123f80"
OK = "#2fbf71"
WARN = "#f0a030"
ERR = "#e5484d"
DISABLED_BG = "#19202a"
DISABLED_FG = "#566173"
CONSOLE_BG = "#06090d"
CONSOLE_FG = "#c9d3e0"
M_STRIPES = ("#4aa3ff", "#1c3f94", "#e2231a")
FONT = "Segoe UI"
MONO = "Consolas"

# Stato delle righe della tabella (mgu7/store.py compare()) -> testo, colore.
ROW_TEXT = {
    store_mod.ROW_CURRENT: ("Aggiornato", OK),
    store_mod.ROW_UPDATE: ("Aggiornamento disponibile", WARN),
    store_mod.ROW_MISSING: ("Non installato", ACCENT_HI),
    store_mod.ROW_NEWER: ("Più recente di quella pubblicata", MUTED),
    store_mod.ROW_OTHER: ("Installato (altra fonte)", MUTED),
}


class _QueueWriter:
    """File-like object funnelling writes to a thread-safe queue."""

    def __init__(self, q):
        self.q = q
        self.pending = ""

    def write(self, text):
        if text:
            self.pending += text
            while "\n" in self.pending:
                line, self.pending = self.pending.split("\n", 1)
                self.q.put(line + "\n")
        return len(text)

    def flush(self):
        if self.pending:
            self.q.put(self.pending)
            self.pending = ""


def _dark_title_bar(root):
    """Windows 10/11: barra del titolo scura, così tutta la finestra segue il
    tema. Innocuo altrove o su versioni vecchie."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        value = ctypes.c_int(1)
        for attribute in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (build nuove, vecchie)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(value),
                                                          ctypes.sizeof(value)) == 0:
                break
    except Exception:  # noqa: BLE001 - solo estetica
        pass


class App:
    def __init__(self, root, auto_check=True):
        self.root = root
        self.q = queue.Queue()
        # Callbacks the worker threads hand back to the Tk thread (_drain runs them).
        self.calls = queue.Queue()
        self.worker = None
        self.i18n = Translator()
        self.i18n.select(LANGUAGE)
        self._history = []
        self._is_busy = False
        self.state = None
        self._store_loading = False
        self._store_error = None

        root.title(WINDOW_TITLE)
        root.configure(bg=BG)
        root.minsize(1240, 680)
        height = min(880, max(680, root.winfo_screenheight() - 80))
        root.geometry("1280x%d" % height)
        self._set_icon()

        self.vin = tk.StringVar()
        self.gateway = tk.StringVar(value="auto")
        self.mgu_ip = tk.StringVar()
        self.arch = tk.StringVar(value="")  # vuota finché l'utente non sceglie la variante
        self.variant_label = tk.StringVar(value=VARIANT_PROMPT)
        self.disable_debug = tk.BooleanVar(value=True)
        self.unique_key = tk.BooleanVar(value=False)

        self._style()
        self._build()
        self._refresh()
        _dark_title_bar(root)
        if auto_check:
            # All'avvio, senza aspettare la vettura: le ultime versioni di
            # mgu-store da GitHub riempiono subito la tabella.
            self.root.after(400, lambda: self.do_store_latest(quiet=True))
        self._drain_id = self.root.after(100, self._drain)
        # Alla chiusura il ciclo del registro si ferma, senza "invalid command name".
        self.root.bind("<Destroy>", self._on_destroy, add="+")

    # --- stile ------------------------------------------------------------

    def _set_icon(self):
        try:
            ico = os.path.join(RESOURCES, "mgu_plus.ico")
            png = os.path.join(RESOURCES, "mgu_plus.png")
            if sys.platform == "win32" and os.path.isfile(ico):
                self.root.iconbitmap(default=ico)
            elif os.path.isfile(png):
                self._icon = tk.PhotoImage(file=png)
                self.root.iconphoto(True, self._icon)
        except tk.TclError:
            pass

    def _style(self):
        st = ttk.Style()
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure(".", background=BG, foreground=TEXT, font=(FONT, 10), bordercolor=BORDER,
                     lightcolor=BORDER, darkcolor=BORDER, troughcolor=SURFACE, focuscolor=ACCENT,
                     selectbackground=ACCENT_LO, selectforeground=TEXT, insertcolor=TEXT)

        def button(name, bg, fg, active, border):
            st.configure(name, background=bg, foreground=fg, font=(FONT, 9, "bold"), padding=(9, 7),
                         borderwidth=1, bordercolor=border, lightcolor=bg, darkcolor=bg, focuscolor=bg,
                         relief="flat", anchor="center")
            st.map(name,
                   background=[("disabled", DISABLED_BG), ("pressed", active), ("active", active)],
                   foreground=[("disabled", DISABLED_FG)],
                   bordercolor=[("disabled", DISABLED_BG), ("active", border)],
                   lightcolor=[("disabled", DISABLED_BG), ("active", active)],
                   darkcolor=[("disabled", DISABLED_BG), ("active", active)])

        button("Primary.TButton", ACCENT, "#ffffff", ACCENT_HI, ACCENT)
        button("Secondary.TButton", SURFACE2, TEXT, HOVER, BORDER)
        button("Danger.TButton", "#231518", "#ff8f92", "#321b20", "#4a2329")
        button("Small.TButton", SURFACE2, TEXT, HOVER, BORDER)
        st.configure("Small.TButton", padding=(9, 4))

        st.configure("TEntry", fieldbackground=SURFACE2, foreground=TEXT, insertcolor=TEXT,
                     bordercolor=BORDER, lightcolor=SURFACE2, darkcolor=SURFACE2, padding=5)
        st.map("TEntry", bordercolor=[("focus", ACCENT)], lightcolor=[("focus", ACCENT)])

        st.configure("TCombobox", fieldbackground=SURFACE2, background=SURFACE2, foreground=TEXT,
                     arrowcolor=TEXT, bordercolor=BORDER, lightcolor=SURFACE2, darkcolor=SURFACE2, padding=4,
                     arrowsize=13)
        st.map("TCombobox",
               fieldbackground=[("readonly", SURFACE2)], background=[("readonly", SURFACE2), ("active", HOVER)],
               foreground=[("readonly", TEXT)], selectbackground=[("readonly", SURFACE2)],
               selectforeground=[("readonly", TEXT)], bordercolor=[("focus", ACCENT)],
               arrowcolor=[("disabled", DISABLED_FG)])
        self.root.option_add("*TCombobox*Listbox.background", SURFACE2)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
        self.root.option_add("*TCombobox*Listbox.font", (FONT, 10))
        self.root.option_add("*TCombobox*Listbox.borderWidth", 0)

        st.configure("Dark.TCheckbutton", background=SURFACE, foreground=TEXT, font=(FONT, 9),
                     indicatorbackground=SURFACE2, indicatorforeground="#ffffff", indicatorrelief="flat",
                     indicatormargin=(0, 0, 8, 0), focuscolor=SURFACE, bordercolor=BORDER)
        st.map("Dark.TCheckbutton", background=[("active", SURFACE)],
               indicatorbackground=[("selected", ACCENT), ("active", HOVER)])

        # Solo il cursore in una guida scura: niente frecce, niente aspetto di serie.
        for orient in ("Vertical", "Horizontal"):
            name = "Dark.%s.TScrollbar" % orient
            st.layout(name, [("%s.Scrollbar.trough" % orient, {
                "children": [("%s.Scrollbar.thumb" % orient, {"expand": "1", "sticky": "nswe"})],
                "sticky": "ns" if orient == "Vertical" else "we"})])
            st.configure(name, background="#2a3546", troughcolor=SURFACE2, bordercolor=SURFACE2,
                         lightcolor="#2a3546", darkcolor="#2a3546", gripcount=0, arrowsize=9)
            st.map(name, background=[("active", "#3b4b62")])

        st.configure("Dark.Treeview", background=SURFACE2, fieldbackground=SURFACE2, foreground=TEXT,
                     rowheight=25, bordercolor=BORDER, lightcolor=SURFACE2, darkcolor=SURFACE2,
                     font=(FONT, 9))
        st.map("Dark.Treeview", background=[("selected", ACCENT_LO)], foreground=[("selected", "#ffffff")])
        st.configure("Dark.Treeview.Heading", background=SURFACE, foreground=MUTED, relief="flat",
                     bordercolor=BORDER, lightcolor=SURFACE, darkcolor=SURFACE, font=(FONT, 9, "bold"),
                     padding=(8, 5))
        st.map("Dark.Treeview.Heading", background=[("active", SURFACE)])
        st.layout("Dark.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])

        st.configure("Dark.Horizontal.TProgressbar", troughcolor=SURFACE2, background=ACCENT,
                     bordercolor=SURFACE2, lightcolor=ACCENT, darkcolor=ACCENT, thickness=6)

    # --- impaginazione ----------------------------------------------------

    def _build(self):
        self._build_header()

        main = tk.Frame(self.root, bg=BG)
        main.pack(fill="x", padx=14, pady=(12, 0))
        main.columnconfigure(0, minsize=610)
        main.columnconfigure(1, weight=1)
        self.card_car = self._build_car_card(main)
        self.card_car.grid(row=0, column=0, sticky="nsew", padx=(0, 12), pady=(0, 12))
        self.card_ssh = self._build_ssh_card(main)
        self.card_ssh.grid(row=1, column=0, sticky="nsew", padx=(0, 12))
        self.card_store = self._build_store_card(main)
        self.card_store.grid(row=0, column=1, rowspan=2, sticky="nsew")

        bar = tk.Frame(self.root, bg=BG)
        bar.pack(fill="x", padx=14, pady=(9, 7))
        self.progress = ttk.Progressbar(bar, mode="determinate", value=0, style="Dark.Horizontal.TProgressbar")
        self.progress.pack(side="left", fill="x", expand=True)
        self.status = tk.Label(bar, text="Pronto: collega il cavo ENET (o l'hotspot dell'auto) e premi "
                               "“Connetti vettura”.", bg=BG, fg=MUTED, font=(FONT, 9), anchor="e")
        self.status.pack(side="left", padx=(14, 0))

        self._build_log()

    def _build_header(self):
        header = tk.Frame(self.root, bg=SURFACE)
        header.pack(fill="x")
        self._logo = None
        try:
            self._logo = tk.PhotoImage(file=os.path.join(RESOURCES, "mgu_plus_header.png"))
        except tk.TclError:
            pass
        if self._logo is not None:
            tk.Label(header, image=self._logo, bg=SURFACE).pack(side="left", padx=(16, 12), pady=6)
        titles = tk.Frame(header, bg=SURFACE)
        titles.pack(side="left", pady=6, padx=(0 if self._logo else 16, 0))
        top = tk.Frame(titles, bg=SURFACE)
        top.pack(anchor="w")
        tk.Label(top, text="MGU PLUS", bg=SURFACE, fg=TEXT, font=(FONT, 17, "bold")).pack(side="left")
        tk.Label(top, text="v" + APP_VERSION, bg=SURFACE, fg=MUTED,
                 font=(FONT, 9)).pack(side="left", padx=(8, 0), pady=(7, 0))
        tk.Label(titles, text="Root SSH e App Store per BMW iDrive 7  ·  iDrive 8 sperimentale", bg=SURFACE, fg=MUTED,
                 font=(FONT, 9)).pack(anchor="w")

        self.btn_update = ttk.Button(header, text="Aggiornamenti MGU Plus", style="Small.TButton",
                                     command=self.do_program_update)
        self.btn_update.pack(side="right", padx=(10, 16))
        self.link = tk.Label(header, text="", bg=SURFACE2, fg=MUTED, font=(FONT, 9, "bold"), padx=12, pady=5)
        self.link.pack(side="right")

        stripes = tk.Frame(self.root, bg=BG, height=3)
        stripes.pack(fill="x")
        for colour in M_STRIPES:
            tk.Frame(stripes, bg=colour, height=3).pack(side="left", fill="x", expand=True)

    def _card(self, parent, number, title):
        """Un riquadro: intestazione (numero, titolo, stato), corpo e una barra
        delle azioni in basso, sempre con l'azione principale per prima a sinistra."""
        card = tk.Frame(parent, bg=SURFACE, highlightthickness=1, highlightbackground=BORDER)
        head = tk.Frame(card, bg=SURFACE)
        head.pack(fill="x", padx=14, pady=(8, 5))
        badge = tk.Canvas(head, width=24, height=24, bg=SURFACE, highlightthickness=0)
        badge.pack(side="left")
        badge.create_oval(1, 1, 23, 23, fill=ACCENT, outline=ACCENT)
        badge.create_text(12, 12, text=str(number), fill="#ffffff", font=(FONT, 9, "bold"))
        tk.Label(head, text=title, bg=SURFACE, fg=TEXT, font=(FONT, 11, "bold")).pack(side="left", padx=(9, 0))
        chip = tk.Label(head, text="", bg=SURFACE2, fg=MUTED, font=(FONT, 8, "bold"), padx=10, pady=3)
        chip.pack(side="right")
        tk.Frame(card, bg=BORDER, height=1).pack(fill="x")
        actions = tk.Frame(card, bg=SURFACE)
        actions.pack(side="bottom", fill="x", padx=14, pady=(4, 10))
        body = tk.Frame(card, bg=SURFACE)
        body.pack(fill="both", expand=True, padx=14, pady=(6, 2))
        card.chip, card.body, card.actions = chip, body, actions
        return card

    @staticmethod
    def _set_chip(card, text, colour):
        card.chip.configure(text=text, fg=colour)

    def _pair(self, parent, row, column, label, widget):
        tk.Label(parent, text=label, bg=SURFACE, fg=MUTED, font=(FONT, 9),
                 anchor="w").grid(row=row, column=column, sticky="w", pady=3, padx=(0 if column == 0 else 18, 10))
        widget.grid(row=row, column=column + 1, sticky="w", pady=3)

    def _value(self, parent):
        return tk.Label(parent, text="—", bg=SURFACE, fg=TEXT, font=(MONO, 9), anchor="w", width=24)

    def _build_car_card(self, parent):
        card = self._card(parent, 1, "Connessione vettura")
        grid = tk.Frame(card.body, bg=SURFACE)
        grid.pack(fill="x")
        self._pair(grid, 0, 0, "Gateway", ttk.Entry(grid, textvariable=self.gateway, width=18, font=(MONO, 10)))
        self._pair(grid, 0, 2, "VIN", ttk.Entry(grid, textvariable=self.vin, width=20, font=(MONO, 10)))
        self._pair(grid, 1, 0, "IP MGU", ttk.Entry(grid, textvariable=self.mgu_ip, width=18, font=(MONO, 10)))
        self.variant_box = ttk.Combobox(grid, textvariable=self.variant_label, state="readonly", width=19,
                                        values=[label for _, label in VARIANT_CHOICES])
        self.variant_box.bind("<<ComboboxSelected>>", lambda _e: self._on_variant(self.variant_label.get()))
        self._pair(grid, 1, 2, "Tipo MGU", self.variant_box)
        self.car_serial = self._value(grid)
        self._pair(grid, 2, 0, "Seriale MGU", self.car_serial)
        self.car_hardware = self._value(grid)
        self._pair(grid, 2, 2, "Hardware MGU", self.car_hardware)
        tk.Label(card.body, text="Cavo ENET nella presa OBD, quadro in modalità diagnosi (3 pressioni rapide di "
                 "Start/Stop). Con l'hotspot Wi-Fi dell'auto solo le operazioni su SSH già attivo.",
                 bg=SURFACE, fg=MUTED, font=(FONT, 8), anchor="w", justify="left",
                 wraplength=580).pack(fill="x", pady=(4, 0))

        self.btn_check = ttk.Button(card.actions, text="Connetti vettura", style="Primary.TButton",
                                    command=self.do_check)
        self.btn_check.pack(side="left")
        self.detect_btn = ttk.Button(card.actions, text="Leggi solo VIN", style="Secondary.TButton",
                                     command=self.detect_vin)
        self.detect_btn.pack(side="left", padx=(6, 0))
        return card

    def _build_ssh_card(self, parent):
        card = self._card(parent, 2, "Accesso SSH MGU")
        opts = tk.Frame(card.body, bg=SURFACE)
        opts.pack(fill="x")
        ttk.Checkbutton(opts, text="SSH persistente con modalità debug spenta (consigliato)",
                        variable=self.disable_debug, style="Dark.TCheckbutton").pack(side="left")
        ttk.Checkbutton(opts, text="Chiave unica per l'auto (avanzato)", variable=self.unique_key,
                        style="Dark.TCheckbutton").pack(side="left", padx=(16, 0))
        coding = tk.Frame(card.body, bg=SURFACE, highlightthickness=1, highlightbackground=BORDER)
        coding.pack(fill="x", pady=(6, 0))
        tk.Label(coding, text="Codifica", bg=SURFACE, fg=TEXT, font=(FONT, 9, "bold"),
                 padx=10).pack(side="left")
        self.coding_info = tk.Label(coding, text="", bg=SURFACE, fg=MUTED, font=(FONT, 8), anchor="w")
        self.coding_info.pack(side="left", fill="x", expand=True)
        self.btn_restore = ttk.Button(coding, text="Ripristina (sperimentale)", style="Small.TButton",
                                      command=self.do_restore)
        self.btn_restore.pack(side="right", padx=(4, 6), pady=4)
        self.btn_backup = ttk.Button(coding, text="Backup codifica", style="Small.TButton", command=self.do_backup)
        self.btn_backup.pack(side="right", pady=4)
        tk.Label(card.body, text="Il flash cancella la codifica e gli abbinamenti Digital Key: prima il backup "
                 "(il programma lo propone da solo), dopo il ripristino. Il ripristino è sperimentale: tieni "
                 "a portata BimmerCode o E-Sys.", bg=SURFACE, fg="#f3c98a", font=(FONT, 8), anchor="w",
                 justify="left", wraplength=580).pack(fill="x", pady=(4, 0))

        self.btn_flash = ttk.Button(card.actions, text="Flash + Apri SSH", style="Primary.TButton",
                                    command=self.do_flash)
        self.btn_flash.pack(side="left")
        self.btn_enable = ttk.Button(card.actions, text="Apri SSH", style="Secondary.TButton",
                                     command=self.do_enable)
        self.btn_enable.pack(side="left", padx=(6, 0))
        self.btn_fix = ttk.Button(card.actions, text="Correggi chiave", style="Secondary.TButton",
                                  command=self.do_fix_key)
        self.btn_fix.pack(side="left", padx=(6, 0))
        self.btn_disable = ttk.Button(card.actions, text="Disattiva SSH", style="Danger.TButton",
                                      command=self.do_disable)
        self.btn_disable.pack(side="left", padx=(6, 0))
        return card

    def _build_store_card(self, parent):
        card = self._card(parent, 3, "Applicazioni MGU (App Store)")
        top = tk.Frame(card.body, bg=SURFACE2)
        top.pack(fill="x")
        self.btn_github = ttk.Button(top, text="Controlla aggiornamenti GitHub", style="Small.TButton",
                                     command=self.do_store_latest)
        self.btn_github.pack(side="right", padx=6, pady=4)
        self.store_line = tk.Label(top, text="", bg=SURFACE2, fg=TEXT, font=(FONT, 9, "bold"),
                                   anchor="w", justify="left", padx=10, pady=7, wraplength=330)
        self.store_line.pack(side="left", fill="x", expand=True)

        table = tk.Frame(card.body, bg=SURFACE2, highlightthickness=1, highlightbackground=BORDER)
        table.pack(fill="both", expand=True, pady=(8, 0))
        self.tree = ttk.Treeview(table, columns=("name", "installed", "latest", "state"), show="headings",
                                 style="Dark.Treeview", selectmode="none", height=6)
        for column, title, width, anchor, stretch in (("name", "Componente", 190, "w", True),
                                                      ("installed", "Installata", 82, "center", False),
                                                      ("latest", "Ultima", 82, "center", False),
                                                      ("state", "Stato", 170, "w", True)):
            self.tree.heading(column, text=title)
            self.tree.column(column, width=width, minwidth=60, anchor=anchor, stretch=stretch)
        tree_scroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview,
                                    style="Dark.Vertical.TScrollbar")
        self.tree.configure(yscrollcommand=tree_scroll.set)
        tree_scroll.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        for state, (_, colour) in ROW_TEXT.items():
            self.tree.tag_configure(state, foreground=colour)
        tk.Label(card.body, text="Con internet in auto lo Store si aggiorna anche da solo, dalla sua pagina "
                 "Installed.", bg=SURFACE, fg=MUTED, font=(FONT, 8), anchor="w").pack(fill="x", pady=(6, 0))

        self.btn_store = ttk.Button(card.actions, text="Installa / Aggiorna", style="Primary.TButton",
                                    command=self.do_store)
        self.btn_store.pack(side="left")
        self.btn_versions = ttk.Button(card.actions, text="Verifica app installate", style="Secondary.TButton",
                                       command=self.do_check)
        self.btn_versions.pack(side="left", padx=(6, 0))
        self.btn_restart = ttk.Button(card.actions, text="Riavvia MGU", style="Secondary.TButton",
                                      command=self.do_restart)
        self.btn_restart.pack(side="left", padx=(6, 0))
        self.btn_remove = ttk.Button(card.actions, text="Rimuovi Store", style="Danger.TButton",
                                     command=self.do_remove)
        self.btn_remove.pack(side="left", padx=(6, 0))
        return card

    def _build_log(self):
        card = tk.Frame(self.root, bg=SURFACE, highlightthickness=1, highlightbackground=BORDER)
        card.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        head = tk.Frame(card, bg=SURFACE)
        head.pack(fill="x", padx=14, pady=(8, 6))
        tk.Label(head, text="Registro attività", bg=SURFACE, fg=TEXT, font=(FONT, 10, "bold")).pack(side="left")
        tk.Label(head, text="(completo, in inglese, in data\\logs)", bg=SURFACE, fg=MUTED,
                 font=(FONT, 8)).pack(side="left", padx=(6, 0))
        self.btn_clear = ttk.Button(head, text="Pulisci", style="Small.TButton", command=self.clear_log)
        self.btn_clear.pack(side="right")
        ttk.Button(head, text="Apri cartella log", style="Small.TButton",
                   command=self.open_logs_folder).pack(side="right", padx=(0, 6))
        box = tk.Frame(card, bg=CONSOLE_BG)
        box.pack(fill="both", expand=True, padx=1, pady=(0, 1))
        self.out = tk.Text(box, height=4, wrap="word", font=(MONO, 9), bg=CONSOLE_BG, fg=CONSOLE_FG,
                           insertbackground=CONSOLE_FG, relief="flat", borderwidth=0, padx=12, pady=8,
                           selectbackground=ACCENT_LO, highlightthickness=0)
        scroll = ttk.Scrollbar(box, orient="vertical", command=self.out.yview, style="Dark.Vertical.TScrollbar")
        self.out.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.out.pack(side="left", fill="both", expand=True)
        self.out.tag_configure("ok", foreground=OK)
        self.out.tag_configure("bad", foreground=ERR)
        self.out.tag_configure("warn", foreground=WARN)
        self.out.tag_configure("cmd", foreground=ACCENT_HI)
        self.out.configure(state="disabled")

    # --- registro ---------------------------------------------------------

    def _insert(self, text):
        for line in text.splitlines(keepends=True):
            body = line.split("] ", 1)[1] if line.startswith("[") and "] " in line[:12] else line
            tag = ()
            if body.startswith("$ "):
                tag = ("cmd",)
            elif body.startswith(("FATTO", "DONE", "SSH OK", "Root SSH OK", "Installer verificato",
                                  "Installazione completata")):
                tag = ("ok",)
            elif body.startswith(("NON RIUSCITO", "FAILED", "ERRORE", "ERROR", "FERMO", "STOPPED",
                                  "Installazione dello Store non riuscita", "Connessione SSH non riuscita",
                                  "Rimozione dello Store")):
                tag = ("bad",)
            elif body.startswith(("ATTENZIONE", "WARNING", "NOTA", "NOTE")):
                tag = ("warn",)
            self.out.insert("end", line, tag)

    def _append(self, text):
        self._history.append(text)
        self.out.configure(state="normal")
        self._insert(self.i18n.log(text, keep_original=False))
        self.out.see("end")
        self.out.configure(state="disabled")

    def clear_log(self):
        self._history = []
        self.out.configure(state="normal")
        self.out.delete("1.0", "end")
        self.out.configure(state="disabled")

    def open_logs_folder(self):
        self._open_folder(os.path.join(HERE, "data", "logs"))

    def _open_folder(self, folder):
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError:
            pass
        try:
            if sys.platform == "win32":
                os.startfile(folder)  # noqa: S606 - apre Esplora file
            elif sys.platform == "darwin":
                import subprocess
                subprocess.Popen(["open", folder])
            else:
                import subprocess
                subprocess.Popen(["xdg-open", folder])
        except Exception as e:  # noqa: BLE001
            messagebox.showinfo("Cartella", "La cartella è:\n%s\n\n(%s)" % (folder, e))

    def _drain(self):
        try:
            while True:
                self._append(self.q.get_nowait())
        except queue.Empty:
            pass
        try:
            while True:
                self.calls.get_nowait()()
        except queue.Empty:
            pass
        self._drain_id = self.root.after(100, self._drain)

    def _on_destroy(self, event):
        if event.widget is self.root and self._drain_id is not None:
            try:
                self.root.after_cancel(self._drain_id)
            except tk.TclError:
                pass
            self._drain_id = None

    def _later(self, callback):
        """From a worker thread: run *callback* on the Tk thread (Tk is not
        thread-safe, so workers never touch widgets themselves)."""
        self.calls.put(callback)

    # --- esecuzione -------------------------------------------------------

    def _action_buttons(self):
        return [self.btn_check, self.detect_btn, self.btn_flash, self.btn_enable, self.btn_fix,
                self.btn_backup, self.btn_restore, self.btn_disable, self.btn_store, self.btn_versions, self.btn_restart,
                self.btn_remove, self.btn_github, self.btn_update]

    def _busy(self, on, text=None):
        self._is_busy = on
        state = "disabled" if on else "normal"
        for b in self._action_buttons():
            b.configure(state=state)
        if on:
            self.status.configure(text=text or "Operazione in corso: non scollegare il cavo e non spegnere l'auto…",
                                  fg=WARN)
            self.progress.configure(mode="indeterminate")
            self.progress.start(12)
        else:
            self.progress.stop()
            self.progress.configure(mode="determinate", value=0)
            self.status.configure(text="Pronto.", fg=MUTED)
            self._refresh()

    def _common_args(self):
        args = ["--yes"]
        if self.vin.get().strip():
            args += ["--vin", self.vin.get().strip()]
        if self.gateway.get().strip():
            args += ["--gateway", self.gateway.get().strip()]
        if self.mgu_ip.get().strip():
            args += ["--mgu-ip", self.mgu_ip.get().strip()]
        if self.arch.get() in ARCHES:
            args += ["--arch", self.arch.get()]
        return args

    def _on_variant(self, label):
        """Dalla voce del menu al codice che vuole la riga di comando."""
        self.arch.set(_VARIANT_BY_LABEL.get(label, ""))

    def _require_variant(self):
        """True se la variante della head unit è scelta; altrimenti avvisa.
        Flashare la variante sbagliata blocca l'unità in programmazione (#47)."""
        if self.arch.get() in ARCHES:
            return True
        messagebox.showerror(
            "Scegli il tipo di MGU",
            "Prima del flash scegli il tipo di MGU (ID7, ID8 BDC3 o ID8 BCP) in “Connessione vettura”.\n\n"
            "Non c'è un valore predefinito apposta: flashare la variante sbagliata scrive in parte "
            "l'immagine sbagliata e può lasciare l'unità bloccata in modalità di programmazione.")
        return False

    def _require_vin(self):
        if self.vin.get().strip():
            return True
        messagebox.showerror("VIN obbligatorio",
                             "Il flash scrive il VIN sull'unità, quindi è obbligatorio.\n"
                             "Premi prima “Connetti vettura” o “Leggi solo VIN”.")
        return False

    def _run(self, argv, label, on_done=None, text=None):
        """Esegue ssh_enable.main(argv) in un thread; *on_done(rc)* gira nel
        thread di Tk quando ha finito."""
        if self._is_busy:
            return
        self._busy(True, text)
        self._append("\n$ ssh_enable %s\n" % " ".join(argv))

        def task():
            saved_out, saved_err = sys.stdout, sys.stderr
            sys.stdout = sys.stderr = _QueueWriter(self.q)
            rc = 1
            try:
                rc = ssh_enable.main(argv)
            except SystemExit as e:
                self.q.put("\n%s\n" % e)
                rc = e.code if isinstance(e.code, int) else 1
            except Exception as e:  # noqa: BLE001 - tutto finisce nel registro
                self.q.put("\nERROR: %s\n" % e)
            finally:
                sys.stdout.flush()
                sys.stdout, sys.stderr = saved_out, saved_err
                self.q.put("\n[%s finished, exit %s]\n" % (label, rc))
                self._later(lambda: self._finished(rc, on_done))

        self.worker = threading.Thread(target=task, daemon=True)
        self.worker.start()

    def _finished(self, rc, on_done):
        self._busy(False)
        if on_done:
            on_done(rc)

    # --- stato ------------------------------------------------------------

    def _title(self, row):
        titles = row.get("titles") or {}
        return titles.get(LANGUAGE) or titles.get("en") or row.get("id")

    def _refresh(self):
        """Riempie i riquadri, la tabella e i chip dallo stato dell'ultimo controllo."""
        s = self.state or {}
        car, root, store, catalog = s.get("car"), s.get("root"), s.get("store"), s.get("catalog")

        if not car:
            self._set_chip(self.card_car, "NON COLLEGATA", MUTED)
            self.link.configure(text="Vettura non collegata", fg=MUTED)
        elif car.get("error"):
            self._set_chip(self.card_car, "NON TROVATA", ERR)
            self.link.configure(text="Vettura non trovata", fg=ERR)
        else:
            via = "HOTSPOT" if car.get("via") == "hotspot" else "ENET"
            self._set_chip(self.card_car, "COLLEGATA · %s" % via, OK)
            self.link.configure(text="%s · MGU %s" % (via, car.get("mgu_ip") or "?"), fg=OK)
        self.car_serial.configure(text=(car or {}).get("serial") or "Non disponibile")
        hardware = ((car or {}).get("hardware") or "Non disponibile").split(",")[0]
        self.car_hardware.configure(text=hardware[5:] if hardware.startswith("HWEL ") else hardware)

        if not root:
            self._set_chip(self.card_ssh, "DA VERIFICARE", MUTED)
        elif root.get("active"):
            self._set_chip(self.card_ssh, "ROOT ATTIVO", OK)
        elif root.get("reason") in ("login-refused", "key-unreadable"):
            self._set_chip(self.card_ssh, "CHIAVE RIFIUTATA", ERR)
        else:
            self._set_chip(self.card_ssh, "NON ATTIVO", WARN)

        backups = self._backups()
        if backups:
            b = backups[0]
            self.coding_info.configure(text="backup del %s UTC, %d blocchi%s" % (
                b["taken"], b["blocks"], "" if b["verified"] else " (NON verificato)"),
                fg=TEXT if b["verified"] else WARN)
        else:
            self.coding_info.configure(text="nessun backup per questo VIN", fg=MUTED)
        self.tree.delete(*self.tree.get_children())
        rows = (store or {}).get("rows") or (catalog or {}).get("rows") or []
        for row in rows:
            state = row.get("state")
            text = ROW_TEXT[state][0] if state in ROW_TEXT else "Da confrontare con la MGU"
            self.tree.insert("", "end", values=(self._title(row), row.get("installed") or "—",
                                                row.get("latest") or "—", text),
                             tags=(state,) if state in ROW_TEXT else ())
        latest = next((r.get("latest") for r in rows if r.get("id") == "robertn_store"), None)
        offline = catalog is not None and not catalog.get("online")
        suffix = "  (versioni dell'ultimo controllo, senza internet)" if offline else ""
        summary = (store or {}).get("summary") or {}
        pending = summary.get("updates", 0) + summary.get("missing", 0)
        if store and store.get("error"):
            self._set_chip(self.card_store, "VERIFICA NON RIUSCITA", ERR)
            line, colour = "Lettura delle versioni installate non riuscita: vedi il registro.", ERR
        elif store and not summary.get("installed"):
            self._set_chip(self.card_store, "NON INSTALLATO", ACCENT_HI)
            line, colour = "Lo Store non è installato su questa MGU." + suffix, TEXT
        elif store and pending:
            self._set_chip(self.card_store, "%d AGGIORNAMENT%s" % (pending, "O" if pending == 1 else "I"), WARN)
            line, colour = ("Store %s installato: %s." % (summary.get("store") or "?",
                                                          "1 aggiornamento disponibile" if pending == 1
                                                          else "%d aggiornamenti disponibili" % pending)
                            + suffix), WARN
        elif store:
            self._set_chip(self.card_store, "AGGIORNATO", OK)
            line, colour = "Store %s installato: è tutto aggiornato." % (summary.get("store") or "?") + suffix, OK
        else:
            self._set_chip(self.card_store, "DA VERIFICARE", MUTED)
            if self._store_loading:
                line, colour = "Lettura delle ultime versioni di mgu-store da GitHub…", MUTED
            elif self._store_error:
                line, colour = ("Versioni da GitHub non lette (%s). Premi “Controlla aggiornamenti GitHub” per "
                                "riprovare." % self._store_error), WARN
            elif latest:
                line, colour = ("Ultimo Store pubblicato: %s. Collega la vettura per confrontarlo con la MGU."
                                % latest + suffix), TEXT
            else:
                line, colour = "Premi “Connetti vettura” per leggere le versioni.", MUTED
        self.store_line.configure(text=line, fg=colour)

    def _load_state(self):
        self.state = store_mod.read_state()
        car = (self.state or {}).get("car") or {}
        if car.get("vin") and not self.vin.get().strip():
            self.vin.set(car["vin"])
        if car.get("mgu_ip"):
            self.mgu_ip.set(car["mgu_ip"])
        if car.get("via") == "hotspot":
            self.gateway.set("hotspot")
        self._refresh()

    # --- azioni -----------------------------------------------------------

    def _persist_args(self):
        args = ["--persist"]
        if not self.disable_debug.get():
            args.append("--keep-debug")
        args.append("--unique-key" if self.unique_key.get() else "--no-unique-key")
        return args

    def do_check(self):
        """Legge vettura, root SSH e versioni dello Store (non modifica nulla)."""
        try:
            os.remove(store_mod.STATE_FILE)
        except OSError:
            pass
        self._run(["check"] + self._common_args(), "check", on_done=self._check_done,
                  text="Lettura della vettura e delle versioni in corso…")

    def _check_done(self, rc):
        self._load_state()

    def do_enable(self):
        self._run(["enable"] + self._persist_args() + self._common_args(), "enable",
                  on_done=lambda rc: self.do_check())

    def do_flash(self):
        if not self._require_variant() or not self._require_vin():
            return
        if not messagebox.askyesno(
                "Prima predisposizione MGU",
                "La head unit entra in modalità di programmazione e si riavvia.\n\n"
                "Sull'unità verrà scritto il VIN %s.\n\n"
                "Il flash cancella la codifica (CAFD), alcune impostazioni e gli abbinamenti Digital Key. "
                "Hai fatto il backup della codifica?\n\nContinuare?" % self.vin.get().strip(),
                icon="warning"):
            return
        if not self._has_verified_backup():
            answer = messagebox.askyesnocancel(
                "Backup della codifica",
                "Per il VIN %s non c'è ancora un backup verificato della codifica.\n\n"
                "Sì = fai prima il backup (consigliato), poi il flash.\n"
                "No = flash senza backup.\nAnnulla = non fare nulla." % self.vin.get().strip(), icon="warning")
            if answer is None:
                return
            if answer:
                self._run(["preflight"] + self._common_args(), "preflight",
                          on_done=lambda rc: self._flash_after_backup(rc),
                          text="Backup della codifica in corso (sola lettura)…")
                return
        self._start_flash()

    def _flash_after_backup(self, rc):
        if rc == 0 and self._has_verified_backup():
            self._start_flash()
        else:
            messagebox.showerror("Backup codifica", "Il backup non è riuscito o non è verificato: il flash NON è "
                                 "partito. Il motivo è nel registro.")

    def _start_flash(self):
        self._run(["flash"] + self._persist_args() + self._common_args(), "flash", on_done=self._flash_done,
                  text="Flash in corso: non scollegare il PC e non avviare altre operazioni…")

    def _flash_done(self, rc):
        """Dopo un flash riuscito la codifica è cancellata: dire cosa viene prima dello Store."""
        if rc == 0 and self._has_verified_backup():
            if messagebox.askyesno(
                    "Accesso SSH completato",
                    "Root SSH attivo. Il flash ha cancellato la codifica della head unit.\n\n"
                    "Ripristinarla adesso dal backup (sperimentale)?\n\n"
                    "No = la ripristini tu con BimmerCode o E-Sys."):
                self._run(["coding-restore"] + self._common_args(), "coding-restore", on_done=self._restore_done,
                          text="Ripristino della codifica in corso: non scollegare il cavo e non spegnere l'auto…")
                return
        elif rc == 0:
            messagebox.showinfo(
                "Accesso SSH completato",
                "Root SSH attivo.\n\nIl flash ha cancellato la codifica della head unit: ripristinala adesso con "
                "BimmerCode o E-Sys e riavvia la head unit.\n\n"
                "Quando la MGU è di nuovo accesa, premi “Installa / Aggiorna”.")
        self.do_check()

    def do_fix_key(self):
        if not self._require_variant() or not self._require_vin():
            return
        if not messagebox.askyesno(
                "Correggere la chiave SSH rifiutata?",
                "Per le unità in cui flash e registrazione riescono e la porta 22 si apre, ma l'accesso root "
                "viene rifiutato perché la loro chiave originale è diversa.\n\n"
                "Rifà il flash dell'immagine di debug con la nostra chiave nell'unica regione non firmata. "
                "Nel caso peggiore la chiave non resta, nessun blocco. La head unit si riavvia e, come ogni "
                "flash, la codifica CAFD si cancella: ripristinala dal backup.\n\n"
                "Sull'unità verrà scritto il VIN %s.\n\nContinuare?" % self.vin.get().strip(),
                icon="warning"):
            return
        self._run(["fix-key"] + self._persist_args() + self._common_args(), "fix-key",
                  on_done=lambda rc: self.do_check())

    def _backups(self):
        vin = self.vin.get().strip()
        if not vin:
            return []
        return coding_mod.find_backups(os.path.join(HERE, "data", "preflight"), vin)

    def _has_verified_backup(self):
        return any(b["verified"] for b in self._backups())

    def do_restore(self):
        if not self._require_vin():
            return
        if not self._has_verified_backup():
            messagebox.showerror("Ripristino codifica",
                                 "Non c'è un backup verificato della codifica per il VIN %s.\n\n"
                                 "Il backup va fatto PRIMA del flash con “Backup codifica”." % self.vin.get().strip())
            return
        if not messagebox.askyesno(
                "Ripristino codifica (sperimentale)",
                "Riscrive sulla MGU la codifica dell'ultimo backup di questo VIN, blocco per blocco, rileggendo "
                "ogni blocco. Si ferma al primo rifiuto della MGU e alla fine la riavvia.\n\n"
                "È SPERIMENTALE: tieni a portata BimmerCode o E-Sys. Nel caso peggiore la MGU resta senza "
                "codifica, come dopo il flash.\n\nContinuare?", icon="warning"):
            return
        self._run(["coding-restore"] + self._common_args(), "coding-restore", on_done=self._restore_done,
                  text="Ripristino della codifica in corso: non scollegare il cavo e non spegnere l'auto…")

    def _restore_done(self, rc):
        if rc == 0:
            messagebox.showinfo("Ripristino codifica",
                                "Codifica ripristinata: la MGU riporta di nuovo la sua CAFD.\n\n"
                                "Quando la MGU è riaccesa controlla lingua, retrocamera e menu, poi premi "
                                "“Installa / Aggiorna”.")
        elif rc == ssh_enable.EXIT_CODING_PARTIAL:
            messagebox.showwarning("Ripristino codifica",
                                   "I blocchi sono stati scritti, ma la MGU non riporta ancora la sua CAFD.\n\n"
                                   "Completa con E-Sys (o BimmerCode) e conserva il registro in data\\logs: "
                                   "serve a migliorare il ripristino.")
        else:
            messagebox.showerror("Ripristino codifica",
                                 "Il ripristino si è fermato: il motivo è nel registro.\n\n"
                                 "Ripristina con BimmerCode o E-Sys e conserva il registro in data\\logs.")
        self.do_check()

    def do_backup(self):
        self._run(["preflight"] + self._common_args(), "preflight", on_done=self._backup_done,
                  text="Backup della codifica in corso (sola lettura)…")

    def _backup_done(self, rc):
        folder = os.path.join(HERE, "data", "preflight")
        if rc == 0:
            messagebox.showinfo("Backup codifica",
                                "Backup della codifica salvato e verificato in:\n%s\n\n"
                                "Copialo anche fuori da questo PC prima del flash." % folder)
            self._open_folder(folder)
        else:
            messagebox.showwarning("Backup codifica",
                                   "Il backup non è completo o non è verificato: non farci affidamento come "
                                   "unico backup. Il motivo è nel registro.")

    def do_disable(self):
        if not messagebox.askyesno("Disattivare SSH?",
                                   "Riportare SSH allo stato originale e spegnere la modalità debug sulla "
                                   "head unit?\n\nLo Store installato resta, ma senza SSH non si aggiorna da qui."):
            return
        self._run(["disable"] + self._common_args(), "disable", on_done=lambda rc: self.do_check())

    def do_store(self):
        if not messagebox.askyesno(
                "Installare / aggiornare lo Store?",
                "Installa mgu-store sulla head unit tramite root SSH, o lo aggiorna all'ultima versione.\n\n"
                "Tieni l'auto sveglia e con alimentazione stabile (motore acceso o caricabatterie) fino alla "
                "fine, e questo PC collegato a internet.\n\nContinuare?"):
            return
        self._run(["store"] + self._common_args(), "store", on_done=self._store_done,
                  text="Installazione dello Store in corso…")

    def _ask_restart(self, message):
        if messagebox.askyesno("Riavviare la MGU?", message):
            self._run(["store-restart"] + self._common_args(), "store-restart",
                      text="Riavvio della MGU in corso…")
            return True
        return False

    def _store_done(self, rc):
        if rc == store_mod.EXIT_RESTART_NEEDED:
            if self._ask_restart("Lo Store è installato.\n\nRiavviare ora la MGU per rendere effettiva la modifica?"):
                return
        elif rc == store_mod.EXIT_DONE:
            messagebox.showinfo("Store installato", "Lo Store è installato: la sua icona è tra le app dell'auto.")
        elif rc == store_mod.EXIT_REFUSED:
            messagebox.showwarning(
                "Store non installato",
                "L'installer si è fermato prima di cambiare qualsiasi cosa.\n\n"
                "Se il registro dice che manca la codifica (normale dopo un flash): ripristinala con BimmerCode "
                "o E-Sys, riavvia la head unit, poi premi di nuovo “Installa / Aggiorna”.")
        else:
            messagebox.showerror("Store non installato", "L'installazione non è terminata. Il motivo è nel registro.")
        self.do_check()

    def do_remove(self):
        if not messagebox.askyesno(
                "Rimuovere lo Store?",
                "Toglie dalla head unit tutto quello che l'installazione dello Store ha messo: app, strumenti, "
                "sorgenti e token.\n\nL'accesso SSH resta.\n\nContinuare?", icon="warning"):
            return
        self._run(["store-remove"] + self._common_args(), "store-remove", on_done=self._remove_done,
                  text="Rimozione dello Store in corso…")

    def _remove_done(self, rc):
        if rc == store_mod.EXIT_RESTART_NEEDED:
            if self._ask_restart("Lo Store è stato rimosso.\n\nRiavviare ora la MGU per completare la rimozione?"):
                return
        elif rc == store_mod.EXIT_DONE:
            messagebox.showinfo("Store rimosso", "Lo Store è stato rimosso dalla head unit.")
        else:
            messagebox.showerror("Rimozione non completata", "La rimozione non è terminata. Il motivo è nel registro.")
        self.do_check()

    def do_restart(self):
        if not messagebox.askyesno("Riavviare la MGU?", "Riavviare adesso la head unit? Torna attiva dopo circa "
                                   "un minuto."):
            return
        self._run(["store-restart"] + self._common_args(), "store-restart", text="Riavvio della MGU in corso…")

    def detect_vin(self):
        if self._is_busy:
            return
        self._busy(True, "Ricerca del gateway ENET…")
        self._append("\nScanning for an ENET gateway (UDP 6811)…\n")

        def task():
            try:
                gws = discovery.discover(log=lambda *a, **k: self.q.put(" ".join(str(x) for x in a) + "\n"))
            except Exception as e:  # noqa: BLE001
                gws = []
                self.q.put("Discovery failed: %s\n" % e)

            def apply():
                if gws:
                    g = gws[0]
                    if g.vin:
                        self.vin.set(g.vin)
                    self.gateway.set(g.ip)
                    self.q.put("Found gateway %s | VIN: %s\n" % (g.ip, g.vin or "not reported"))
                    if len(gws) > 1:
                        self.q.put("(%d gateways responded; using the first)\n" % len(gws))
                else:
                    self.q.put("No ENET gateway responded. Check the cable/adapter and that the car is awake.\n")
                self._busy(False)

            self._later(apply)

        self.worker = threading.Thread(target=task, daemon=True)
        self.worker.start()

    def do_store_latest(self, quiet=False):
        """Legge da GitHub le ultime versioni pubblicate di mgu-store (release
        "catalog") e le confronta con quelle lette dalla MGU all'ultimo
        collegamento. Non tocca l'auto: basta internet sul PC. *quiet*
        (all'avvio): solo tabella e registro, nessuna finestra."""
        if self._is_busy:
            return
        self._store_loading = True
        self._store_error = None
        self._busy(True, "Lettura delle ultime versioni di mgu-store da GitHub…")
        self._refresh()
        self._append("\n$ GitHub %s: catalog\n" % STORE_REPO)

        def log(message):
            self.q.put("[%s] %s\n" % (time.strftime("%H:%M:%S"), message))

        def task():
            try:
                catalog, online = store_mod.fetch_catalog(log)
                result = (store_mod.catalog_rows(catalog), online, catalog.get("commit"), None)
            except store_mod.StoreError as e:
                log("Could not read the latest Store versions: %s" % e)
                result = (None, False, None, str(e))
            self._later(lambda: self._store_latest_done(*result, quiet=quiet))

        self.worker = threading.Thread(target=task, daemon=True)
        self.worker.start()

    def _store_latest_done(self, rows, online, commit, error, quiet=False):
        self._store_loading = False
        self._store_error = error if rows is None else None
        self._busy(False)
        if rows is None:
            if quiet:
                return
            messagebox.showwarning("Aggiornamenti mgu-store",
                                   "Impossibile leggere le versioni da GitHub:\n%s" % error)
            return
        state = dict(self.state or {})
        state["catalog"] = {"online": online, "commit": commit, "rows": rows}
        store = state.get("store")
        if store and "rows" in store:
            installed = store.get("installed")
            compared = store_mod.compare(rows, installed)
            state["store"] = {"installed": installed, "rows": compared,
                              "summary": store_mod.summarize(compared, installed)}
        self.state = state
        self._refresh()
        latest = next((r["latest"] for r in rows if r["id"] == "robertn_store"), "?")
        self._append("[%s] Latest Store version: %s\n" % (time.strftime("%H:%M:%S"), latest))
        if quiet:
            return
        offline = "" if online else "\n\n(Senza internet: sono le versioni dell'ultimo controllo.)"
        summary = (state.get("store") or {}).get("summary")
        if summary and summary.get("installed"):
            pending = summary.get("updates", 0) + summary.get("missing", 0)
            if pending:
                messagebox.showinfo(
                    "Aggiornamenti mgu-store",
                    "Ultima versione dello Store: %s (sulla MGU: %s).\n\nComponenti da aggiornare o installare: %d.\n"
                    "Collega la vettura e premi “Installa / Aggiorna”.%s"
                    % (latest, summary.get("store") or "?", pending, offline))
            else:
                messagebox.showinfo("Aggiornamenti mgu-store",
                                    "La MGU ha già l'ultima versione dello Store (%s).%s" % (latest, offline))
        else:
            messagebox.showinfo("Aggiornamenti mgu-store",
                                "Ultima versione pubblicata dello Store: %s.\n\nPremi “Connetti vettura” per "
                                "confrontarla con quella sulla MGU.%s" % (latest, offline))

    def do_program_update(self):
        """Confronta questa versione di MGU Plus con l'ultima pubblicata su GitHub."""
        if self._is_busy:
            return
        self._busy(True, "Controllo degli aggiornamenti di MGU Plus…")

        def task():
            result = check_program_update()
            self._later(lambda: self._program_update_done(result))

        self.worker = threading.Thread(target=task, daemon=True)
        self.worker.start()

    def _program_update_done(self, result):
        self._busy(False)
        kind, version, url, detail = result
        if kind == "newer":
            self._append("MGU Plus %s is available (this is %s).\n" % (version, APP_VERSION))
            if messagebox.askyesno("Aggiornamento disponibile",
                                   "È disponibile MGU Plus %s (questa è la %s).\n\nAprire la pagina di "
                                   "download?" % (version, APP_VERSION)):
                webbrowser.open(url)
        elif kind == "current":
            messagebox.showinfo("Nessun aggiornamento", "Hai già l'ultima versione di MGU Plus (%s)." % APP_VERSION)
        elif kind == "none":
            messagebox.showinfo("Nessun aggiornamento", "Su GitHub (%s) non è ancora pubblicata nessuna versione "
                                "definitiva di MGU Plus." % UPDATE_REPO)
        else:
            messagebox.showwarning("Controllo non riuscito", "Impossibile controllare gli aggiornamenti:\n%s"
                                   % detail)


def check_program_update(api=UPDATE_API, current=APP_VERSION):
    """("newer" | "current" | "none" | "error", versione, pagina, dettaglio) per
    l'ultima release definitiva (non pre-release) del repository di MGU Plus."""
    request = urllib.request.Request(api, headers={"User-Agent": "MGU-Plus",
                                                   "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return ("none", None, None, None)
        return ("error", None, None, "HTTP %d" % e.code)
    except (urllib.error.URLError, OSError, ValueError) as e:
        return ("error", None, None, str(getattr(e, "reason", e)))
    tag = str(data.get("tag_name") or "").strip()
    if not tag:
        return ("none", None, None, None)
    version = tag.lstrip("vV")
    url = data.get("html_url") or "https://github.com/%s/releases" % UPDATE_REPO
    if store_mod.parse_version(version) > store_mod.parse_version(current):
        return ("newer", version, url, None)
    return ("current", version, url, None)


def main():
    try:
        root = tk.Tk()
        App(root)
        root.mainloop()
    except Exception:
        # Un exe senza console sparirebbe e basta: lascia una traccia e, se
        # possibile, mostrala.
        import traceback
        tb = traceback.format_exc()
        try:
            with open(os.path.join(HERE, "mgu-plus-error.log"), "w", encoding="utf-8") as f:
                f.write(tb)
        except Exception:
            pass
        try:
            messagebox.showerror("MGU Plus non si è avviato", tb)
        except Exception:
            pass
        raise


if __name__ == "__main__":
    main()
