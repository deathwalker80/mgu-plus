"""Controlli disegnati a mano per MGU Plus, con lo stesso aspetto di NBT
Companion (deathwalker80/nbt-companion, UI/Theme.cs e UI/Controls.cs):
pulsanti arrotondati, pastiglie di stato, voci di navigazione numerate,
riquadri arrotondati e barra di attività.

Tk non sfuma i bordi delle forme disegnate sul Canvas: gli angoli arrotondati
sono piccole immagini calcolate qui con la copertura reale dei pixel
(antialias), i lati dritti sono rettangoli del Canvas. Niente librerie esterne.
"""

import tkinter as tk
from tkinter import font as tkfont

# Tavolozza di NBT Companion (UI/Theme.cs).
BACK = "#14171c"
PANEL = "#1c2128"
FIELD = "#242a33"
BORDER = "#373f4b"
TEXT = "#e6e8eb"
DIM = "#9aa4b2"
ACCENT = "#2d7ff9"
ACCENT_HOVER = "#4691ff"
ACCENT_PRESSED = "#1e64d2"
ACCENT_DISABLED = "#283e62"
HOVER = "#21272f"
GOOD = "#3fb950"
BAD = "#f85149"
WARN = "#d29922"
WHITE = "#ffffff"

FAMILY = "Segoe UI"
FAMILY_SEMIBOLD = "Segoe UI Semibold"
BODY = (FAMILY, 10)
SMALL = (FAMILY, 9)
HEADING = (FAMILY_SEMIBOLD, 14)
BOLD = (FAMILY_SEMIBOLD, 10)
MONO = ("Consolas", 10)

_SAMPLES = 4  # campioni per lato di ogni pixel, per la copertura degli angoli


def _rgb(colour):
    colour = colour.lstrip("#")
    return tuple(int(colour[i:i + 2], 16) for i in (0, 2, 4))


def _hex(rgb):
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(c)))) for c in rgb)


def _mix(colours_weights):
    return _hex([sum(c[i] * w for c, w in colours_weights) for i in range(3)])


class _Corners:
    """Angoli arrotondati (e cerchi) già sfumati, condivisi da tutti i controlli
    della stessa finestra principale (le immagini Tk appartengono a quella)."""

    @staticmethod
    def _cache(root):
        cache = getattr(root, "_rounded_images", None)
        if cache is None:
            cache = {}
            root._rounded_images = cache
        return cache

    def corner(self, root, radius, fill, border, background, which):
        """Immagine radius x radius dell'angolo *which* ('nw', 'ne', 'sw', 'se'):
        cerchio di raggio *radius* riempito con *fill* e bordo di 1 px *border*,
        fuori dal cerchio *background*."""
        cache = self._cache(root)
        key = ("corner", radius, fill, border, background, which)
        image = cache.get(key)
        if image is None:
            image = self._paint(root, radius, radius, radius, fill, border, background, which)
            cache[key] = image
        return image

    def circle(self, root, diameter, fill, border, background):
        cache = self._cache(root)
        key = ("circle", diameter, fill, border, background)
        image = cache.get(key)
        if image is None:
            image = self._paint(root, diameter, diameter, diameter / 2.0, fill, border, background, None)
            cache[key] = image
        return image

    @staticmethod
    def _paint(root, width, height, radius, fill, border, background, which):
        bg, fl, bd = _rgb(background), _rgb(fill), _rgb(border)
        # centro del cerchio nelle coordinate dell'immagine
        if which is None:
            cx = cy = radius
        else:
            cx = radius if which in ("nw", "sw") else 0.0
            cy = radius if which in ("nw", "ne") else 0.0
        inner = max(0.0, radius - 1.0)
        rows = []
        step = 1.0 / _SAMPLES
        for y in range(height):
            row = []
            for x in range(width):
                outside = ring = 0
                for sy in range(_SAMPLES):
                    py = y + (sy + 0.5) * step - cy
                    for sx in range(_SAMPLES):
                        px = x + (sx + 0.5) * step - cx
                        d2 = px * px + py * py
                        if d2 > radius * radius:
                            outside += 1
                        elif d2 > inner * inner:
                            ring += 1
                total = float(_SAMPLES * _SAMPLES)
                o, r = outside / total, ring / total
                row.append(_mix(((bg, o), (bd, r), (fl, 1.0 - o - r))))
            rows.append("{" + " ".join(row) + "}")
        image = tk.PhotoImage(master=root, width=width, height=height)
        image.put(" ".join(rows), to=(0, 0))
        return image


CORNERS = _Corners()


def round_rect(canvas, x0, y0, x1, y1, radius, fill, border, background, tag):
    """Rettangolo arrotondato con bordo di 1 px, da (x0, y0) incluso a (x1, y1)
    escluso, sfumato sullo sfondo *background*. Tutto con l'etichetta *tag*."""
    radius = max(1, min(radius, (x1 - x0) // 2, (y1 - y0) // 2))
    root = canvas.winfo_toplevel()
    canvas.create_rectangle(x0 + radius, y0, x1 - radius, y1, fill=fill, outline="", width=0, tags=tag)
    canvas.create_rectangle(x0, y0 + radius, x1, y1 - radius, fill=fill, outline="", width=0, tags=tag)
    if border != fill:
        canvas.create_line(x0 + radius, y0, x1 - radius, y0, fill=border, tags=tag)
        canvas.create_line(x0 + radius, y1 - 1, x1 - radius, y1 - 1, fill=border, tags=tag)
        canvas.create_line(x0, y0 + radius, x0, y1 - radius, fill=border, tags=tag)
        canvas.create_line(x1 - 1, y0 + radius, x1 - 1, y1 - radius, fill=border, tags=tag)
    for which, x, y in (("nw", x0, y0), ("ne", x1 - radius, y0),
                        ("sw", x0, y1 - radius), ("se", x1 - radius, y1 - radius)):
        canvas.create_image(x, y, anchor="nw", tags=tag,
                            image=CORNERS.corner(root, radius, fill, border, background, which))


def background_of(widget):
    try:
        return widget.cget("background")
    except tk.TclError:
        return BACK


class RoundButton(tk.Canvas):
    """Pulsante arrotondato: principale (blu pieno), secondario (contornato) o
    pericoloso (contornato, testo rosso); stati sopra, premuto e disattivato."""

    def __init__(self, master, text="", command=None, kind="secondary", min_width=120, height=36, **kw):
        self._bg = background_of(master)
        super().__init__(master, height=height, bg=self._bg, highlightthickness=0, borderwidth=0,
                         cursor="hand2", takefocus=1, **kw)
        self._text = text
        self._command = command
        self._kind = kind
        self._min_width = min_width
        self._height = height
        self._hover = False
        self._down = False
        self._enabled = True
        self._font = tkfont.Font(root=self, font=BOLD)
        self._resize()
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<KeyPress-space>", lambda _e: self.invoke())
        self.bind("<KeyPress-Return>", lambda _e: self.invoke())
        self.bind("<FocusIn>", lambda _e: self._draw())
        self.bind("<FocusOut>", lambda _e: self._draw())

    # stile ttk.Button, quanto basta al resto del programma
    def configure(self, cnf=None, **kw):
        cnf = dict(cnf or {}, **kw)
        redraw = False
        if "text" in cnf:
            self._text = cnf.pop("text")
            self._resize()
            redraw = True
        if "command" in cnf:
            self._command = cnf.pop("command")
        if "state" in cnf:
            self._enabled = cnf.pop("state") != "disabled"
            if not self._enabled:
                self._hover = self._down = False
            super().configure(cursor="hand2" if self._enabled else "arrow")
            redraw = True
        result = super().configure(cnf) if cnf else None
        if redraw:
            self._draw()
        return result

    config = configure

    def cget(self, key):
        if key == "text":
            return self._text
        if key == "state":
            return "normal" if self._enabled else "disabled"
        return super().cget(key)

    def state(self):
        return () if self._enabled else ("disabled",)

    def invoke(self):
        if self._enabled and self._command:
            return self._command()
        return None

    def _resize(self):
        width = max(self._min_width, self._font.measure(self._text) + 28)
        super().configure(width=width)
        self._width = width
        self._draw()

    def _colours(self):
        if not self._enabled:
            if self._kind == "primary":
                return ACCENT_DISABLED, ACCENT_DISABLED, DIM
            return FIELD, BORDER, DIM
        if self._kind == "primary":
            fill = ACCENT_PRESSED if self._down else ACCENT_HOVER if self._hover else ACCENT
            return fill, fill, WHITE
        fill = BACK if self._down else BORDER if self._hover else FIELD
        if self._kind == "danger":
            return fill, BAD if self._hover else BORDER, BAD
        return fill, DIM if self._hover else BORDER, TEXT

    def _draw(self):
        self.delete("all")
        fill, border, text = self._colours()
        round_rect(self, 0, 0, self._width, self._height, 7, fill, border, self._bg, "shape")
        self.create_text(self._width // 2, self._height // 2, text=self._text, fill=text, font=self._font)
        if self._enabled and self.focus_get() is self:
            self.create_rectangle(3, 3, self._width - 4, self._height - 4, outline="#9aa0a8", dash=(1, 2))

    def _on_enter(self, _e):
        if self._enabled:
            self._hover = True
            self._draw()

    def _on_leave(self, _e):
        self._hover = self._down = False
        self._draw()

    def _on_press(self, _e):
        if self._enabled:
            self._down = True
            self._draw()

    def _on_release(self, e):
        inside = 0 <= e.x < self._width and 0 <= e.y < self._height
        was_down = self._down
        self._down = False
        self._draw()
        if was_down and inside:
            self.invoke()


class Chip(tk.Canvas):
    """Pastiglia di stato: pallino colorato (verde, giallo, grigio, rosso) e testo."""

    def __init__(self, master, text="", colour=DIM, **kw):
        self._bg = background_of(master)
        super().__init__(master, height=26, bg=self._bg, highlightthickness=0, borderwidth=0, **kw)
        self._text = text
        self._colour = colour
        self._font = tkfont.Font(root=self, font=SMALL)
        self._draw()

    def configure(self, cnf=None, **kw):
        cnf = dict(cnf or {}, **kw)
        changed = False
        if "text" in cnf:
            self._text = cnf.pop("text")
            changed = True
        for key in ("fg", "foreground"):
            if key in cnf:
                self._colour = cnf.pop(key)
                changed = True
        result = super().configure(cnf) if cnf else None
        if changed:
            self._draw()
        return result

    config = configure

    def cget(self, key):
        if key == "text":
            return self._text
        if key in ("fg", "foreground"):
            return self._colour
        return super().cget(key)

    def _draw(self):
        width = self._font.measure(self._text) + 34
        super().configure(width=width)
        self.delete("all")
        round_rect(self, 0, 0, width, 26, 13, PANEL, BORDER, self._bg, "shape")
        root = self.winfo_toplevel()
        self.create_image(11, 9, anchor="nw", image=CORNERS.circle(root, 8, self._colour, self._colour, PANEL))
        self.create_text(25, 13, text=self._text, anchor="w", fill=TEXT, font=self._font)


class NavItem(tk.Canvas):
    """Voce dell'elenco delle pagine: numero nel cerchio, testo, barra blu a
    sinistra quando è selezionata."""

    def __init__(self, master, number, text, command, width=242, **kw):
        super().__init__(master, width=width, height=44, bg=PANEL, highlightthickness=0, borderwidth=0,
                         cursor="hand2", **kw)
        self._number = str(number)
        self._text = text
        self._command = command
        self._width = width
        self._hover = False
        self._selected = False
        self._font = tkfont.Font(root=self, font=BOLD)
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))
        self.bind("<ButtonRelease-1>", lambda _e: self._command())
        self._draw()

    @property
    def selected(self):
        return self._selected

    @selected.setter
    def selected(self, value):
        self._selected = bool(value)
        self._draw()

    def _set_hover(self, on):
        self._hover = on
        self._draw()

    def _draw(self):
        self.delete("all")
        root = self.winfo_toplevel()
        if self._selected or self._hover:
            fill = FIELD if self._selected else HOVER
            round_rect(self, 0, 0, self._width, 44, 8, fill, fill, PANEL, "shape")
        if self._selected:
            round_rect(self, 0, 9, 4, 44 - 9, 2, ACCENT, ACCENT, FIELD, "bar")
        back = FIELD if self._selected else HOVER if self._hover else PANEL
        circle_fill = ACCENT if self._selected else FIELD
        circle_border = ACCENT if self._selected else BORDER
        self.create_image(14, 9, anchor="nw", image=CORNERS.circle(root, 26, circle_fill, circle_border, back))
        self.create_text(27, 22, text=self._number, fill=WHITE if self._selected else DIM, font=self._font)
        self.create_text(50, 22, text=self._text, anchor="w", fill=WHITE if self._selected else TEXT,
                         font=self._font)


class Card(tk.Frame):
    """Riquadro arrotondato che contiene una pagina o il registro: il contenuto
    va in *self.body*."""

    def __init__(self, master, padding=(16, 14, 16, 14), **kw):
        self._outer = background_of(master)
        super().__init__(master, bg=self._outer, **kw)
        self._padding = padding
        # dimensione richiesta minima: la decide chi impagina il riquadro
        self.canvas = tk.Canvas(self, bg=self._outer, highlightthickness=0, borderwidth=0, width=1, height=1)
        self.canvas.pack(fill="both", expand=True)
        self.body = tk.Frame(self.canvas, bg=PANEL)
        self._window = self.canvas.create_window(padding[0], padding[1], anchor="nw", window=self.body)
        self._size = None
        self.canvas.bind("<Configure>", self._on_configure)

    def _on_configure(self, e):
        size = (e.width, e.height)
        if size == self._size or e.width < 30 or e.height < 30:
            return
        self._size = size
        self.canvas.delete("shape")
        round_rect(self.canvas, 0, 0, e.width, e.height, 10, PANEL, BORDER, self._outer, "shape")
        self.canvas.tag_lower("shape")
        left, top, right, bottom = self._padding
        self.canvas.coords(self._window, left, top)
        self.canvas.itemconfigure(self._window, width=max(1, e.width - left - right),
                                  height=max(1, e.height - top - bottom))


class Field(tk.Frame):
    """Bordo sottile intorno a un campo: grigio, blu quando il campo ha il fuoco."""

    def __init__(self, master, padding=(8, 6), **kw):
        super().__init__(master, bg=FIELD, highlightthickness=1, highlightbackground=BORDER,
                         highlightcolor=BORDER, **kw)
        self._padding = padding

    def hold(self, inner):
        inner.pack(fill="both", expand=True, padx=self._padding[0], pady=self._padding[1])
        inner.bind("<FocusIn>", lambda _e: self.configure(highlightbackground=ACCENT, highlightcolor=ACCENT), add="+")
        inner.bind("<FocusOut>", lambda _e: self.configure(highlightbackground=BORDER, highlightcolor=BORDER),
                   add="+")
        return inner


def entry(master, variable, width=31):
    """Campo di testo come in NBT Companion: sfondo scuro, bordo che si accende."""
    frame = Field(master)
    frame.hold(tk.Entry(frame, textvariable=variable, width=width, bg=FIELD, fg=TEXT, insertbackground=TEXT,
                        relief="flat", borderwidth=0, highlightthickness=0, font=BODY,
                        disabledbackground=FIELD, disabledforeground=DIM, selectbackground=ACCENT,
                        selectforeground=WHITE))
    return frame


class BusyBar(tk.Canvas):
    """Barra animata sotto l'intestazione mentre un'operazione è in corso."""

    def __init__(self, master, **kw):
        super().__init__(master, height=3, bg=PANEL, highlightthickness=0, borderwidth=0, **kw)
        self._pos = 0
        self._job = None

    @property
    def running(self):
        return self._job is not None

    @running.setter
    def running(self, on):
        if on and self._job is None:
            self._pos = 0
            self._tick()
        elif not on and self._job is not None:
            self.after_cancel(self._job)
            self._job = None
            self.delete("all")

    def _tick(self):
        width = max(1, self.winfo_width())
        self._pos = (self._pos + 8) % (width + 240)
        self.delete("all")
        self.create_rectangle(self._pos - 240, 0, self._pos, 3, fill=ACCENT, outline="", width=0)
        self._job = self.after(30, self._tick)

    def destroy(self):
        if self._job is not None:
            try:
                self.after_cancel(self._job)
            except tk.TclError:
                pass
            self._job = None
        super().destroy()
