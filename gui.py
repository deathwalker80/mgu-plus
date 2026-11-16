"""MGU Plus - root SSH e App Store (mgu-store) per le head unit BMW MGU
(iDrive 7 / iDrive 8). Stessa impaginazione di NBT Companion: intestazione,
pastiglie di stato, elenco delle pagine a sinistra (Collegamento, Accesso SSH,
Codifica, App Store), pagina al centro, registro completo in basso.

Interfaccia Tkinter sopra ssh_enable.py: ogni azione esegue ssh_enable.main in
un thread e ne mostra il registro nella finestra, esattamente come da riga di
comando. "Connetti vettura" (azione `check`) scrive data/mgu-store/state.json,
che riempie le pagine e le pastiglie di stato: la vettura, se root SSH
risponde, e le versioni dello Store sulla head unit confrontate con le ultime
pubblicate.

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
from tkinter import font as tkfont
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
from widgets import (ACCENT, BACK, BAD, BODY, BOLD, BORDER, DIM, FIELD, GOOD, HEADING, MONO, PANEL, SMALL, TEXT,
                     WARN, WHITE, BusyBar, Card, Chip, Field, NavItem, RoundButton, entry)

APP_NAME = "MGU Plus"
APP_VERSION = "1.3.8"
AUTHOR = "DeathWalkeR"
WINDOW_TITLE = "%s %s by %s" % (APP_NAME, APP_VERSION, AUTHOR)
# Lingua dell'interfaccia (italiano o inglese, pulsante nell'intestazione
# come in NBT Companion), salvata in data/settings.json.
LANGUAGES = ("it", "en")
SETTINGS_FILE = os.path.join(HERE, "data", "settings.json")
_language = ["it"]


def T(it, en):
    """Il testo nella lingua scelta (come L.T di NBT Companion)."""
    return en if _language[0] == "en" else it


def load_language(path=None):
    try:
        with open(path or SETTINGS_FILE, encoding="utf-8") as f:
            value = json.load(f).get("language")
    except (OSError, ValueError, AttributeError):
        return "it"
    return value if value in LANGUAGES else "it"


def save_language(language, path=None):
    path = path or SETTINGS_FILE
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        settings = {}
        try:
            with open(path, encoding="utf-8") as f:
                settings = json.load(f)
        except (OSError, ValueError):
            settings = {}
        if not isinstance(settings, dict):
            settings = {}
        settings["language"] = language
        with open(path, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
    except OSError:
        pass  # la lingua resta quella scelta fino alla chiusura
# Dove mgu-store pubblica le sue versioni (release "catalog" su GitHub).
STORE_REPO = "robertnisipeanu/mgu-store"
# Dove si pubblicano le versioni di MGU Plus (GitHub releases del fork).
UPDATE_REPO = "deathwalker80/mgu-plus"
UPDATE_API = "https://api.github.com/repos/%s/releases?per_page=30" % UPDATE_REPO

ARCHES = (ID7, ID8_BDC3, ID8_BCP)
# The flash contract MUST match the head unit. Flashing an ID8 with the ID7
# image (or vice-versa) partially writes the wrong regions and leaves the unit
# stuck in programming mode (issue #47). The software baseline alone (030 vs
# 036) is not a reliable enough discriminator to auto-pick, so we make the user
# choose their exact variant - there is no silent default.
VARIANT_PROMPT = "— scegli —"
VARIANT_PROMPT_EN = "— choose —"
VARIANT_CHOICES = (
    (ID7, "ID7  (software 030.xxx)"),
    (ID8_BDC3, "ID8 BDC3  (036.xxx, sperimentale)"),
    (ID8_BCP, "ID8 BCP  (036.xxx, sperimentale)"),
)
VARIANT_CHOICES_EN = (
    (ID7, "ID7  (software 030.xxx)"),
    (ID8_BDC3, "ID8 BDC3  (036.xxx, experimental)"),
    (ID8_BCP, "ID8 BCP  (036.xxx, experimental)"),
)
_VARIANT_BY_LABEL = {label: code for code, label in VARIANT_CHOICES + VARIANT_CHOICES_EN}


def variant_choices():
    return VARIANT_CHOICES_EN if _language[0] == "en" else VARIANT_CHOICES


def variant_prompt():
    return VARIANT_PROMPT_EN if _language[0] == "en" else VARIANT_PROMPT


# Tavolozza e caratteri: quelli di NBT Companion, in widgets.py.

# Stato delle righe della tabella (mgu7/store.py compare()) -> testo, colore.
ROW_TEXT = {
    store_mod.ROW_CURRENT: ("Aggiornato", "Up to date", GOOD),
    store_mod.ROW_UPDATE: ("Aggiornamento disponibile", "Update available", WARN),
    store_mod.ROW_MISSING: ("Non installato", "Not installed", ACCENT),
    store_mod.ROW_NEWER: ("Più recente di quella pubblicata", "Newer than the published one", DIM),
    store_mod.ROW_OTHER: ("Installato (altra fonte)", "Installed (other source)", DIM),
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
        _language[0] = load_language()
        self.i18n = Translator()
        self.i18n.select(_language[0])
        self._history = []
        self._is_busy = False
        self.state = None
        self._store_loading = False
        self._store_error = None

        root.title(WINDOW_TITLE)
        root.configure(bg=BACK)
        root.minsize(1100, 760)
        height = min(900, max(760, root.winfo_screenheight() - 80))
        root.geometry("1280x%d" % height)
        self._set_icon()

        self.vin = tk.StringVar()
        self.gateway = tk.StringVar(value="auto")
        self.mgu_ip = tk.StringVar()
        self.arch = tk.StringVar(value="")  # vuota finché l'utente non sceglie la variante
        self.variant_label = tk.StringVar(value=variant_prompt())
        self.disable_debug = tk.BooleanVar(value=True)
        self.unique_key = tk.BooleanVar(value=False)

        self._style()
        self.current_page = "car"
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
        st.configure(".", background=BACK, foreground=TEXT, font=BODY, bordercolor=BORDER,
                     lightcolor=BORDER, darkcolor=BORDER, troughcolor=PANEL, focuscolor=ACCENT,
                     selectbackground=ACCENT, selectforeground=WHITE, insertcolor=TEXT)

        st.configure("TCombobox", fieldbackground=FIELD, background=FIELD, foreground=TEXT,
                     arrowcolor=TEXT, bordercolor=BORDER, lightcolor=FIELD, darkcolor=FIELD, padding=(8, 6),
                     arrowsize=13)
        st.map("TCombobox",
               fieldbackground=[("readonly", FIELD)], background=[("readonly", FIELD), ("active", BORDER)],
               foreground=[("readonly", TEXT)], selectbackground=[("readonly", FIELD)],
               selectforeground=[("readonly", TEXT)], bordercolor=[("focus", ACCENT)],
               lightcolor=[("focus", FIELD)], arrowcolor=[("disabled", DIM)])
        self.root.option_add("*TCombobox*Listbox.background", FIELD)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.root.option_add("*TCombobox*Listbox.selectForeground", WHITE)
        self.root.option_add("*TCombobox*Listbox.font", BODY)
        self.root.option_add("*TCombobox*Listbox.borderWidth", 0)

        st.configure("Dark.TCheckbutton", background=PANEL, foreground=TEXT, font=BODY,
                     indicatorbackground=FIELD, indicatorforeground=WHITE, indicatorrelief="flat",
                     indicatormargin=(0, 0, 8, 0), focuscolor=PANEL, bordercolor=BORDER, padding=(0, 4))
        st.map("Dark.TCheckbutton", background=[("active", PANEL)],
               indicatorbackground=[("selected", ACCENT), ("active", BORDER)])

        # Solo il cursore in una guida scura: niente frecce, niente aspetto di serie.
        for orient in ("Vertical", "Horizontal"):
            name = "Dark.%s.TScrollbar" % orient
            st.layout(name, [("%s.Scrollbar.trough" % orient, {
                "children": [("%s.Scrollbar.thumb" % orient, {"expand": "1", "sticky": "nswe"})],
                "sticky": "ns" if orient == "Vertical" else "we"})])
            st.configure(name, background=BORDER, troughcolor=FIELD, bordercolor=FIELD,
                         lightcolor=BORDER, darkcolor=BORDER, gripcount=0, arrowsize=9)
            st.map(name, background=[("active", DIM)])

        # Tabella come la ListView di NBT Companion: intestazione sul fondo del
        # riquadro, righe sul fondo dei campi, selezione blu.
        st.configure("Dark.Treeview", background=FIELD, fieldbackground=FIELD, foreground=TEXT,
                     rowheight=26, bordercolor=FIELD, lightcolor=FIELD, darkcolor=FIELD, font=BODY)
        st.map("Dark.Treeview", background=[("selected", ACCENT)], foreground=[("selected", WHITE)])
        st.configure("Dark.Treeview.Heading", background=PANEL, foreground=DIM, relief="flat",
                     bordercolor=BORDER, lightcolor=PANEL, darkcolor=PANEL, font=BOLD, padding=(6, 6))
        st.map("Dark.Treeview.Heading", background=[("active", PANEL)])
        st.layout("Dark.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])

    # --- impaginazione ----------------------------------------------------

    def _build(self):
        root = self.root
        root.columnconfigure(0, minsize=262, weight=0)
        root.columnconfigure(1, weight=1)
        for row, size in ((0, 64), (1, 3), (2, 46), (4, 200)):
            root.rowconfigure(row, minsize=size, weight=0)
        root.rowconfigure(3, weight=1)

        self._build_header()
        self.busy_bar = BusyBar(root)
        self.busy_bar.grid(row=1, column=0, columnspan=2, sticky="nsew")
        self._build_chips()

        nav = tk.Frame(root, bg=PANEL)
        nav.grid(row=3, column=0, sticky="nsew")
        self.host = tk.Frame(root, bg=BACK)
        self.host.grid(row=3, column=1, sticky="nsew", padx=20, pady=(6, 8))
        self.host.rowconfigure(0, weight=1)
        self.host.columnconfigure(0, weight=1)
        self.pages = {}
        self.nav_items = {}
        self.card_car = self._build_car_page()
        self.card_ssh = self._build_ssh_page()
        self.card_coding = self._build_coding_page()
        self.card_store = self._build_store_page()
        for key, number, title in (("car", 1, T("Collegamento", "Connection")),
                                   ("ssh", 2, T("Accesso SSH", "SSH access")),
                                   ("coding", 3, T("Codifica", "Coding")),
                                   ("store", 4, "App Store")):
            item = NavItem(nav, number, title, lambda k=key: self.show_page(k))
            item.pack(padx=10, pady=(12 if number == 1 else 0, 4), anchor="w")
            self.nav_items[key] = item
        self.card_car.chip = self.chip_car
        self.card_ssh.chip = self.chip_ssh
        self.card_coding.chip = self.chip_coding
        self.card_store.chip = self.chip_store
        self.show_page(self.current_page)

        self._build_log()

    def show_page(self, key):
        self.current_page = key
        self.pages[key].tkraise()
        for name, item in self.nav_items.items():
            item.selected = name == key

    def toggle_language(self):
        """Italiano <-> inglese, come in NBT Companion: rifà la finestra nella
        nuova lingua, tenendo campi, pagina aperta e log."""
        if self._is_busy:
            return
        language = "it" if _language[0] == "en" else "en"
        _language[0] = language
        self.i18n.select(language)
        save_language(language)
        code = self.arch.get()
        for widget in self.root.winfo_children():
            widget.destroy()
        label = next((lbl for c, lbl in variant_choices() if c == code), None)
        self.variant_label.set(label or variant_prompt())
        self._build()
        self._refresh()
        history, self._history = self._history, []
        for text in history:
            self._append(text)

    def _build_header(self):
        header = tk.Frame(self.root, bg=PANEL, height=64)
        header.grid(row=0, column=0, columnspan=2, sticky="nsew")
        header.grid_propagate(False)
        self._logo = None
        try:
            self._logo = tk.PhotoImage(file=os.path.join(RESOURCES, "mgu_plus_header.png"))
        except tk.TclError:
            pass
        left = 14
        if self._logo is not None:
            tk.Label(header, image=self._logo, bg=PANEL, borderwidth=0).place(x=14, y=9)
            left = 68
        title = tk.Label(header, text=APP_NAME, bg=PANEL, fg=TEXT, font=HEADING, borderwidth=0)
        title.place(x=left, y=6)
        version = tk.Label(header, text="%s  by %s" % (APP_VERSION, AUTHOR), bg=PANEL, fg=DIM, font=SMALL,
                           borderwidth=0)
        version.place(x=left + 2, y=37)
        widest = max(tkfont.Font(font=HEADING).measure(APP_NAME), tkfont.Font(font=SMALL).measure(
            "%s  by %s" % (APP_VERSION, AUTHOR)))
        tk.Label(header, text=T("BMW MGU iDrive 7 (iDrive 8 sperimentale) - root SSH e App Store via cavo ENET",
                                "BMW MGU iDrive 7 (iDrive 8 experimental) - root SSH and App Store over the ENET "
                                "cable"),
                 bg=PANEL, fg=DIM, font=SMALL, borderwidth=0).place(x=left + widest + 34, y=23)
        self.btn_language = RoundButton(header, "English" if _language[0] == "it" else "Italiano",
                                        self.toggle_language, min_width=100, height=34)
        self.btn_language.place(relx=1.0, x=-18, y=15, anchor="ne")
        self.btn_update = RoundButton(header, T("Aggiornamenti", "Updates"), self.do_program_update,
                                      min_width=130, height=34)
        self.btn_update.place(relx=1.0, x=-18 - int(self.btn_language.cget("width")) - 10, y=15, anchor="ne")

    def _build_chips(self):
        bar = tk.Frame(self.root, bg=BACK)
        bar.grid(row=2, column=0, columnspan=2, sticky="nsew")
        self.chip_car = Chip(bar)
        self.chip_ssh = Chip(bar)
        self.chip_coding = Chip(bar)
        self.chip_store = Chip(bar)
        self.chip_busy = Chip(bar, colour=ACCENT)
        for chip in (self.chip_car, self.chip_ssh, self.chip_coding, self.chip_store):
            chip.pack(side="left", padx=(16 if chip is self.chip_car else 0, 8), pady=(10, 0), anchor="n")

    def _page(self, key, title, description, actions):
        """Una pagina, sempre uguale: titolo, descrizione, contenuto nel
        riquadro, pulsanti in basso a sinistra (il principale per primo)."""
        page = tk.Frame(self.host, bg=BACK)
        page.grid(row=0, column=0, sticky="nsew")
        tk.Label(page, text=title, bg=BACK, fg=TEXT, font=HEADING, anchor="w").pack(fill="x", pady=(0, 2))
        tk.Label(page, text=description, bg=BACK, fg=DIM, font=BODY, anchor="w", justify="left",
                 wraplength=920).pack(fill="x", pady=(0, 10))
        bar = tk.Frame(page, bg=BACK, height=56)
        bar.pack(side="bottom", fill="x")
        bar.pack_propagate(False)
        card = Card(page)
        card.pack(fill="both", expand=True)
        buttons = []
        for name, text, command, kind in actions:
            button = RoundButton(bar, text, command, kind=kind)
            button.pack(side="left", padx=(0, 10), pady=(12, 0), anchor="n")
            setattr(self, name, button)
            buttons.append(button)
        page.body = card.body
        page.buttons = buttons
        self.pages[key] = page
        return page

    @staticmethod
    def _form_label(parent, text, row):
        tk.Label(parent, text=text, bg=PANEL, fg=DIM, font=BOLD, anchor="w").grid(
            row=row, column=0, sticky="w", padx=(0, 18), pady=(0, 8))

    def _value(self, parent):
        return tk.Label(parent, text="—", bg=PANEL, fg=TEXT, font=MONO, anchor="w")

    def _build_car_page(self):
        page = self._page("car", T("Collegamento", "Connection"),
                          T("Cavo ENET nella presa OBD, quadro in modalità diagnosi (3 pressioni rapide di Start/Stop). "
                            "Con l'hotspot Wi-Fi dell'auto solo le operazioni su SSH già attivo.",
                            "ENET cable in the OBD port, dashboard in diagnostic mode (press Start/Stop 3 times "
                            "quickly). Over the car's Wi-Fi hotspot only the operations on an already active SSH."),
                          (("btn_check", T("Connetti vettura", "Connect car"), self.do_check, "primary"),
                           ("detect_btn", T("Leggi solo VIN", "Read VIN only"), self.detect_vin, "secondary"),
                           ("btn_diag", T("Diagnostica completa", "Full diagnostics"), self.do_diagnose,
                            "secondary")))
        form = tk.Frame(page.body, bg=PANEL)
        form.pack(anchor="nw")
        self._form_label(form, "Gateway", 0)
        entry(form, self.gateway).grid(row=0, column=1, sticky="w", pady=(0, 8))
        self._form_label(form, "VIN", 1)
        entry(form, self.vin).grid(row=1, column=1, sticky="w", pady=(0, 8))
        self._form_label(form, T("IP MGU", "MGU IP"), 2)
        entry(form, self.mgu_ip).grid(row=2, column=1, sticky="w", pady=(0, 8))
        self._form_label(form, T("Tipo MGU", "MGU type"), 3)
        self.variant_box = ttk.Combobox(form, textvariable=self.variant_label, state="readonly", width=30,
                                        values=[label for _, label in variant_choices()], font=BODY)
        self.variant_box.bind("<<ComboboxSelected>>", lambda _e: self._on_variant(self.variant_label.get()))
        self.variant_box.grid(row=3, column=1, sticky="w", pady=(0, 8))
        self._form_label(form, T("Seriale MGU", "MGU serial"), 4)
        self.car_serial = self._value(form)
        self.car_serial.grid(row=4, column=1, sticky="w", pady=(0, 8))
        self._form_label(form, T("Hardware MGU", "MGU hardware"), 5)
        self.car_hardware = self._value(form)
        self.car_hardware.grid(row=5, column=1, sticky="w", pady=(0, 8))
        return page

    def _build_ssh_page(self):
        page = self._page("ssh", T("Accesso SSH MGU", "MGU SSH access"),
                          T("Flash dell'immagine di debug e accesso root via SSH alla MGU.",
                            "Flash of the debug image and root SSH access to the MGU."),
                          (("btn_flash", T("Flash + Apri SSH", "Flash + Open SSH"), self.do_flash, "primary"),
                           ("btn_enable", T("Apri SSH", "Open SSH"), self.do_enable, "secondary"),
                           ("btn_fix", T("Correggi chiave", "Fix key"), self.do_fix_key, "secondary"),
                           ("btn_disable", T("Disattiva SSH", "Disable SSH"), self.do_disable, "danger")))
        ttk.Checkbutton(page.body, text=T("SSH persistente con modalità debug spenta (consigliato)",
                                          "Persistent SSH with debug mode off (recommended)"),
                        variable=self.disable_debug, style="Dark.TCheckbutton").pack(anchor="w")
        ttk.Checkbutton(page.body, text=T("Chiave unica per l'auto (avanzato)", "Unique key for the car (advanced)"),
                        variable=self.unique_key, style="Dark.TCheckbutton").pack(anchor="w")
        tk.Label(page.body, text=T("Il flash cancella la codifica e gli abbinamenti Digital Key: prima il backup "
                                   "(il programma lo propone da solo), dopo il ripristino. Il ripristino è "
                                   "sperimentale: tieni a portata BimmerCode o E-Sys.",
                                   "The flash erases the coding and the Digital Key pairings: backup first (the "
                                   "program offers it by itself), restore afterwards. The restore is experimental: "
                                   "keep BimmerCode or E-Sys at hand."),
                 bg=PANEL, fg=WARN, font=BODY, anchor="w", justify="left", wraplength=880).pack(fill="x", pady=(12, 0))
        return page

    def _build_coding_page(self):
        page = self._page("coding", T("Codifica", "Coding"),
                          T("Backup della codifica della MGU prima del flash e ripristino dopo.",
                            "Backup of the MGU coding before the flash and restore afterwards."),
                          (("btn_backup", T("Backup codifica", "Back up coding"), self.do_backup, "primary"),
                           ("btn_restore", T("Ripristina (sperimentale)", "Restore (experimental)"), self.do_restore,
                            "secondary")))
        form = tk.Frame(page.body, bg=PANEL)
        form.pack(anchor="nw", fill="x")
        self._form_label(form, "Backup", 0)
        self.coding_info = tk.Label(form, text="", bg=PANEL, fg=DIM, font=BODY, anchor="w")
        self.coding_info.grid(row=0, column=1, sticky="w", pady=(0, 8))
        tk.Label(page.body, text=T("Il ripristino è sperimentale: tieni a portata BimmerCode o E-Sys.",
                                   "The restore is experimental: keep BimmerCode or E-Sys at hand."),
                 bg=PANEL, fg=WARN, font=BODY, anchor="w", justify="left",
                 wraplength=880).pack(fill="x", pady=(12, 0))
        return page

    def _build_store_page(self):
        page = self._page("store", T("Applicazioni MGU (App Store)", "MGU applications (App Store)"),
                          T("Con internet in auto lo Store si aggiorna anche da solo, dalla sua pagina Installed.",
                            "With internet in the car the Store also updates by itself, from its Installed page."),
                          (("btn_store", T("Installa / Aggiorna", "Install / Update"), self.do_store, "primary"),
                           ("btn_versions", T("Verifica app installate", "Check installed apps"), self.do_check,
                            "secondary"),
                           ("btn_github", T("Controlla aggiornamenti GitHub", "Check GitHub updates"),
                            self.do_store_latest, "secondary"),
                           ("btn_restart", T("Riavvia MGU", "Restart MGU"), self.do_restart, "secondary"),
                           ("btn_remove", T("Rimuovi Store", "Remove Store"), self.do_remove, "danger")))
        self.store_line = tk.Label(page.body, text="", bg=PANEL, fg=TEXT, font=BOLD, anchor="w", justify="left",
                                   wraplength=880)
        self.store_line.pack(fill="x", pady=(0, 8))
        table = Field(page.body, padding=(0, 0))
        table.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(table, columns=("name", "installed", "latest", "state"), show="headings",
                                 style="Dark.Treeview", selectmode="none", height=6)
        for column, title, width, anchor, stretch in (("name", T("Componente", "Component"), 260, "w", True),
                                                      ("installed", T("Installata", "Installed"), 110, "w", False),
                                                      ("latest", T("Ultima", "Latest"), 110, "w", False),
                                                      ("state", T("Stato", "State"), 240, "w", True)):
            self.tree.heading(column, text=title, anchor="w")
            self.tree.column(column, width=width, minwidth=60, anchor=anchor, stretch=stretch)
        tree_scroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview,
                                    style="Dark.Vertical.TScrollbar")
        self.tree.configure(yscrollcommand=tree_scroll.set)
        tree_scroll.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        for state, (_, _, colour) in ROW_TEXT.items():
            self.tree.tag_configure(state, foreground=colour)
        return page

    def _build_log(self):
        outer = tk.Frame(self.root, bg=BACK)
        outer.grid(row=4, column=0, columnspan=2, sticky="nsew", padx=16, pady=(6, 14))
        card = Card(outer, padding=(14, 8, 14, 12))
        card.pack(fill="both", expand=True)
        head = tk.Frame(card.body, bg=PANEL)
        head.pack(fill="x", pady=(0, 6))
        tk.Label(head, text=T("Log completo", "Complete log"), bg=PANEL, fg=DIM, font=BOLD).pack(side="left")
        self.btn_clear = RoundButton(head, T("Pulisci finestra", "Clear window"), self.clear_log)
        self.btn_clear.pack(side="right")
        RoundButton(head, T("Apri cartella dei log", "Open log folder"), self.open_logs_folder).pack(
            side="right", padx=(0, 10))
        box = Field(card.body, padding=(0, 0))
        box.configure(bg=BACK)
        box.pack(fill="both", expand=True)
        self.out = tk.Text(box, height=4, wrap="none", font=MONO, bg=BACK, fg=TEXT,
                           insertbackground=TEXT, relief="flat", borderwidth=0, padx=8, pady=6,
                           selectbackground=ACCENT, selectforeground=WHITE, highlightthickness=0)
        scroll = ttk.Scrollbar(box, orient="vertical", command=self.out.yview, style="Dark.Vertical.TScrollbar")
        hscroll = ttk.Scrollbar(box, orient="horizontal", command=self.out.xview,
                                style="Dark.Horizontal.TScrollbar")
        self.out.configure(yscrollcommand=scroll.set, xscrollcommand=hscroll.set)
        scroll.pack(side="right", fill="y")
        hscroll.pack(side="bottom", fill="x")
        self.out.pack(side="left", fill="both", expand=True)
        self.out.tag_configure("ok", foreground=GOOD)
        self.out.tag_configure("bad", foreground=BAD)
        self.out.tag_configure("warn", foreground=WARN)
        self.out.tag_configure("cmd", foreground=ACCENT)
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
            messagebox.showinfo(T("Cartella", "Folder"), T("La cartella è:\n%s\n\n(%s)",
                                                           "The folder is:\n%s\n\n(%s)") % (folder, e))

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
                self.btn_remove, self.btn_github, self.btn_update, self.btn_language, self.btn_diag]

    def _busy(self, on, text=None):
        self._is_busy = on
        state = "disabled" if on else "normal"
        for b in self._action_buttons():
            b.configure(state=state)
        self.busy_bar.running = on
        if on:
            self.chip_busy.configure(text=text or T("Operazione in corso: non scollegare il cavo e non spegnere "
                                                    "l'auto…", "Operation running: do not unplug the cable and do "
                                                    "not switch the car off…"))
            self.chip_busy.pack(side="left", padx=(0, 8), pady=(10, 0), anchor="n")
            self.root.configure(cursor="watch")
        else:
            self.chip_busy.pack_forget()
            self.root.configure(cursor="")
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
            T("Scegli il tipo di MGU", "Choose the MGU type"),
            T("Prima del flash scegli il tipo di MGU (ID7, ID8 BDC3 o ID8 BCP) in “Collegamento”.\n\n"
              "Non c'è un valore predefinito apposta: flashare la variante sbagliata scrive in parte "
              "l'immagine sbagliata e può lasciare l'unità bloccata in modalità di programmazione.",
              "Before the flash choose the MGU type (ID7, ID8 BDC3 or ID8 BCP) in “Connection”.\n\n"
              "There is no default on purpose: flashing the wrong variant partly writes the wrong image and "
              "can leave the unit stuck in programming mode."))
        return False

    def _require_vin(self):
        if self.vin.get().strip():
            return True
        messagebox.showerror(T("VIN obbligatorio", "VIN required"),
                             T("Il flash scrive il VIN sull'unità, quindi è obbligatorio.\n"
                               "Premi prima “Connetti vettura” o “Leggi solo VIN”.",
                               "The flash writes the VIN to the unit, so it is required.\n"
                               "Press “Connect car” or “Read VIN only” first."))
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
        return titles.get(_language[0]) or titles.get("en") or row.get("title") or row.get("id")

    def _refresh(self):
        """Riempie le pagine, la tabella e le pastiglie dallo stato dell'ultimo controllo."""
        s = self.state or {}
        car, root, store, catalog = s.get("car"), s.get("root"), s.get("store"), s.get("catalog")

        if not car:
            self.chip_car.configure(text=T("Vettura: non collegata", "Car: not connected"), fg=DIM)
        elif car.get("error"):
            self.chip_car.configure(text=T("Vettura: non trovata", "Car: not found"), fg=BAD)
        else:
            via = "hotspot" if car.get("via") == "hotspot" else "ENET"
            self.chip_car.configure(text=T("Vettura: collegata via %s, MGU %s", "Car: connected via %s, MGU %s")
                                    % (via, car.get("mgu_ip") or "?"), fg=GOOD)
        missing = T("Non disponibile", "Not available")
        self.car_serial.configure(text=(car or {}).get("serial") or missing)
        hardware = ((car or {}).get("hardware") or missing).split(",")[0]
        self.car_hardware.configure(text=hardware[5:] if hardware.startswith("HWEL ") else hardware)

        if not root:
            self.chip_ssh.configure(text=T("SSH: da verificare", "SSH: to be checked"), fg=DIM)
        elif root.get("active"):
            self.chip_ssh.configure(text=T("SSH: root attivo", "SSH: root active"), fg=GOOD)
        elif root.get("reason") in ("login-refused", "key-unreadable"):
            self.chip_ssh.configure(text=T("SSH: chiave rifiutata", "SSH: key refused"), fg=BAD)
        else:
            self.chip_ssh.configure(text=T("SSH: non attivo", "SSH: not active"), fg=WARN)

        backups = self._backups()
        if backups:
            b = backups[0]
            self.coding_info.configure(text=T("backup del %s UTC, %d blocchi%s", "backup of %s UTC, %d blocks%s") % (
                b["taken"], b["blocks"], "" if b["verified"] else T(" (NON verificato)", " (NOT verified)")),
                fg=TEXT if b["verified"] else WARN)
            self.chip_coding.configure(text=T("Codifica: backup del %s UTC%s", "Coding: backup of %s UTC%s") % (
                b["taken"], "" if b["verified"] else T(", non verificato", ", not verified")),
                fg=GOOD if b["verified"] else WARN)
        else:
            self.coding_info.configure(text=T("nessun backup per questo VIN", "no backup for this VIN"), fg=DIM)
            self.chip_coding.configure(text=T("Codifica: nessun backup", "Coding: no backup"), fg=DIM)
        self.tree.delete(*self.tree.get_children())
        rows = (store or {}).get("rows") or (catalog or {}).get("rows") or []
        for row in rows:
            state = row.get("state")
            if state in ROW_TEXT:
                text = T(ROW_TEXT[state][0], ROW_TEXT[state][1])
            else:
                text = T("Da confrontare con la MGU", "To be compared with the MGU")
            self.tree.insert("", "end", values=(self._title(row), row.get("installed") or "—",
                                                row.get("latest") or "—", text),
                             tags=(state,) if state in ROW_TEXT else ())
        latest = next((r.get("latest") for r in rows if r.get("id") == "robertn_store"), None)
        offline = catalog is not None and not catalog.get("online")
        suffix = T("  (versioni dell'ultimo controllo, senza internet)",
                   "  (versions of the last check, no internet)") if offline else ""
        summary = (store or {}).get("summary") or {}
        pending = summary.get("updates", 0) + summary.get("missing", 0)
        installed = summary.get("store") or "?"
        if store and store.get("error"):
            self.chip_store.configure(text=T("Store: verifica non riuscita", "Store: check failed"), fg=BAD)
            line, colour = T("Lettura delle versioni installate non riuscita: vedi il registro.",
                             "Reading the installed versions failed: see the log."), BAD
        elif store and not summary.get("installed"):
            self.chip_store.configure(text=T("Store: non installato", "Store: not installed"), fg=ACCENT)
            line, colour = T("Lo Store non è installato su questa MGU.",
                             "The Store is not installed on this MGU.") + suffix, TEXT
        elif store and pending:
            if pending == 1:
                self.chip_store.configure(text=T("Store: 1 aggiornamento", "Store: 1 update"), fg=WARN)
                line = T("Store %s installato: 1 aggiornamento disponibile.",
                         "Store %s installed: 1 update available.") % installed
            else:
                self.chip_store.configure(text=T("Store: %d aggiornamenti", "Store: %d updates") % pending, fg=WARN)
                line = T("Store %s installato: %d aggiornamenti disponibili.",
                         "Store %s installed: %d updates available.") % (installed, pending)
            line, colour = line + suffix, WARN
        elif store:
            self.chip_store.configure(text=T("Store: aggiornato", "Store: up to date"), fg=GOOD)
            line, colour = T("Store %s installato: è tutto aggiornato.",
                             "Store %s installed: everything is up to date.") % installed + suffix, GOOD
        else:
            self.chip_store.configure(text=T("Store: da verificare", "Store: to be checked"), fg=DIM)
            if self._store_loading:
                line, colour = T("Lettura delle ultime versioni di mgu-store da GitHub…",
                                 "Reading the latest mgu-store versions from GitHub…"), DIM
            elif self._store_error:
                line, colour = T("Versioni da GitHub non lette (%s). Premi “Controlla aggiornamenti GitHub” per "
                                 "riprovare.", "Versions from GitHub not read (%s). Press “Check GitHub updates” to "
                                 "try again.") % self._store_error, WARN
            elif latest:
                line, colour = T("Ultimo Store pubblicato: %s. Collega la vettura per confrontarlo con la MGU.",
                                 "Latest published Store: %s. Connect the car to compare it with the MGU.") % latest \
                    + suffix, TEXT
            else:
                line, colour = T("Premi “Connetti vettura” per leggere le versioni.",
                                 "Press “Connect car” to read the versions."), DIM
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
        self._apply_detected_variant(car.get("variant") or {})
        self._refresh()

    def _apply_detected_variant(self, variant):
        """Imposta da sola la variante quando è certa (ID7); per ID8 lascia
        scegliere BDC3/BCP all'utente. Non sovrascrive una scelta già fatta."""
        if not variant or self.arch.get() in ARCHES:
            return
        msg = variant.get("en") if _language[0] == "en" else variant.get("it")
        code = variant.get("code")
        if code in ARCHES:
            label = next((lbl for c, lbl in variant_choices() if c == code), None)
            if label:
                self.variant_label.set(label)
                self.arch.set(code)
        if msg:
            self._append((T("Riconoscimento: ", "Detected: ")) + msg + "\n")

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
                  text=T("Lettura della vettura e delle versioni in corso…", "Reading the car and the versions…"))

    def _check_done(self, rc):
        self._load_state()

    def do_enable(self):
        self._run(["enable"] + self._persist_args() + self._common_args(), "enable",
                  on_done=lambda rc: self.do_check())

    def do_flash(self):
        if not self._require_variant() or not self._require_vin():
            return
        if not messagebox.askyesno(
                T("Prima predisposizione MGU", "First MGU setup"),
                T("La head unit entra in modalità di programmazione e si riavvia.\n\n"
                  "Sull'unità verrà scritto il VIN %s.\n\n"
                  "Il flash cancella la codifica (CAFD), alcune impostazioni e gli abbinamenti Digital Key. "
                  "Hai fatto il backup della codifica?\n\nContinuare?",
                  "The head unit enters programming mode and restarts.\n\n"
                  "The VIN %s will be written to the unit.\n\n"
                  "The flash erases the coding (CAFD), some settings and the Digital Key pairings. "
                  "Did you back up the coding?\n\nContinue?") % self.vin.get().strip(),
                icon="warning"):
            return
        if not self._has_verified_backup():
            answer = messagebox.askyesnocancel(
                T("Backup della codifica", "Coding backup"),
                T("Per il VIN %s non c'è ancora un backup verificato della codifica.\n\n"
                  "Sì = fai prima il backup (consigliato), poi il flash.\n"
                  "No = flash senza backup.\nAnnulla = non fare nulla.",
                  "There is no verified coding backup for VIN %s yet.\n\n"
                  "Yes = back up first (recommended), then flash.\n"
                  "No = flash without a backup.\nCancel = do nothing.") % self.vin.get().strip(), icon="warning")
            if answer is None:
                return
            if answer:
                self._run(["preflight"] + self._common_args(), "preflight",
                          on_done=lambda rc: self._flash_after_backup(rc),
                          text=T("Backup della codifica in corso (sola lettura)…", "Coding backup running (read only)…"))
                return
        self._start_flash()

    def _flash_after_backup(self, rc):
        if rc == 0 and self._has_verified_backup():
            self._start_flash()
        else:
            messagebox.showerror(T("Backup codifica", "Coding backup"),
                                 T("Il backup non è riuscito o non è verificato: il flash NON è partito. Il motivo è "
                                   "nel registro.", "The backup failed or is not verified: the flash did NOT start. "
                                   "The reason is in the log."))

    def _start_flash(self):
        self._run(["flash"] + self._persist_args() + self._common_args(), "flash", on_done=self._flash_done,
                  text=T("Flash in corso: non scollegare il PC e non avviare altre operazioni…",
                         "Flash running: do not unplug the PC and do not start other operations…"))

    def _flash_done(self, rc):
        """Dopo un flash riuscito la codifica è cancellata: dire cosa viene prima dello Store."""
        if rc == 0 and self._has_verified_backup():
            if messagebox.askyesno(
                    T("Accesso SSH completato", "SSH access done"),
                    T("Root SSH attivo. Il flash ha cancellato la codifica della head unit.\n\n"
                      "Ripristinarla adesso dal backup (sperimentale)?\n\n"
                      "No = la ripristini tu con BimmerCode o E-Sys.",
                      "Root SSH active. The flash erased the head unit coding.\n\n"
                      "Restore it now from the backup (experimental)?\n\n"
                      "No = you restore it yourself with BimmerCode or E-Sys.")):
                self._run(["coding-restore"] + self._common_args(), "coding-restore", on_done=self._restore_done,
                          text=T("Ripristino della codifica in corso: non scollegare il cavo e non spegnere l'auto…",
                                 "Coding restore running: do not unplug the cable and do not switch the car off…"))
                return
        elif rc == 0:
            messagebox.showinfo(
                T("Accesso SSH completato", "SSH access done"),
                T("Root SSH attivo.\n\nIl flash ha cancellato la codifica della head unit: ripristinala adesso con "
                  "BimmerCode o E-Sys e riavvia la head unit.\n\n"
                  "Quando la MGU è di nuovo accesa, premi “Installa / Aggiorna”.",
                  "Root SSH active.\n\nThe flash erased the head unit coding: restore it now with "
                  "BimmerCode or E-Sys and restart the head unit.\n\n"
                  "When the MGU is on again, press “Install / Update”."))
        self.do_check()

    def do_fix_key(self):
        if not self._require_variant() or not self._require_vin():
            return
        if not messagebox.askyesno(
                T("Correggere la chiave SSH rifiutata?", "Fix the refused SSH key?"),
                T("Per le unità in cui flash e registrazione riescono e la porta 22 si apre, ma l'accesso root "
                  "viene rifiutato perché la loro chiave originale è diversa.\n\n"
                  "Rifà il flash dell'immagine di debug con la nostra chiave nell'unica regione non firmata. "
                  "Nel caso peggiore la chiave non resta, nessun blocco. La head unit si riavvia e, come ogni "
                  "flash, la codifica CAFD si cancella: ripristinala dal backup.\n\n"
                  "Sull'unità verrà scritto il VIN %s.\n\nContinuare?",
                  "For units where flash and enrollment succeed and port 22 opens, but root login is refused "
                  "because their original key is different.\n\n"
                  "It flashes the debug image again with our key in the only unsigned region. "
                  "In the worst case the key does not stay, no brick. The head unit restarts and, like every "
                  "flash, the CAFD coding is erased: restore it from the backup.\n\n"
                  "The VIN %s will be written to the unit.\n\nContinue?") % self.vin.get().strip(),
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
            messagebox.showerror(T("Ripristino codifica", "Coding restore"),
                                 T("Non c'è un backup verificato della codifica per il VIN %s.\n\n"
                                   "Il backup va fatto PRIMA del flash con “Backup codifica”.",
                                   "There is no verified coding backup for VIN %s.\n\n"
                                   "The backup has to be made BEFORE the flash with “Back up coding”.")
                                 % self.vin.get().strip())
            return
        if not messagebox.askyesno(
                T("Ripristino codifica (sperimentale)", "Coding restore (experimental)"),
                T("Riscrive sulla MGU la codifica dell'ultimo backup di questo VIN, blocco per blocco, rileggendo "
                  "ogni blocco. Si ferma al primo rifiuto della MGU e alla fine la riavvia.\n\n"
                  "È SPERIMENTALE: tieni a portata BimmerCode o E-Sys. Nel caso peggiore la MGU resta senza "
                  "codifica, come dopo il flash.\n\nContinuare?",
                  "Writes the coding of the latest backup of this VIN back to the MGU, block by block, reading "
                  "every block back. It stops at the first refusal of the MGU and restarts it at the end.\n\n"
                  "It is EXPERIMENTAL: keep BimmerCode or E-Sys at hand. In the worst case the MGU stays without "
                  "coding, as after the flash.\n\nContinue?"), icon="warning"):
            return
        self._run(["coding-restore"] + self._common_args(), "coding-restore", on_done=self._restore_done,
                  text=T("Ripristino della codifica in corso: non scollegare il cavo e non spegnere l'auto…",
                         "Coding restore running: do not unplug the cable and do not switch the car off…"))

    def _restore_done(self, rc):
        title = T("Ripristino codifica", "Coding restore")
        if rc == 0:
            messagebox.showinfo(title,
                                T("Codifica ripristinata: la MGU riporta di nuovo la sua CAFD.\n\n"
                                  "Quando la MGU è riaccesa controlla lingua, retrocamera e menu, poi premi "
                                  "“Installa / Aggiorna”.",
                                  "Coding restored: the MGU reports its CAFD again.\n\n"
                                  "When the MGU is on again check language, reverse camera and menus, then press "
                                  "“Install / Update”."))
        elif rc == ssh_enable.EXIT_CODING_PARTIAL:
            messagebox.showwarning(title,
                                   T("I blocchi sono stati scritti, ma la MGU non riporta ancora la sua CAFD.\n\n"
                                     "Completa con E-Sys (o BimmerCode) e conserva il registro in data\\logs: "
                                     "serve a migliorare il ripristino.",
                                     "The blocks were written, but the MGU does not report its CAFD yet.\n\n"
                                     "Finish with E-Sys (or BimmerCode) and keep the log in data\\logs: "
                                     "it helps to improve the restore."))
        else:
            messagebox.showerror(title,
                                 T("Il ripristino si è fermato: il motivo è nel registro.\n\n"
                                   "Ripristina con BimmerCode o E-Sys e conserva il registro in data\\logs.",
                                   "The restore stopped: the reason is in the log.\n\n"
                                   "Restore with BimmerCode or E-Sys and keep the log in data\\logs."))
        self.do_check()

    def do_backup(self):
        self._run(["preflight"] + self._common_args(), "preflight", on_done=self._backup_done,
                  text=T("Backup della codifica in corso (sola lettura)…", "Coding backup running (read only)…"))

    def _backup_done(self, rc):
        folder = os.path.join(HERE, "data", "preflight")
        if rc == 0:
            messagebox.showinfo(T("Backup codifica", "Coding backup"),
                                T("Backup della codifica salvato e verificato in:\n%s\n\n"
                                  "Copialo anche fuori da questo PC prima del flash.",
                                  "Coding backup saved and verified in:\n%s\n\n"
                                  "Copy it outside this PC too before the flash.") % folder)
            self._open_folder(folder)
        else:
            messagebox.showwarning(T("Backup codifica", "Coding backup"),
                                   T("Il backup non è completo o non è verificato: non farci affidamento come "
                                     "unico backup. Il motivo è nel registro.",
                                     "The backup is not complete or not verified: do not rely on it as the only "
                                     "backup. The reason is in the log."))

    def do_disable(self):
        if not messagebox.askyesno(T("Disattivare SSH?", "Disable SSH?"),
                                   T("Riportare SSH allo stato originale e spegnere la modalità debug sulla "
                                     "head unit?\n\nLo Store installato resta, ma senza SSH non si aggiorna da qui.",
                                     "Put SSH back to its original state and switch debug mode off on the "
                                     "head unit?\n\nThe installed Store stays, but without SSH it cannot be "
                                     "updated from here.")):
            return
        self._run(["disable"] + self._common_args(), "disable", on_done=lambda rc: self.do_check())

    def do_store(self):
        if not messagebox.askyesno(
                T("Installare / aggiornare lo Store?", "Install / update the Store?"),
                T("Installa mgu-store sulla head unit tramite root SSH, o lo aggiorna all'ultima versione.\n\n"
                  "Tieni l'auto sveglia e con alimentazione stabile (motore acceso o caricabatterie) fino alla "
                  "fine, e questo PC collegato a internet.\n\nContinuare?",
                  "Installs mgu-store on the head unit over root SSH, or updates it to the latest version.\n\n"
                  "Keep the car awake with a stable power supply (engine running or battery charger) until the "
                  "end, and this PC connected to the internet.\n\nContinue?")):
            return
        self._run(["store"] + self._common_args(), "store", on_done=self._store_done,
                  text=T("Installazione dello Store in corso…", "Store installation running…"))

    def _ask_restart(self, message):
        if messagebox.askyesno(T("Riavviare la MGU?", "Restart the MGU?"), message):
            self._run(["store-restart"] + self._common_args(), "store-restart",
                      text=T("Riavvio della MGU in corso…", "MGU restart running…"))
            return True
        return False

    def _store_done(self, rc):
        if rc == store_mod.EXIT_RESTART_NEEDED:
            if self._ask_restart(T("Lo Store è installato.\n\nRiavviare ora la MGU per rendere effettiva la modifica?",
                                   "The Store is installed.\n\nRestart the MGU now to apply the change?")):
                return
        elif rc == store_mod.EXIT_DONE:
            messagebox.showinfo(T("Store installato", "Store installed"),
                                T("Lo Store è installato: la sua icona è tra le app dell'auto.",
                                  "The Store is installed: its icon is among the car's apps."))
        elif rc == store_mod.EXIT_REFUSED:
            messagebox.showwarning(
                T("Store non installato", "Store not installed"),
                T("L'installer si è fermato prima di cambiare qualsiasi cosa.\n\n"
                  "Se il registro dice che manca la codifica (normale dopo un flash): ripristinala con BimmerCode "
                  "o E-Sys, riavvia la head unit, poi premi di nuovo “Installa / Aggiorna”.",
                  "The installer stopped before changing anything.\n\n"
                  "If the log says the coding is missing (normal after a flash): restore it with BimmerCode "
                  "or E-Sys, restart the head unit, then press “Install / Update” again."))
        else:
            messagebox.showerror(T("Store non installato", "Store not installed"),
                                 T("L'installazione non è terminata. Il motivo è nel registro.",
                                   "The installation did not finish. The reason is in the log."))
        self.do_check()

    def do_remove(self):
        if not messagebox.askyesno(
                T("Rimuovere lo Store?", "Remove the Store?"),
                T("Toglie dalla head unit tutto quello che l'installazione dello Store ha messo: app, strumenti, "
                  "sorgenti e token.\n\nL'accesso SSH resta.\n\nContinuare?",
                  "Removes from the head unit everything the Store installation put there: apps, tools, "
                  "sources and tokens.\n\nSSH access stays.\n\nContinue?"), icon="warning"):
            return
        self._run(["store-remove"] + self._common_args(), "store-remove", on_done=self._remove_done,
                  text=T("Rimozione dello Store in corso…", "Store removal running…"))

    def _remove_done(self, rc):
        if rc == store_mod.EXIT_RESTART_NEEDED:
            if self._ask_restart(T("Lo Store è stato rimosso.\n\nRiavviare ora la MGU per completare la rimozione?",
                                   "The Store was removed.\n\nRestart the MGU now to complete the removal?")):
                return
        elif rc == store_mod.EXIT_DONE:
            messagebox.showinfo(T("Store rimosso", "Store removed"),
                                T("Lo Store è stato rimosso dalla head unit.", "The Store was removed from the head unit."))
        else:
            messagebox.showerror(T("Rimozione non completata", "Removal not completed"),
                                 T("La rimozione non è terminata. Il motivo è nel registro.",
                                   "The removal did not finish. The reason is in the log."))
        self.do_check()

    def do_restart(self):
        if not messagebox.askyesno(T("Riavviare la MGU?", "Restart the MGU?"),
                                   T("Riavviare adesso la head unit? Torna attiva dopo circa un minuto.",
                                     "Restart the head unit now? It is back after about a minute.")):
            return
        self._run(["store-restart"] + self._common_args(), "store-restart",
                  text=T("Riavvio della MGU in corso…", "MGU restart running…"))

    def detect_vin(self):
        if self._is_busy:
            return
        self._busy(True, T("Ricerca del gateway ENET…", "Looking for the ENET gateway…"))
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

    def do_diagnose(self):
        """Legge TUTTO dalla vettura in sola lettura (variante, software, CAFD,
        seriale, part number, errori) e salva un report. Non modifica nulla."""
        if self._is_busy:
            return
        import diagnose
        self._busy(True, T("Diagnostica completa in corso (sola lettura)…",
                           "Full diagnostics running (read only)…"))
        gw = self.gateway.get().strip() or "auto"
        mgu_ip = self.mgu_ip.get().strip() or None
        self._append("\n$ diagnostica (sola lettura)\n")

        def task():
            try:
                code, path = diagnose.run(gateway=gw, mgu_ip=mgu_ip, open_folder=False)
                result = (code, path, None)
            except Exception as e:  # noqa: BLE001
                result = (1, None, str(e))
            self._later(lambda: self._diagnose_done(*result))

        self.worker = threading.Thread(target=task, daemon=True)
        self.worker.start()

    def _diagnose_done(self, code, path, error):
        self._busy(False)
        if error:
            self._append("ERRORE: %s\n" % error)
            messagebox.showerror(T("Diagnostica", "Diagnostics"),
                                 T("Non riuscita:\n%s", "Failed:\n%s") % error)
            return
        self._append((T("Report salvato: ", "Report saved: ")) + (path or "?") + "\n")
        if path:
            self._open_folder(os.path.dirname(path))
        if code == 0:
            messagebox.showinfo(T("Diagnostica completa", "Full diagnostics"),
                                T("Letto tutto dalla vettura. Il report è in data\\diagnostica\\.\n\n"
                                  "Puoi inviarmelo: è solo lettura, nessuna modifica all'auto.",
                                  "Everything read from the car. The report is in data\\diagnostica\\.\n\n"
                                  "You can send it to me: read-only, no change to the car."))
        else:
            messagebox.showwarning(T("Diagnostica", "Diagnostics"),
                                   T("Collegamento non riuscito: vedi il report in data\\diagnostica\\.",
                                     "Connection failed: see the report in data\\diagnostica\\."))

    def do_store_latest(self, quiet=False):
        """Legge da GitHub le ultime versioni pubblicate di mgu-store (release
        "catalog") e le confronta con quelle lette dalla MGU all'ultimo
        collegamento. Non tocca l'auto: basta internet sul PC. *quiet*
        (all'avvio): solo tabella e log, nessuna finestra."""
        if self._is_busy:
            return
        self._store_loading = True
        self._store_error = None
        self._busy(True, T("Lettura delle ultime versioni di mgu-store da GitHub…",
                           "Reading the latest mgu-store versions from GitHub…"))
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
        title = T("Aggiornamenti mgu-store", "mgu-store updates")
        if rows is None:
            if quiet:
                return
            messagebox.showwarning(title, T("Impossibile leggere le versioni da GitHub:\n%s",
                                            "Cannot read the versions from GitHub:\n%s") % error)
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
        offline = "" if online else T("\n\n(Senza internet: sono le versioni dell'ultimo controllo.)",
                                      "\n\n(No internet: these are the versions of the last check.)")
        summary = (state.get("store") or {}).get("summary")
        if summary and summary.get("installed"):
            pending = summary.get("updates", 0) + summary.get("missing", 0)
            if pending:
                messagebox.showinfo(
                    title,
                    T("Ultima versione dello Store: %s (sulla MGU: %s).\n\nComponenti da aggiornare o installare: %d.\n"
                      "Collega la vettura e premi “Installa / Aggiorna”.%s",
                      "Latest Store version: %s (on the MGU: %s).\n\nComponents to update or install: %d.\n"
                      "Connect the car and press “Install / Update”.%s")
                    % (latest, summary.get("store") or "?", pending, offline))
            else:
                messagebox.showinfo(title, T("La MGU ha già l'ultima versione dello Store (%s).%s",
                                             "The MGU already has the latest Store version (%s).%s")
                                    % (latest, offline))
        else:
            messagebox.showinfo(title,
                                T("Ultima versione pubblicata dello Store: %s.\n\nPremi “Connetti vettura” per "
                                  "confrontarla con quella sulla MGU.%s",
                                  "Latest published Store version: %s.\n\nPress “Connect car” to "
                                  "compare it with the one on the MGU.%s") % (latest, offline))

    def do_program_update(self):
        """Confronta questa versione di MGU Plus con l'ultima pubblicata su GitHub."""
        if self._is_busy:
            return
        self._busy(True, T("Controllo degli aggiornamenti di MGU Plus…", "Checking for MGU Plus updates…"))

        def task():
            result = check_program_update()
            self._later(lambda: self._program_update_done(result))

        self.worker = threading.Thread(target=task, daemon=True)
        self.worker.start()

    def _program_update_done(self, result):
        self._busy(False)
        kind, version, url, detail, beta = result
        if kind == "newer":
            label = T("%s (beta da provare)", "%s (beta to be tested)") % version if beta else version
            self._append("MGU Plus %s is available (this is %s).\n" % (label, APP_VERSION))
            text = T("È disponibile MGU Plus %s (questa è la %s).", "MGU Plus %s is available (this is %s).") \
                % (label, APP_VERSION)
            if beta:
                text += T("\n\nATTENZIONE: è una versione BETA, non ancora provata in auto. Usala solo se puoi "
                          "rimediare da solo (BimmerCode / E-Sys) se qualcosa va storto.",
                          "\n\nWARNING: this is a BETA version, not yet tested in a car. Use it only if you can "
                          "fix things yourself (BimmerCode / E-Sys) if something goes wrong.")
            if messagebox.askyesno(T("Aggiornamento disponibile", "Update available"),
                                   text + T("\n\nAprire la pagina di download?", "\n\nOpen the download page?"),
                                   icon="warning" if beta else "question"):
                webbrowser.open(url)
        elif kind == "current":
            messagebox.showinfo(T("Nessun aggiornamento", "No update"),
                                T("Hai già l'ultima versione di MGU Plus (%s%s).",
                                  "You already have the latest version of MGU Plus (%s%s).")
                                % (APP_VERSION, ", beta" if beta else ""))
        elif kind == "none":
            messagebox.showinfo(T("Nessun aggiornamento", "No update"),
                                T("Su GitHub (%s) non è ancora pubblicata nessuna versione di MGU Plus.",
                                  "No MGU Plus version is published on GitHub (%s) yet.") % UPDATE_REPO)
        else:
            messagebox.showwarning(T("Controllo non riuscito", "Check failed"),
                                   T("Impossibile controllare gli aggiornamenti:\n%s",
                                     "Cannot check for updates:\n%s") % detail)


def check_program_update(api=UPDATE_API, current=APP_VERSION):
    """("newer" | "current" | "none" | "error", versione, pagina, dettaglio, beta)
    per la release più recente del repository di MGU Plus, pre-release (beta)
    comprese: beta è True quando quella release è segnata come pre-release."""
    request = urllib.request.Request(api, headers={"User-Agent": "MGU-Plus",
                                                   "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return ("none", None, None, None, False)
        return ("error", None, None, "HTTP %d" % e.code, False)
    except (urllib.error.URLError, OSError, ValueError) as e:
        return ("error", None, None, str(getattr(e, "reason", e)), False)
    releases = [r for r in (data if isinstance(data, list) else [data])
                if isinstance(r, dict) and not r.get("draft") and str(r.get("tag_name") or "").strip()]
    if not releases:
        return ("none", None, None, None, False)
    best = max(releases, key=lambda r: store_mod.parse_version(str(r["tag_name"]).strip().lstrip("vV")))
    version = str(best["tag_name"]).strip().lstrip("vV")
    url = best.get("html_url") or "https://github.com/%s/releases" % UPDATE_REPO
    beta = bool(best.get("prerelease"))
    if store_mod.parse_version(version) > store_mod.parse_version(current):
        return ("newer", version, url, None, beta)
    return ("current", version, url, None, beta)


def main():
    if "--diagnostica" in sys.argv:
        import diagnose
        gw = "auto"
        mgu_ip = None
        for i, a in enumerate(sys.argv):
            if a == "--gateway" and i + 1 < len(sys.argv):
                gw = sys.argv[i + 1]
            if a == "--mgu-ip" and i + 1 < len(sys.argv):
                mgu_ip = sys.argv[i + 1]
        code, _path = diagnose.run(gateway=gw, mgu_ip=mgu_ip, open_folder=True)
        sys.exit(code)
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
