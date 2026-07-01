#!/usr/bin/env python3
"""
mandarin_learner_gui_v2.py — Modern "media player" GUI for the Mandarin Reader
==============================================================================
A second-generation interface for mandarin_learner_engine.py, styled like a
cross between Spotify, Audible, and a karaoke machine:

  • A big, centred **karaoke stage** — the current sentence is large with pinyin
    above every character; the lines before/after sit dimmed above and below,
    like scrolling lyrics.
  • A **now-playing transport bar** — a large circular Play, rounded buttons with
    the **keyboard shortcut printed right on each button**, a progress scrubber,
    and a live accuracy score.
  • A dark, roomy layout with large type that actually uses the whitespace.

Same engine, same features as V1 (voices, speeds, Simplified⇄Traditional,
offline speak-back grading, resume). V1 (`mandarin_learner_gui.py`) is untouched.

Run:  python3 mandarin_learner_gui_v2.py
"""

import os
import queue
import threading
import tempfile
import traceback
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import tkinter.font as tkfont
from pathlib import Path

import mandarin_learner_engine as E


# ── Fonts ─────────────────────────────────────────────────────────────────────

def _pick_fonts():
    import sys
    if sys.platform == "darwin":
        return "PingFang SC", "Helvetica Neue", "Menlo"
    if os.name == "nt":
        return "Microsoft YaHei", "Segoe UI", "Consolas"
    return "Noto Sans CJK SC", "DejaVu Sans", "DejaVu Sans Mono"


CJK_FONT, UI_FONT, MONO_FONT = _pick_fonts()


# ── Colour palettes ───────────────────────────────────────────────────────────

DARK = {
    "bg": "#0B0E11", "bar": "#14181D", "panel": "#1A1F26", "stage_hi": "#1B2634",
    "text": "#EAF0F6", "dim": "#7C8794", "faint": "#4C555F", "pinyin": "#9FB0C0",
    "accent": "#1ED760", "on_accent": "#08160B", "danger": "#FF5468",
    "on_danger": "#FFFFFF", "btn": "#232A33", "btn_hi": "#2E3742",
    "btn_disabled": "#171B20", "border": "#262D36",
    "green": "#2BD46A", "amber": "#F5B300", "red": "#FF5C6C",
    "keycap": "#2B333D", "keycap_fg": "#C4CED8", "scrub": "#2A323C",
}
LIGHT = {
    "bg": "#F4F6F9", "bar": "#FFFFFF", "panel": "#FFFFFF", "stage_hi": "#EAF1FF",
    "text": "#1B2129", "dim": "#8A93A0", "faint": "#B7BEC8", "pinyin": "#5B6675",
    "accent": "#16C25E", "on_accent": "#06210F", "danger": "#F0455B",
    "on_danger": "#FFFFFF", "btn": "#EDF0F4", "btn_hi": "#E1E6EC",
    "btn_disabled": "#F0F2F5", "border": "#E2E6EC",
    "green": "#1F9D3B", "amber": "#D9880F", "red": "#E23B3B",
    "keycap": "#E8ECF1", "keycap_fg": "#55606E", "scrub": "#E4E8EE",
}
THEMES = {"dark": DARK, "light": LIGHT}


# ── Colour helpers ────────────────────────────────────────────────────────────

def _hex(c):
    c = c.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def _rgb(t):
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(v))) for v in t)


def _lighten(c, amt):
    r, g, b = _hex(c)
    return _rgb((r + (255 - r) * amt, g + (255 - g) * amt, b + (255 - b) * amt))


# ── Rounded-rectangle drawing ─────────────────────────────────────────────────

def round_rect(canvas, x1, y1, x2, y2, r, **kw):
    r = min(r, (x2 - x1) / 2, (y2 - y1) / 2)
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return canvas.create_polygon(pts, smooth=True, **kw)


# ── Custom media button (Canvas-drawn: rounded pill or circle + keycap) ───────

class MediaButton(tk.Canvas):
    """A rounded button with an icon glyph, optional label, and the keyboard
    shortcut printed on a little keycap — the whole canvas is the click target."""

    def __init__(self, parent, pal, icon="", label="", key="", command=None,
                 kind="ghost", shape="pill", w=118, h=74, icon_size=22):
        self.pal = pal
        self.parent_bg = parent["bg"] if "bg" in parent.keys() else pal["bar"]
        super().__init__(parent, width=w, height=h, highlightthickness=0, bd=0,
                         bg=self.parent_bg)
        self.icon, self.label, self.key = icon, label, key
        self.command, self.kind, self.shape = command, kind, shape
        self.w, self.h, self.icon_size = w, h, icon_size
        self.enabled, self.hover = True, False
        self.bind("<Button-1>", self._click)
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        self.draw()

    def _face(self):
        p = self.pal
        if not self.enabled:
            return p["btn_disabled"], p["faint"]
        if self.kind == "primary":
            base, fg = p["accent"], p["on_accent"]
        elif self.kind == "danger":
            base, fg = p["danger"], p["on_danger"]
        else:
            base, fg = p["btn"], p["text"]
        if self.hover:
            base = _lighten(base, 0.10) if self.kind == "ghost" else _lighten(base, 0.06)
        return base, fg

    def draw(self):
        self.delete("all")
        self.configure(bg=self.parent_bg)
        bg, fg = self._face()
        pad = 2
        if self.shape == "circle":
            d = min(self.w, self.h - 22)
            cx = self.w / 2
            cy = d / 2 + 2
            self.create_oval(cx - d / 2, cy - d / 2, cx + d / 2, cy + d / 2,
                             fill=bg, outline="")
            self.create_text(cx + (1 if self.icon == "▶" else 0), cy, text=self.icon,
                             fill=fg, font=(UI_FONT, self.icon_size))
            if self.key:
                self._keycap(cx, self.h - 9, self.key)
            return
        # pill
        round_rect(self, pad, pad, self.w - pad, self.h - pad, 16, fill=bg, outline="")
        has_key = bool(self.key)
        top_y = self.h * (0.40 if has_key else 0.5)
        # icon + label on the top row, measured for centering
        uf = tkfont.Font(family=UI_FONT, size=self.icon_size)
        lf = tkfont.Font(family=UI_FONT, size=14, weight="bold")
        iw = uf.measure(self.icon) if self.icon else 0
        lw = lf.measure(self.label) if self.label else 0
        gap = 8 if (self.icon and self.label) else 0
        total = iw + gap + lw
        x = self.w / 2 - total / 2
        if self.icon:
            self.create_text(x + iw / 2, top_y, text=self.icon, fill=fg,
                             font=(UI_FONT, self.icon_size))
            x += iw + gap
        if self.label:
            self.create_text(x + lw / 2, top_y, text=self.label, fill=fg,
                             font=(UI_FONT, 14, "bold"))
        if has_key:
            self._keycap(self.w / 2, self.h * 0.74, self.key)

    def _keycap(self, cx, cy, text):
        f = tkfont.Font(family=MONO_FONT, size=11)
        w = max(f.measure(text) + 14, 22)
        h = 18
        round_rect(self, cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2, 5,
                   fill=self.pal["keycap"], outline="")
        self.create_text(cx, cy, text=text, fill=self.pal["keycap_fg"],
                         font=(MONO_FONT, 11))

    def _click(self, _e):
        if self.enabled and self.command:
            self.command()

    def _enter(self, _e):
        self.hover = True
        self.configure(cursor="hand2" if self.enabled else "arrow")
        self.draw()

    def _leave(self, _e):
        self.hover = False
        self.draw()

    def set_label(self, label=None, icon=None, kind=None):
        if label is not None:
            self.label = label
        if icon is not None:
            self.icon = icon
        if kind is not None:
            self.kind = kind
        self.draw()

    def set_enabled(self, on):
        self.enabled = bool(on)
        self.draw()

    def restyle(self, pal, parent_bg=None):
        self.pal = pal
        if parent_bg:
            self.parent_bg = parent_bg
        self.draw()


class ToggleChip(tk.Canvas):
    """A small rounded pill that lights up in the accent colour when on."""

    def __init__(self, parent, pal, text, var, command=None, w=176, h=34):
        self.pal = pal
        self.parent_bg = parent["bg"] if "bg" in parent.keys() else pal["bar"]
        super().__init__(parent, width=w, height=h, highlightthickness=0, bd=0,
                         bg=self.parent_bg)
        self.text, self.var, self.command = text, var, command
        self.w, self.h = w, h
        self.bind("<Button-1>", self._click)
        self.draw()

    def draw(self):
        self.delete("all")
        self.configure(bg=self.parent_bg)
        on = bool(self.var.get())
        p = self.pal
        bg = p["accent"] if on else p["btn"]
        fg = p["on_accent"] if on else p["dim"]
        round_rect(self, 1, 1, self.w - 1, self.h - 1, self.h / 2, fill=bg, outline="")
        dot = self.h - 12
        dx = self.w - dot / 2 - 6 if on else dot / 2 + 6
        self.create_oval(dx - dot / 2, self.h / 2 - dot / 2, dx + dot / 2,
                         self.h / 2 + dot / 2, fill=fg, outline="")
        anchor_x = 14 if on else dot + 12
        self.create_text(anchor_x, self.h / 2, text=self.text, anchor="w", fill=fg,
                         font=(UI_FONT, 12, "bold"))

    def _click(self, _e):
        self.var.set(not self.var.get())
        self.draw()
        if self.command:
            self.command()

    def restyle(self, pal, parent_bg=None):
        self.pal = pal
        if parent_bg:
            self.parent_bg = parent_bg
        self.draw()


# ── The app ───────────────────────────────────────────────────────────────────

class App:
    def __init__(self, root):
        self.root = root
        self.root.title("Mandarin Learning Reader")
        self.root.geometry("1240x820")
        self.root.minsize(1000, 640)
        self.q = queue.Queue()

        prog = E.load_progress()
        s = prog["settings"]
        self.settings = s
        self.theme_name = s.get("theme_v2", "dark")   # V2 keeps its own theme, dark by default
        if self.theme_name not in THEMES:
            self.theme_name = "dark"
        self.pal = THEMES[self.theme_name]

        self.book_path = None
        self.book_title = ""
        self.chapters = []
        self.chapter_idx = None
        self.units = []
        self.unit_idx = 0
        self.detected_script = "unknown"
        self.current_marks = None
        self.last_accuracy = None
        self.recorder = None
        self.recording = False
        self._rec_seq = 0

        self.voice_var = tk.StringVar(value=self._voice_label(s.get("voice")))
        self.speed_var = tk.StringVar(value=self._speed_label(s.get("speed", 1.0)))
        self.script_var = tk.StringVar(value=self._script_label(s.get("script_mode", "none")))
        self.autoadv_var = tk.BooleanVar(value=s.get("auto_advance", True))
        self.lenient_var = tk.BooleanVar(value=s.get("pinyin_tolerance", False))
        self.status_var = tk.StringVar(value="Open a Chinese e-book to begin")

        self.whisper = E.WhisperEngine(s.get("whisper_model", E.WHISPER_MODEL_DEFAULT))
        self._whisper_ready = False

        self._init_audio()
        self._build_ui()
        self._bind_keys()
        self.root.after(100, self._poll)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        last = prog.get("last_book")
        if last and Path(last).exists():
            self.root.after(250, lambda: self._open_book(last, resume=True))

    # ── audio ────────────────────────────────────────────────────────────────
    def _init_audio(self):
        self.have_mixer = False
        try:
            import pygame
            pygame.mixer.init()
            self.pygame = pygame
            self.have_mixer = True
        except Exception:
            self.pygame = None

    # ── label helpers ────────────────────────────────────────────────────────
    def _voice_label(self, vid):
        for label, v in E.VOICES.items():
            if v == vid:
                return label
        return next(iter(E.VOICES))

    def _script_label(self, mode):
        return {"none": "Original", "s2t": "→ Traditional",
                "t2s": "→ Simplified"}.get(mode, "Original")

    def _script_mode(self):
        return {"Original": "none", "→ Traditional": "s2t",
                "→ Simplified": "t2s"}.get(self.script_var.get(), "none")

    def _voice_id(self):
        return E.VOICES.get(self.voice_var.get(), E.DEFAULT_VOICE)

    def _speed_label(self, sp):
        return f"{float(sp):g}×"

    def _speed(self):
        raw = self.speed_var.get().rstrip("×x").strip()
        try:
            val = float(raw)
        except ValueError:
            return 1.0
        return min(E.SPEEDS, key=lambda s: abs(s - val))

    # ── UI construction ──────────────────────────────────────────────────────
    def _build_ui(self):
        p = self.pal
        self.root.configure(bg=p["bg"])
        self.style = ttk.Style()
        try:
            self.style.theme_use("clam")
        except tk.TclError:
            pass

        # Header ---------------------------------------------------------------
        self.header = tk.Frame(self.root, bg=p["bar"], height=64)
        self.header.pack(side="top", fill="x")
        self.header.pack_propagate(False)
        self.open_btn = MediaButton(self.header, p, icon="📖", label="Open",
                                    key="", command=self._choose_book, kind="ghost",
                                    w=132, h=44, icon_size=16)
        self.open_btn.pack(side="left", padx=(16, 12), pady=10)
        self.title_lbl = tk.Label(self.header, text="No book open", bg=p["bar"],
                                  fg=p["text"], font=(CJK_FONT, 17, "bold"))
        self.title_lbl.pack(side="left", pady=10)
        self.theme_btn = MediaButton(self.header, p, icon="🌙", key="",
                                     command=self._toggle_theme, kind="ghost",
                                     w=52, h=44, icon_size=16)
        self.theme_btn.pack(side="right", padx=(6, 16), pady=10)
        self.script_info = tk.Label(self.header, text="", bg=p["bar"], fg=p["dim"],
                                    font=(UI_FONT, 12))
        self.script_info.pack(side="right", padx=10)

        # Body: sidebar | stage ------------------------------------------------
        body = tk.Frame(self.root, bg=p["bg"])
        body.pack(side="top", fill="both", expand=True)

        self.sidebar = tk.Frame(body, bg=p["bar"], width=270)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)
        tk.Label(self.sidebar, text="CHAPTERS", bg=p["bar"], fg=p["dim"],
                 font=(UI_FONT, 11, "bold")).pack(side="top", anchor="w",
                                                  padx=20, pady=(18, 8))
        listwrap = tk.Frame(self.sidebar, bg=p["bar"])
        listwrap.pack(side="top", fill="both", expand=True, padx=(10, 8))
        self.chapter_list = tk.Listbox(
            listwrap, activestyle="none", exportselection=False, highlightthickness=0,
            borderwidth=0, font=(CJK_FONT, 14), bg=p["bar"], fg=p["text"],
            selectbackground=p["btn_hi"], selectforeground=p["text"], relief="flat")
        self.chapter_list.pack(side="left", fill="both", expand=True)
        self.chapter_list.bind("<<ListboxSelect>>", self._on_chapter_pick)
        # Don't let the listbox's built-in Space/Enter ("select active row") hijack
        # the transport shortcuts and snap back to the chapter's first sentence.
        self.chapter_list.bind("<space>", lambda e: (self.on_play(), "break")[1])
        self.chapter_list.bind("<Return>", lambda e: (self.on_play(), "break")[1])

        # Stage (karaoke) ------------------------------------------------------
        self.stage = tk.Canvas(body, bg=p["bg"], highlightthickness=0, bd=0)
        self.stage.pack(side="left", fill="both", expand=True)
        self.stage.bind("<Configure>", lambda e: self._render_stage())

        # Now-playing bar ------------------------------------------------------
        self._build_transport()

    def _build_transport(self):
        p = self.pal
        self.bar = tk.Frame(self.root, bg=p["bar"], height=204)
        self.bar.pack(side="bottom", fill="x")
        self.bar.pack_propagate(False)

        # Row A: position + status (left)  ·  toggle chips + score (right)
        top = tk.Frame(self.bar, bg=p["bar"])
        top.pack(side="top", fill="x", padx=24, pady=(12, 2))
        self.pos_lbl = tk.Label(top, text="—", bg=p["bar"], fg=p["dim"],
                                font=(UI_FONT, 12))
        self.pos_lbl.pack(side="left")
        self.status_lbl = tk.Label(top, textvariable=self.status_var, bg=p["bar"],
                                   fg=p["dim"], font=(UI_FONT, 12))
        self.status_lbl.pack(side="left", padx=16)
        self.score_lbl = tk.Label(top, text="", bg=p["bar"], fg=p["text"],
                                  font=(UI_FONT, 18, "bold"))
        self.score_lbl.pack(side="right", padx=(14, 0))
        self.lenient_chip = ToggleChip(top, p, "Lenient", self.lenient_var,
                                       command=self._save_settings, w=104, h=30)
        self.lenient_chip.pack(side="right", padx=6)
        self.autoadv_chip = ToggleChip(top, p, "Auto-advance ≥90%", self.autoadv_var,
                                       command=self._save_settings, w=186, h=30)
        self.autoadv_chip.pack(side="right", padx=6)

        # Scrubber
        self.scrub = tk.Canvas(self.bar, height=6, bg=p["bar"], highlightthickness=0, bd=0)
        self.scrub.pack(side="top", fill="x", padx=24, pady=(4, 8))
        self.scrub.bind("<Configure>", lambda e: self._draw_scrub())
        self.scrub.bind("<Button-1>", self._scrub_click)

        # Row B: transport (centre) + pickers (right)
        row = tk.Frame(self.bar, bg=p["bar"])
        row.pack(side="top", fill="both", expand=True, padx=24, pady=(0, 10))

        settings = tk.Frame(row, bg=p["bar"])
        settings.pack(side="right", anchor="s", pady=(0, 6))
        self._build_pickers(settings)

        transport = tk.Frame(row, bg=p["bar"])
        transport.pack(side="top", expand=True)
        self.prev_btn = MediaButton(transport, p, icon="⏮", label="Prev", key="←",
                                    command=self.on_prev, kind="ghost", w=104)
        self.prev_btn.pack(side="left", padx=6)
        self.rec_btn = MediaButton(transport, p, icon="●", label="Record", key="R",
                                   command=self.on_record, kind="danger", w=122)
        self.rec_btn.pack(side="left", padx=6)
        self.play_btn = MediaButton(transport, p, icon="▶", key="Space",
                                    command=self.on_play, kind="primary",
                                    shape="circle", w=96, h=98, icon_size=30)
        self.play_btn.pack(side="left", padx=12)
        self.replay_btn = MediaButton(transport, p, icon="↻", label="Replay",
                                      key="↵", command=self.on_play, kind="ghost", w=112)
        self.replay_btn.pack(side="left", padx=6)
        self.next_btn = MediaButton(transport, p, icon="⏭", label="Next", key="→",
                                    command=self.on_next, kind="ghost", w=104)
        self.next_btn.pack(side="left", padx=6)
        self.stop_btn = MediaButton(transport, p, icon="⏹", label="Stop", key="Esc",
                                    command=self.on_stop, kind="ghost", w=104)
        self.stop_btn.pack(side="left", padx=6)

        if not (E.HAVE_SOUNDDEVICE and E.HAVE_WHISPER):
            self.rec_btn.set_enabled(False)
            miss = []
            if not E.HAVE_SOUNDDEVICE:
                miss.append("microphone support")
            if not E.HAVE_WHISPER:
                miss.append("speech recognition")
            self.status_var.set("Recording unavailable — missing " + ", ".join(miss))

    def _build_pickers(self, parent):
        p = self.pal
        self.style.configure("V2.TMenubutton", background=p["btn"], foreground=p["text"],
                             font=(UI_FONT, 12), relief="flat", padding=6,
                             arrowcolor=p["dim"])
        self.style.map("V2.TMenubutton", background=[("active", p["btn_hi"])])

        grid = tk.Frame(parent, bg=p["bar"])
        grid.pack(side="top", anchor="e")
        self._picker(grid, "Voice", self.voice_var, list(E.VOICES.keys()),
                     self._save_settings, 0, wide=True)
        self._picker(grid, "Speed", self.speed_var,
                     [self._speed_label(s) for s in E.SPEEDS], self._save_settings, 1)
        self._picker(grid, "Script", self.script_var,
                     ["Original", "→ Traditional", "→ Simplified"],
                     self._on_script_change, 2)

    def _picker(self, grid, label, var, values, cb, col, wide=False):
        p = self.pal
        tk.Label(grid, text=label, bg=p["bar"], fg=p["dim"],
                 font=(UI_FONT, 11)).grid(row=0, column=col, padx=(12, 4), sticky="e")
        om = ttk.OptionMenu(grid, var, var.get(), *values, command=lambda *_: cb())
        om.configure(style="V2.TMenubutton", width=20 if wide else 8)
        om.grid(row=1, column=col, padx=(12, 4), sticky="e")
        try:
            om["menu"].configure(bg=p["panel"], fg=p["text"],
                                 activebackground=p["btn_hi"], activeforeground=p["text"],
                                 relief="flat", font=(UI_FONT, 12))
        except tk.TclError:
            pass

    # ── theme ────────────────────────────────────────────────────────────────
    def _toggle_theme(self):
        self.theme_name = "light" if self.theme_name == "dark" else "dark"
        self.pal = THEMES[self.theme_name]
        self.settings["theme_v2"] = self.theme_name
        self._restyle()
        self._save_settings()

    def _restyle(self):
        p = self.pal
        self.root.configure(bg=p["bg"])
        for w in (self.header, self.sidebar, self.bar):
            w.configure(bg=p["bar"])
        self.stage.configure(bg=p["bg"])
        self.scrub.configure(bg=p["bar"])
        self.title_lbl.configure(bg=p["bar"], fg=p["text"])
        self.script_info.configure(bg=p["bar"], fg=p["dim"])
        self.chapter_list.configure(bg=p["bar"], fg=p["text"], selectbackground=p["btn_hi"],
                                    selectforeground=p["text"])
        self.pos_lbl.configure(bg=p["bar"], fg=p["dim"])
        self.status_lbl.configure(bg=p["bar"], fg=p["dim"])
        self.score_lbl.configure(bg=p["bar"])
        # recolour every intermediate frame + label inside header/sidebar/bar,
        # otherwise container frames keep the old theme's colour (a stray band).
        for parent in (self.header, self.sidebar, self.bar):
            for ch in self._all_children(parent):
                if isinstance(ch, (tk.Frame, tk.Label)):
                    ch.configure(bg=p["bar"])
        self.theme_btn.set_label(icon="☀️" if self.theme_name == "dark" else "🌙")
        for b in (self.open_btn, self.theme_btn, self.prev_btn, self.rec_btn,
                  self.play_btn, self.replay_btn, self.next_btn, self.stop_btn):
            b.restyle(p, parent_bg=p["bar"])
        self.autoadv_chip.restyle(p, parent_bg=p["bar"])
        self.lenient_chip.restyle(p, parent_bg=p["bar"])
        self._build_pickers  # style already reconfigured on next open; restyle menus:
        self.style.configure("V2.TMenubutton", background=p["btn"], foreground=p["text"],
                             arrowcolor=p["dim"])
        self.style.map("V2.TMenubutton", background=[("active", p["btn_hi"])])
        self._draw_scrub()
        self._render_stage()
        self._update_score()

    def _all_children(self, w):
        out = []
        for c in w.winfo_children():
            out.append(c)
            out.extend(self._all_children(c))
        return out

    # ── keyboard ─────────────────────────────────────────────────────────────
    def _bind_keys(self):
        b = self.root.bind
        b("<space>", lambda e: self._key(e, self.on_play))
        b("r", lambda e: self._key(e, self.on_record))
        b("R", lambda e: self._key(e, self.on_record))
        b("<Right>", lambda e: self._key(e, self.on_next))
        b("<Left>", lambda e: self._key(e, self.on_prev))
        b("<Return>", lambda e: self._key(e, self.on_play))
        b("<Escape>", lambda e: self._key(e, self.on_stop))

    def _key(self, event, fn):
        if isinstance(self.root.focus_get(), (tk.Entry, ttk.Entry)):
            return
        fn()
        return "break"

    # ── book opening ─────────────────────────────────────────────────────────
    def _choose_book(self):
        path = filedialog.askopenfilename(
            title="Open a Chinese EPUB",
            filetypes=[("EPUB files", "*.epub"), ("All files", "*.*")])
        if path:
            self._open_book(path)

    def _open_book(self, path, resume=False):
        self.status_var.set("Loading book…")
        threading.Thread(target=self._load_worker, args=(path, resume),
                         daemon=True).start()

    def _load_worker(self, path, resume):
        try:
            book, title, author = E.load_book(path)
            chapters = E.gather_chapters(book)
            sample = "".join(c["text"] for c in chapters if not c["junk"])[:5000]
            script = E.detect_script(sample)
            self.q.put(("book", (path, title, chapters, script, resume)))
        except Exception as exc:
            self.q.put(("error", f"Could not open book:\n{exc}"))

    def _on_book(self, payload):
        path, title, chapters, script, resume = payload
        self.book_path, self.book_title = path, title
        self.chapters, self.detected_script = chapters, script
        self.title_lbl.config(text=title)
        self.script_info.config(text=f"Detected: {script}")
        self.chapter_list.delete(0, tk.END)
        for c in chapters:
            flag = "  ⚠" if c["junk"] else ""
            self.chapter_list.insert(tk.END, f" {c['index']:>2}   {c['title'][:24]}{flag}")
        start_ch, start_unit = 0, 0
        if resume:
            pos = E.get_position(path)
            for i, c in enumerate(chapters):
                if c["index"] == pos["chapter_index"]:
                    start_ch, start_unit = i, pos["sentence_index"]
                    break
        else:
            for i, c in enumerate(chapters):
                if not c["junk"]:
                    start_ch = i
                    break
        self._load_chapter(start_ch, start_unit)
        self.status_var.set("Ready · Space to listen · R to speak")

    # ── navigation ───────────────────────────────────────────────────────────
    def _on_chapter_pick(self, _e):
        sel = self.chapter_list.curselection()
        # Only load when a *different* chapter is chosen, so a stray re-selection
        # of the current chapter can't reset you to its first sentence.
        if sel and sel[0] != self.chapter_idx:
            self._load_chapter(sel[0], 0)

    def _load_chapter(self, ch_index, unit_index=0):
        if not self.chapters:
            return
        ch_index = max(0, min(ch_index, len(self.chapters) - 1))
        self.chapter_idx = ch_index
        text = E.convert_script(self.chapters[ch_index]["text"], self._script_mode())
        self.units = E.chunk_text_for_learning(text)
        self.unit_idx = max(0, min(unit_index, len(self.units) - 1)) if self.units else 0
        self.chapter_list.selection_clear(0, tk.END)
        self.chapter_list.selection_set(ch_index)
        self.chapter_list.see(ch_index)
        self._goto_unit(reset=True)

    def _goto_unit(self, reset=True):
        if reset:
            self.current_marks = None
            self.last_accuracy = None
            self.status_var.set("Ready · Space to listen · R to speak")
        self._render_stage()
        self._update_pos()
        self._draw_scrub()
        self._update_score()
        self._save_position()
        self._prefetch()

    def _update_pos(self):
        if not self.units:
            self.pos_lbl.config(text="—")
            return
        ch = self.chapters[self.chapter_idx]["index"]
        self.pos_lbl.config(text=f"Chapter {ch}   ·   {self.unit_idx + 1} / {len(self.units)}")

    # ── karaoke stage rendering ──────────────────────────────────────────────
    def _render_stage(self):
        c = self.stage
        c.delete("all")
        p = self.pal
        W = c.winfo_width() or 900
        H = c.winfo_height() or 500
        if not self.units:
            c.create_text(W / 2, H / 2, text="Open a Chinese e-book to begin",
                          fill=p["dim"], font=(UI_FONT, 22))
            return

        i = self.unit_idx
        cells = E.annotate(self.units[i]["text"])
        marks = self.current_marks

        # measure + wrap the current sentence into lines of char cells
        cf = tkfont.Font(family=CJK_FONT, size=60)
        pf = tkfont.Font(family=CJK_FONT, size=24)
        margin = 90
        avail = max(200, W - margin * 2)
        pad = 10
        lines, line, lw = [], [], 0
        for idx, cell in enumerate(cells):
            cw = max(cf.measure(cell["char"]), pf.measure(cell["py"])) + pad * 2
            cw = max(cw, 30)
            if lw + cw > avail and line:
                lines.append(line)
                line, lw = [], 0
            line.append((idx, cell, cw))
            lw += cw
        if line:
            lines.append(line)

        line_h = 108
        cur_h = len(lines) * line_h
        prev_txt = self.units[i - 1]["text"] if i > 0 else ""
        next_txt = self.units[i + 1]["text"] if i + 1 < len(self.units) else ""
        next2_txt = self.units[i + 2]["text"] if i + 2 < len(self.units) else ""

        gap = 46
        prev_h = 40 if prev_txt else 0
        next_h = (40 if next_txt else 0) + (34 if next2_txt else 0)
        total = prev_h + (gap if prev_txt else 0) + cur_h + (gap if next_txt else 0) + next_h
        y = max(40, (H - total) / 2)

        if prev_txt:
            c.create_text(W / 2, y + 20, text=prev_txt, fill=p["dim"],
                          font=(CJK_FONT, 26), width=avail, justify="center")
            y += prev_h + gap

        # highlight band behind the current sentence
        band_pad = 26
        round_rect(c, margin - 34, y - band_pad, W - margin + 34, y + cur_h + band_pad - 20,
                   24, fill=p["stage_hi"], outline="")

        def colour(idx, cell):
            if not cell["py"] or marks is None:
                return p["text"]
            cj = sum(1 for cc in cells[:idx] if cc["py"])
            if cj < len(marks):
                m = marks[cj]
                return p["green"] if m["ok"] else (p["amber"] if m.get("sound") else p["red"])
            return p["text"]

        for li, ln in enumerate(lines):
            tot = sum(w for _, _, w in ln)
            x = (W - tot) / 2
            ytop = y + li * line_h
            for idx, cell, cw in ln:
                cx = x + cw / 2
                if cell["py"]:
                    c.create_text(cx, ytop + 20, text=cell["py"], fill=p["pinyin"],
                                  font=(CJK_FONT, 24))
                c.create_text(cx, ytop + 68, text=cell["char"], fill=colour(idx, cell),
                              font=(CJK_FONT, 60))
                x += cw
        y += cur_h + (gap if next_txt else 0)

        if next_txt:
            c.create_text(W / 2, y + 18, text=next_txt, fill=p["faint"],
                          font=(CJK_FONT, 24), width=avail, justify="center")
            y += 40
        if next2_txt:
            c.create_text(W / 2, y + 15, text=next2_txt, fill=p["faint"],
                          font=(CJK_FONT, 20), width=avail, justify="center")

    def _draw_scrub(self):
        c = self.scrub
        c.delete("all")
        p = self.pal
        W = c.winfo_width() or 400
        h = 6
        round_rect(c, 0, 1, W, h - 1, 3, fill=p["scrub"], outline="")
        if self.units:
            frac = (self.unit_idx + 1) / len(self.units)
            round_rect(c, 0, 1, max(6, W * frac), h - 1, 3, fill=p["accent"], outline="")

    def _scrub_click(self, e):
        if not self.units:
            return
        W = self.scrub.winfo_width() or 1
        frac = min(1.0, max(0.0, e.x / W))
        self.unit_idx = min(len(self.units) - 1, int(frac * len(self.units)))
        self._goto_unit(reset=True)

    def _update_score(self):
        p = self.pal
        if self.last_accuracy is None:
            self.score_lbl.config(text="")
            return
        pct = self.last_accuracy * 100
        col = p["green"] if pct >= 90 else (p["amber"] if pct >= 70 else p["red"])
        self.score_lbl.config(text=f"{pct:.0f}%", fg=col)

    # ── transport actions ────────────────────────────────────────────────────
    def on_play(self):
        if not self.units:
            return
        text = self.units[self.unit_idx]["text"]
        voice, speed = self._voice_id(), self._speed()
        self.status_var.set("Playing…" if E.is_synth_cached(text, voice, speed)
                            else "Fetching audio…")
        threading.Thread(target=self._play_worker, args=(text, voice, speed),
                         daemon=True).start()

    def _play_worker(self, text, voice, speed):
        try:
            path = E.synth_sentence(text, voice, speed)
            self.q.put(("play", path))
        except Exception as exc:
            self.q.put(("error", f"Could not fetch audio:\n{exc}"))

    def _do_play(self, path):
        if not self.have_mixer:
            self.status_var.set("Audio playback unavailable")
            return
        try:
            self.pygame.mixer.music.load(path)
            self.pygame.mixer.music.play()
            self.status_var.set("Playing…")
        except Exception as exc:
            self.status_var.set(f"Playback error: {exc}")

    def on_record(self):
        if not (E.HAVE_SOUNDDEVICE and E.HAVE_WHISPER) or not self.units:
            return
        if self.recording:
            self._stop_recording()
            return
        try:
            self.recorder = E.Recorder(max_seconds=30)
            self.recorder.start()
        except Exception as exc:
            self.status_var.set(f"Microphone error: {exc}")
            return
        self.recording = True
        self.rec_btn.set_label(icon="■", label="Stop")
        self.status_var.set("Listening… press R again when done")

    def _stop_recording(self):
        self.recording = False
        self.rec_btn.set_label(icon="●", label="Record")
        self.status_var.set("Checking your pronunciation…")
        self._rec_seq += 1
        wav = os.path.join(tempfile.gettempdir(),
                           f"mandarin_v2_{os.getpid()}_{self._rec_seq}.wav")
        try:
            self.recorder.stop(wav)
        except Exception as exc:
            self.status_var.set(f"Recording error: {exc}")
            return
        text = self.units[self.unit_idx]["text"]
        threading.Thread(target=self._grade_worker, args=(wav, text), daemon=True).start()

    def _grade_worker(self, wav, original):
        try:
            if not self._whisper_ready:
                self.q.put(("status", "Loading speech model (one-time download)…"))
                self.whisper.load()
                self._whisper_ready = True
            heard = self.whisper.transcribe(wav)
            res = E.grade(original, heard,
                          pinyin_tolerance=bool(self.lenient_var.get()))
            self.q.put(("graded", res))
        except Exception as exc:
            self.q.put(("error", f"Could not check pronunciation:\n{exc}"))
        finally:
            try:
                os.remove(wav)
            except OSError:
                pass

    def _on_graded(self, res):
        self.current_marks = res["marks"]
        self.last_accuracy = res["accuracy"]
        self._render_stage()
        self._update_score()
        pct = res["accuracy"] * 100
        if res["auto_advance"] and self.autoadv_var.get():
            self.status_var.set(f"✓ {pct:.0f}% — nice! Advancing…")
            self.root.after(950, self.on_next)
        else:
            self.status_var.set(f"{pct:.0f}% — Replay, Record again, or Next")

    def on_next(self):
        if not self.units:
            return
        if self.unit_idx + 1 < len(self.units):
            self.unit_idx += 1
            self._goto_unit(reset=True)
        elif self.chapter_idx is not None and self.chapter_idx + 1 < len(self.chapters):
            self._load_chapter(self.chapter_idx + 1, 0)

    def on_prev(self):
        if not self.units:
            return
        if self.unit_idx > 0:
            self.unit_idx -= 1
            self._goto_unit(reset=True)
        elif self.chapter_idx is not None and self.chapter_idx > 0:
            self._load_chapter(self.chapter_idx - 1, 0)
            self.unit_idx = len(self.units) - 1
            self._goto_unit(reset=True)

    def on_stop(self):
        if self.have_mixer:
            try:
                self.pygame.mixer.music.stop()
            except Exception:
                pass
        if self.recording:
            self.recording = False
            self.rec_btn.set_label(icon="●", label="Record")
            try:
                self.recorder.stop(os.path.join(tempfile.gettempdir(), "_discard.wav"))
            except Exception:
                pass
        self.status_var.set("Stopped")

    # ── settings / prefetch / persistence ────────────────────────────────────
    def _on_script_change(self):
        self._save_settings()
        if self.chapter_idx is not None:
            self._load_chapter(self.chapter_idx, self.unit_idx)

    def _prefetch(self):
        voice, speed = self._voice_id(), self._speed()
        texts = [u["text"] for u in self.units[self.unit_idx:self.unit_idx + 4]]
        threading.Thread(target=self._prefetch_worker, args=(texts, voice, speed),
                         daemon=True).start()

    def _prefetch_worker(self, texts, voice, speed):
        for t in texts:
            try:
                if not E.is_synth_cached(t, voice, speed):
                    E.synth_sentence(t, voice, speed)
            except Exception:
                pass

    def _save_settings(self):
        self.settings.update({
            "voice": self._voice_id(), "speed": self._speed(),
            "script_mode": self._script_mode(), "theme_v2": self.theme_name,
            "auto_advance": bool(self.autoadv_var.get()),
            "pinyin_tolerance": bool(self.lenient_var.get()),
        })
        if self.book_path and self.chapter_idx is not None:
            self._save_position()
        else:
            data = E.load_progress()
            data["settings"].update(self.settings)
            E.save_progress(data)

    def _save_position(self):
        if not self.book_path or self.chapter_idx is None:
            return
        try:
            E.record_position(self.book_path, self.book_title,
                              self.chapters[self.chapter_idx]["index"],
                              self.unit_idx, settings=self.settings)
        except Exception:
            pass

    # ── poll / close ─────────────────────────────────────────────────────────
    def _poll(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "book":
                    self._on_book(payload)
                elif kind == "play":
                    self._do_play(payload)
                elif kind == "graded":
                    self._on_graded(payload)
                elif kind == "status":
                    self.status_var.set(payload)
                elif kind == "error":
                    self.status_var.set("Error")
                    messagebox.showerror("Mandarin Learning Reader", payload)
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def _on_close(self):
        self._save_position()
        try:
            if self.have_mixer:
                self.pygame.mixer.music.stop()
        except Exception:
            pass
        self.root.destroy()


def main():
    missing = E.missing_deps()
    root = tk.Tk()
    if missing:
        messagebox.showerror("Missing dependencies",
                             "Install the core packages first:\n\n  pip install "
                             + " ".join(missing))
        root.destroy()
        return
    try:
        App(root)
        root.mainloop()
    except Exception:
        traceback.print_exc()


if __name__ == "__main__":
    main()
